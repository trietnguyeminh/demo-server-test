from __future__ import annotations

import re
import unicodedata

from .contracts import (
    ROUTER_BUILD_VERSION,
    ROUTER_CONTRACT_VERSION,
)

from .models import (
    ModalityDecision,
    ModalityMode,
    RouteDecision,
    RouteRequest,
    SearchProfile,
)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    value = re.sub(r"[^\wÀ-ỹ-]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


class QueryRouter:
    """Low-latency local router.

    Scores are routing heuristics, not calibrated probabilities. Auto profile
    runs OCR in parallel because the pre-built SQLite index is cheap; weak OCR
    evidence is later restricted to visual-candidate enrichment by the service.
    """

    OCR_TERMS = {
        "chữ", "có chữ", "dòng chữ", "dòng", "ghi", "ghi trên",
        "in trên", "được in", "nội dung", "khẩu hiệu", "slogan",
        "bảng hiệu", "biển hiệu", "biển báo", "biển số", "logo",
        "thương hiệu", "nhãn", "tên cửa hàng", "số điện thoại",
        "ngày tháng", "giá tiền", "phụ đề", "tiêu đề", "màn hình",
        "text", "sign", "signboard", "license plate", "brand", "subtitle",
    }
    ASR_TERMS = {
        "nói", "phát biểu", "trả lời", "hỏi", "đọc", "hát", "hô",
        "nhắc đến", "âm thanh", "lời thoại", "cuộc trò chuyện", "giọng nói",
        "nghe thấy", "speech", "says", "speaking", "mentions", "singing",
    }
    COMPLEX_TERMS = {
        "trước khi", "sau khi", "sau đó", "đồng thời", "vừa", "rồi",
        "before", "after", "then", "while", "near", "bên cạnh",
    }
    OCR_MARKERS = {
        "hiệu", "logo", "nhãn", "brand", "chữ", "biển", "tên", "số",
        "ghi", "in", "slogan", "khẩu hiệu",
    }
    TEXT_SURFACES = {
        "túi", "áo", "mũ", "bảng", "biển", "poster", "banner", "chai",
        "hộp", "bao", "bao bì", "sách", "giấy", "tờ rơi", "màn hình",
        "điện thoại", "laptop", "xe", "cốc", "ly",
    }
    OBJECT_MODIFIERS = {
        "vải", "giấy", "nhựa", "da", "đeo", "xách", "phông", "thun",
        "bóng", "nước", "quà", "hàng",
    }
    NON_TEXT_TAIL_STARTS = {
        "màu", "đỏ", "xanh", "trắng", "đen", "vàng", "hồng", "to",
        "nhỏ", "lớn", "đang", "được",
    }
    VISUAL_ACTION_TERMS = {
        "đi", "đứng", "ngồi", "chạy", "cầm", "mang", "xách", "đeo",
        "đặt", "nằm", "treo", "bên cạnh", "ngoài đường", "trong phòng",
        "trên bàn", "với người", "của người",
    }

    PROFILE_THRESHOLDS = {
        SearchProfile.fast: {"ocr": 0.85, "asr": 0.88, "api": 0.98},
        SearchProfile.auto: {"ocr": 0.65, "asr": 0.70, "api": 0.82},
        SearchProfile.accurate: {"ocr": 0.25, "asr": 0.30, "api": 0.55},
    }

    @staticmethod
    def _contains_term(normalized: str, term: str) -> bool:
        """Match a full word/phrase, never a substring inside another word."""
        normalized_term = normalize_text(term)
        if not normalized_term:
            return False
        pattern = rf"(?<!\w){re.escape(normalized_term)}(?!\w)"
        return re.search(pattern, normalized, flags=re.UNICODE) is not None

    def _contains_any(self, normalized: str, terms: set[str]) -> list[str]:
        return sorted(
            term for term in terms if self._contains_term(normalized, term)
        )

    def _infer_text_phrase(self, query: str) -> tuple[str | None, str | None]:
        """Infer `visual object + printed phrase` for compact KIS prompts.

        Example:
            túi vải liên kết cùng phát triển
            -> visual: túi vải
            -> OCR anchor: liên kết cùng phát triển
        """
        normalized = normalize_text(query)
        tokens = normalized.split()
        if len(tokens) < 4:
            return None, None

        # Explicit OCR cue: keep everything after the cue as the text anchor.
        for cue in ("có chữ", "dòng chữ", "ghi trên", "in trên", "khẩu hiệu", "slogan"):
            match = re.search(
                rf"(?<!\w){re.escape(cue)}(?!\w)", normalized, flags=re.UNICODE
            )
            if match:
                prefix = normalized[: match.start()].strip()
                tail = normalized[match.end() :].strip()
                if len(tail.split()) >= 2:
                    return tail, prefix or None

        # Compact query beginning with a text-bearing object.
        first = tokens[0]
        if first not in self.TEXT_SURFACES:
            return None, None

        prefix_len = 2 if len(tokens) > 1 and tokens[1] in self.OBJECT_MODIFIERS else 1
        tail_tokens = tokens[prefix_len:]
        if len(tail_tokens) < 3 or tail_tokens[0] in self.NON_TEXT_TAIL_STARTS:
            return None, None

        tail = " ".join(tail_tokens)
        if self._contains_any(tail, self.VISUAL_ACTION_TERMS):
            return None, None

        return tail, " ".join(tokens[:prefix_len])

    def _extract_ocr_anchors(
        self,
        query: str,
        inferred_phrase: str | None = None,
    ) -> list[str]:
        anchors: list[str] = []
        for quoted in re.findall(r'["“”\']([^"“”\']{2,80})["“”\']', query):
            clean = normalize_text(quoted)
            if clean:
                anchors.append(clean)

        tokens = query.split()
        for index, token in enumerate(tokens[:-1]):
            if normalize_text(token) in self.OCR_MARKERS:
                candidate = normalize_text(tokens[index + 1])
                if candidate:
                    anchors.append(candidate)

        for index, token in enumerate(tokens):
            clean = re.sub(r"[^A-Za-z0-9_-]", "", token)
            is_internal_titlecase = (
                index > 0
                and len(clean) >= 3
                and clean[:1].isupper()
                and clean[1:].islower()
            )
            if len(clean) >= 3 and (
                clean.isupper()
                or is_internal_titlecase
                or any(char.isdigit() for char in clean)
                or "-" in clean
            ):
                anchors.append(normalize_text(clean))

        if inferred_phrase:
            anchors.append(normalize_text(inferred_phrase))

        return sorted(set(filter(None, anchors)), key=lambda item: (-len(item), item))

    @staticmethod
    def _threshold_enabled(
        mode: ModalityMode,
        score: float,
        threshold: float,
    ) -> bool:
        if mode == ModalityMode.on:
            return True
        if mode == ModalityMode.off:
            return False
        return score >= threshold

    @staticmethod
    def _execution_state(
        mode: ModalityMode,
        enabled: bool,
        *,
        auto_parallel: bool = False,
        always_on: bool = False,
    ) -> str:
        if always_on:
            return "always_on"
        if mode == ModalityMode.on:
            return "forced_on"
        if mode == ModalityMode.off:
            return "forced_off"
        if auto_parallel:
            return "auto_parallel"
        return "auto_on" if enabled else "auto_off"

    def route(self, request: RouteRequest) -> RouteDecision:
        query = request.query.strip()
        normalized = normalize_text(query)
        thresholds = self.PROFILE_THRESHOLDS[request.profile]

        ocr_matches = self._contains_any(normalized, self.OCR_TERMS)
        asr_matches = self._contains_any(normalized, self.ASR_TERMS)
        complex_matches = self._contains_any(normalized, self.COMPLEX_TERMS)
        inferred_phrase, inferred_visual = self._infer_text_phrase(query)
        anchors = self._extract_ocr_anchors(query, inferred_phrase)

        ocr_score = min(
            1.0,
            0.05
            + 0.18 * len(ocr_matches)
            + (0.70 if anchors else 0.0)
            + (0.10 if inferred_phrase else 0.0),
        )
        asr_score = min(1.0, 0.05 + 0.70 * len(asr_matches))
        complexity = min(
            1.0,
            0.08
            + 0.18 * len(complex_matches)
            + 0.08 * max(0, len(normalized.split()) - 12)
            + (0.18 if ocr_score > 0.6 and asr_score > 0.6 else 0.0),
        )

        # Auto/Accurate query the cheap pre-built OCR index in parallel. When the
        # routing score is weak, service.py only lets OCR enrich visual candidates.
        ocr_auto_parallel = (
            request.ocr == ModalityMode.auto
            and request.profile in {SearchProfile.auto, SearchProfile.accurate}
        )
        ocr_enabled = (
            True
            if ocr_auto_parallel
            else self._threshold_enabled(request.ocr, ocr_score, thresholds["ocr"])
        )
        asr_enabled = self._threshold_enabled(
            request.asr, asr_score, thresholds["asr"]
        )
        api_enabled = self._threshold_enabled(
            request.api_planner, complexity, thresholds["api"]
        )

        visual_query = inferred_visual or query
        if not inferred_visual:
            for anchor in anchors:
                visual_query = re.sub(
                    rf"(?i)(?<!\w){re.escape(anchor)}(?!\w)", " ", visual_query
                )
            visual_query = re.sub(r"\s+", " ", visual_query).strip() or query

        notes: list[str] = ["visual_always_on", "local_router"]
        if request.profile == SearchProfile.fast:
            notes.append("high_modality_thresholds")
        if ocr_auto_parallel:
            notes.append("ocr_parallel_policy")
        if anchors:
            notes.append("ocr_anchor_detected")
        if inferred_phrase:
            notes.append("printed_phrase_inferred")
        if complexity >= 0.55:
            notes.append("complex_query")

        ocr_reason_parts = [
            f"tín hiệu: {', '.join(ocr_matches) or 'không có'}",
            f"anchor: {', '.join(anchors) or 'không có'}",
        ]
        if inferred_phrase:
            ocr_reason_parts.append("suy luận cụm chữ in trên vật thể")
        elif ocr_auto_parallel and ocr_score < thresholds["ocr"]:
            ocr_reason_parts.append("Auto chạy OCR nhẹ song song")

        return RouteDecision(
            query=query,
            visual_query=visual_query,
            ocr_query=" ".join(anchors) if anchors else query,
            asr_query=query,
            visual=ModalityDecision(
                enabled=True,
                confidence=1.0,
                routing_score=1.0,
                mode=ModalityMode.on,
                execution_state="always_on",
                reason="Visual là fast path mặc định.",
                anchors=[],
            ),
            ocr=ModalityDecision(
                enabled=ocr_enabled,
                confidence=round(ocr_score, 4),
                routing_score=round(ocr_score, 4),
                mode=request.ocr,
                execution_state=self._execution_state(
                    request.ocr,
                    ocr_enabled,
                    auto_parallel=ocr_auto_parallel,
                ),
                reason="OCR " + "; ".join(ocr_reason_parts),
                anchors=anchors,
            ),
            asr=ModalityDecision(
                enabled=asr_enabled,
                confidence=round(asr_score, 4),
                routing_score=round(asr_score, 4),
                mode=request.asr,
                execution_state=self._execution_state(request.asr, asr_enabled),
                reason=f"ASR tín hiệu: {', '.join(asr_matches) or 'không có'}",
                anchors=[],
            ),
            api_planner=ModalityDecision(
                enabled=api_enabled,
                confidence=round(complexity, 4),
                routing_score=round(complexity, 4),
                mode=request.api_planner,
                execution_state=self._execution_state(
                    request.api_planner, api_enabled
                ),
                reason="Chỉ là routing score; bản demo chưa gọi model cloud.",
                anchors=[],
            ),
            complexity=round(complexity, 4),
            notes=notes,
        )
