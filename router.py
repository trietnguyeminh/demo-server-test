from __future__ import annotations

import re
import unicodedata

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
    OCR_TERMS = {
        "chữ", "dòng chữ", "bảng hiệu", "biển hiệu", "biển báo", "biển số",
        "logo", "thương hiệu", "nhãn", "tên cửa hàng", "số điện thoại",
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
    }

    PROFILE_THRESHOLDS = {
        SearchProfile.fast: {"ocr": 0.85, "asr": 0.88, "api": 0.98},
        SearchProfile.auto: {"ocr": 0.65, "asr": 0.70, "api": 0.82},
        SearchProfile.accurate: {"ocr": 0.25, "asr": 0.30, "api": 0.55},
    }

    def _contains_any(self, normalized: str, terms: set[str]) -> list[str]:
        return sorted(term for term in terms if term in normalized)

    def _extract_ocr_anchors(self, query: str) -> list[str]:
        anchors: list[str] = []
        for quoted in re.findall(r'["“”\']([^"“”\']{2,60})["“”\']', query):
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

        return sorted(set(filter(None, anchors)), key=lambda item: (-len(item), item))

    @staticmethod
    def _apply_override(
        mode: ModalityMode,
        confidence: float,
        threshold: float,
    ) -> bool:
        if mode == ModalityMode.on:
            return True
        if mode == ModalityMode.off:
            return False
        return confidence >= threshold

    def route(self, request: RouteRequest) -> RouteDecision:
        query = request.query.strip()
        normalized = normalize_text(query)
        thresholds = self.PROFILE_THRESHOLDS[request.profile]

        ocr_matches = self._contains_any(normalized, self.OCR_TERMS)
        asr_matches = self._contains_any(normalized, self.ASR_TERMS)
        complex_matches = self._contains_any(normalized, self.COMPLEX_TERMS)
        anchors = self._extract_ocr_anchors(query)

        ocr_confidence = min(
            1.0,
            0.08
            + 0.22 * len(ocr_matches)
            + (0.58 if anchors else 0.0),
        )
        asr_confidence = min(1.0, 0.05 + 0.70 * len(asr_matches))
        complexity = min(
            1.0,
            0.08
            + 0.18 * len(complex_matches)
            + 0.08 * max(0, len(normalized.split()) - 12)
            + (0.18 if ocr_confidence > 0.6 and asr_confidence > 0.6 else 0.0),
        )

        ocr_enabled = self._apply_override(
            request.ocr, ocr_confidence, thresholds["ocr"]
        )
        asr_enabled = self._apply_override(
            request.asr, asr_confidence, thresholds["asr"]
        )
        api_enabled = self._apply_override(
            request.api_planner, complexity, thresholds["api"]
        )

        visual_query = query
        for anchor in anchors:
            visual_query = re.sub(
                rf"(?i)(?<!\w){re.escape(anchor)}(?!\w)", " ", visual_query
            )
        visual_query = re.sub(r"\s+", " ", visual_query).strip() or query

        notes: list[str] = ["visual_always_on", "local_router"]
        if request.profile == SearchProfile.fast:
            notes.append("high_modality_thresholds")
        if anchors:
            notes.append("ocr_anchor_detected")
        if complexity >= 0.55:
            notes.append("complex_query")

        return RouteDecision(
            query=query,
            visual_query=visual_query,
            ocr_query=" ".join(anchors) if anchors else query,
            asr_query=query,
            visual=ModalityDecision(
                enabled=True,
                confidence=1.0,
                mode=ModalityMode.on,
                reason="Visual retrieval is the default fast path.",
                anchors=[],
            ),
            ocr=ModalityDecision(
                enabled=ocr_enabled,
                confidence=round(ocr_confidence, 4),
                mode=request.ocr,
                reason=(
                    f"OCR signals: {', '.join(ocr_matches) or 'none'}; "
                    f"anchors: {', '.join(anchors) or 'none'}"
                ),
                anchors=anchors,
            ),
            asr=ModalityDecision(
                enabled=asr_enabled,
                confidence=round(asr_confidence, 4),
                mode=request.asr,
                reason=f"ASR signals: {', '.join(asr_matches) or 'none'}",
                anchors=[],
            ),
            api_planner=ModalityDecision(
                enabled=api_enabled,
                confidence=round(complexity, 4),
                mode=request.api_planner,
                reason="API planner is reserved for complex queries.",
                anchors=[],
            ),
            complexity=round(complexity, 4),
            notes=notes,
        )
