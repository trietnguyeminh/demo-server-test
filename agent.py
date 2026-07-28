from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import threading
import time
import uuid
from typing import Any

import httpx

from .config import Settings
from .models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentMode,
    AgentModelTier,
    AgentPlan,
    AgentRuntimeInfo,
    ModalityMode,
    SearchHit,
    SearchRequest,
)
from .service import SearchService


AGENT_CONTRACT_VERSION = "external-agent-v1"


@dataclass
class AgentCompletion:
    content: str
    model: str
    latency_ms: float
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionState:
    messages: list[dict[str, str]] = field(default_factory=list)
    last_hits: list[SearchHit] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


class ProviderError(RuntimeError):
    pass


class BaseAgentProvider:
    name = "base"

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
    ) -> AgentCompletion:
        raise NotImplementedError


class GroqAgentProvider(BaseAgentProvider):
    name = "groq"

    def __init__(self, settings: Settings):
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is missing")
        self.settings = settings
        self.client = httpx.Client(
            timeout=httpx.Timeout(settings.agent_timeout_s),
            limits=httpx.Limits(
                max_connections=8,
                max_keepalive_connections=4,
            ),
        )

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
    ) -> AgentCompletion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_completion_tokens": max_tokens,
            "service_tier": self.settings.agent_service_tier,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            response = self.client.post(
                f"{self.settings.agent_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Groq timeout after {self.settings.agent_timeout_s:.1f}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Groq network error: {exc}") from exc

        latency_ms = (time.perf_counter() - started) * 1000.0
        if response.status_code >= 400:
            body = response.text[:1_500]
            raise ProviderError(
                f"Groq HTTP {response.status_code}: {body}"
            )

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderError(
                "Groq response does not contain choices[0].message.content"
            ) from exc

        usage = dict(data.get("usage") or {})
        groq_usage = (data.get("x_groq") or {}).get("usage")
        if isinstance(groq_usage, dict):
            usage.update(
                {
                    f"groq_{key}": value
                    for key, value in groq_usage.items()
                }
            )

        return AgentCompletion(
            content=str(content or ""),
            model=model,
            latency_ms=latency_ms,
            usage=usage,
        )


class MockAgentProvider(BaseAgentProvider):
    name = "mock"

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
    ) -> AgentCompletion:
        user_text = next(
            (
                message["content"]
                for message in reversed(messages)
                if message["role"] == "user"
            ),
            "",
        )
        if json_mode:
            query = user_text
            marker = "CURRENT_USER_MESSAGE:"
            if marker in user_text:
                query = user_text.split(marker, 1)[1].splitlines()[0].strip()
            content = json.dumps(
                {
                    "resolved_query": query,
                    "search_required": True,
                    "ocr": "auto",
                    "asr": "auto",
                    "intent": "search",
                    "rationale": "mock structured planner",
                    "assistant_preface": "Tôi sẽ tìm bằng chứng đa phương thức.",
                },
                ensure_ascii=False,
            )
        else:
            content = (
                "Dựa trên evidence retrieval, đây là các candidate phù hợp nhất. "
                "Hãy kiểm tra gallery và timestamp trước khi nộp."
            )
        return AgentCompletion(
            content=content,
            model=model,
            latency_ms=2.0,
            usage={"mock": True, "max_tokens": max_tokens},
        )


def _extract_json_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ProviderError("Planner did not return a JSON object")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Planner returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProviderError("Planner JSON must be an object")
    return value


class AgentService:
    def __init__(
        self,
        search_service: SearchService,
        settings: Settings,
    ):
        self.search_service = search_service
        self.settings = settings
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionState] = {}

        self.provider: BaseAgentProvider | None = None
        self.provider_error: str | None = None
        if settings.agent_enabled:
            try:
                if settings.agent_provider == "mock":
                    self.provider = MockAgentProvider()
                elif settings.agent_provider == "groq":
                    self.provider = GroqAgentProvider(settings)
                else:
                    raise ValueError(
                        f"Unsupported AIC_AGENT_PROVIDER="
                        f"{settings.agent_provider!r}"
                    )
            except Exception as exc:
                self.provider_error = f"{type(exc).__name__}: {exc}"

    @property
    def ready(self) -> bool:
        return self.provider is not None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.agent_enabled,
            "ready": self.ready,
            "provider": self.settings.agent_provider,
            "fast_model": self.settings.agent_fast_model,
            "quality_model": self.settings.agent_quality_model,
            "timeout_s": self.settings.agent_timeout_s,
            "contract_version": AGENT_CONTRACT_VERSION,
            "error": self.provider_error,
            "api_key_configured": bool(self.settings.groq_api_key)
            if self.settings.agent_provider == "groq"
            else self.ready,
        }

    def _cleanup_sessions(self) -> None:
        cutoff = time.time() - self.settings.agent_session_ttl_s
        stale = [
            key
            for key, value in self._sessions.items()
            if value.updated_at < cutoff
        ]
        for key in stale:
            self._sessions.pop(key, None)

    def _session(self, session_id: str | None) -> tuple[str, SessionState]:
        with self._lock:
            self._cleanup_sessions()
            key = session_id or uuid.uuid4().hex
            state = self._sessions.setdefault(key, SessionState())
            state.updated_at = time.time()
            return key, state

    def reset(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def _model_for(
        self,
        request: AgentChatRequest,
        *,
        answer_stage: bool,
    ) -> str:
        if request.model_tier == AgentModelTier.fast:
            return self.settings.agent_fast_model
        if request.model_tier == AgentModelTier.quality:
            return self.settings.agent_quality_model
        if answer_stage or request.mode == AgentMode.full:
            return self.settings.agent_quality_model
        return self.settings.agent_fast_model

    @staticmethod
    def _history_text(state: SessionState, limit: int) -> str:
        rows = state.messages[-limit:]
        if not rows:
            return "(empty)"
        return "\n".join(
            f"{row['role'].upper()}: {row['content']}"
            for row in rows
        )

    @staticmethod
    def _evidence_rows(hits: list[SearchHit], limit: int) -> str:
        if not hits:
            return "(no evidence)"
        output = []
        for hit in hits[:limit]:
            evidence = hit.ocr_text or hit.asr_text or "visual evidence"
            output.append(
                json.dumps(
                    {
                        "rank": hit.rank,
                        "item_id": hit.item_id,
                        "video_id": hit.video_id,
                        "timestamp_s": hit.pts_time,
                        "modalities": hit.modalities,
                        "ocr_text": hit.ocr_text,
                        "asr_text": hit.asr_text,
                        "evidence": evidence,
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(output)

    def _local_plan(self, request: AgentChatRequest) -> AgentPlan:
        return AgentPlan(
            resolved_query=request.message.strip(),
            search_required=True,
            ocr=request.ocr,
            asr=request.asr,
            intent="search",
            rationale="local fallback: external planner unavailable",
            assistant_preface="Tôi sẽ dùng router local để tìm kiếm.",
        )

    def _plan_with_model(
        self,
        request: AgentChatRequest,
        state: SessionState,
    ) -> tuple[AgentPlan, AgentCompletion]:
        assert self.provider is not None
        model = self._model_for(request, answer_stage=False)
        system = """Bạn là planner cho hệ thống truy xuất video AIC.
Mục tiêu: chuyển hội thoại thành một truy vấn tìm kiếm ngắn, chính xác và
quyết định có cần OCR/ASR. Không được khẳng định nội dung video khi chưa có
evidence. Trả về đúng một JSON object với schema:
{
  "resolved_query": "string",
  "search_required": true,
  "ocr": "off|auto|on",
  "asr": "off|auto|on",
  "intent": "search|refine|explain_previous",
  "rationale": "short string",
  "assistant_preface": "short Vietnamese string"
}
Quy tắc:
- Tên thương hiệu, khẩu hiệu, biển số, chữ in trên đồ vật => OCR on/auto.
- Nội dung nói, hát, phát biểu, lời thoại => ASR on/auto.
- Từ 'phát' không phải từ 'hát'.
- Câu hỏi tham chiếu 'ảnh số 2', 'kết quả trước' có thể dùng evidence trước và
  đặt search_required=false.
- Giữ resolved_query bằng tiếng Việt, không thêm chi tiết không có."""
        user = f"""CONVERSATION:
{self._history_text(state, self.settings.agent_max_history)}

PREVIOUS_EVIDENCE:
{self._evidence_rows(state.last_hits, 5)}

CURRENT_USER_MESSAGE: {request.message}
"""
        completion = self.provider.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            json_mode=True,
            max_tokens=420,
        )
        raw = _extract_json_object(completion.content)

        resolved = str(raw.get("resolved_query") or request.message).strip()
        if not resolved:
            resolved = request.message.strip()

        def modality(value: Any) -> ModalityMode:
            try:
                return ModalityMode(str(value or "auto").lower())
            except ValueError:
                return ModalityMode.auto

        plan = AgentPlan(
            resolved_query=resolved[:1_000],
            search_required=bool(raw.get("search_required", True)),
            ocr=modality(raw.get("ocr")),
            asr=modality(raw.get("asr")),
            intent=str(raw.get("intent") or "search")[:80],
            rationale=str(raw.get("rationale") or "")[:500],
            assistant_preface=str(
                raw.get("assistant_preface") or ""
            )[:500],
        )
        return plan, completion

    @staticmethod
    def _effective_mode(
        user_mode: ModalityMode,
        planned_mode: ModalityMode,
    ) -> ModalityMode:
        if user_mode in {ModalityMode.off, ModalityMode.on}:
            return user_mode
        return planned_mode

    def _deterministic_reply(
        self,
        plan: AgentPlan,
        hits: list[SearchHit],
    ) -> str:
        prefix = plan.assistant_preface.strip()
        if not hits:
            body = (
                "Chưa có candidate đủ phù hợp. Hãy thử bật OCR/ASR cưỡng bức "
                "hoặc mô tả thêm vật thể, hành động và bối cảnh."
            )
        else:
            top = hits[0]
            evidence = top.ocr_text or top.asr_text
            body = (
                f"Candidate đầu là {top.video_id} tại {top.pts_time:.2f}s"
                f" (rank {top.rank})."
            )
            if evidence:
                body += f" Evidence: {evidence[:240]}"
            body += " Hãy kiểm tra ảnh và các frame lân cận trước khi nộp."
        return " ".join(part for part in (prefix, body) if part)

    def _answer_with_model(
        self,
        request: AgentChatRequest,
        state: SessionState,
        plan: AgentPlan,
        hits: list[SearchHit],
    ) -> AgentCompletion:
        assert self.provider is not None
        model = self._model_for(request, answer_stage=True)
        system = """Bạn là trợ lý video retrieval AIC.
Chỉ trả lời dựa trên EVIDENCE được cung cấp. Không bịa nội dung, không khẳng
định chắc chắn khi evidence yếu. Trả lời tiếng Việt, ngắn gọn, ưu tiên:
1) kết luận/candidate;
2) video_id và timestamp;
3) OCR/ASR evidence;
4) bước kiểm tra tiếp theo.
Không dùng markdown table."""
        user = f"""USER_MESSAGE:
{request.message}

PLAN:
{plan.model_dump_json()}

EVIDENCE:
{self._evidence_rows(hits or state.last_hits, self.settings.agent_max_evidence_hits)}
"""
        return self.provider.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            json_mode=False,
            max_tokens=520,
        )

    def chat(self, request: AgentChatRequest) -> AgentChatResponse:
        total_started = time.perf_counter()
        session_id, state = self._session(request.session_id)
        warnings: list[str] = []
        fallback_used = False
        planner_ms = 0.0
        answer_ms = 0.0
        usage: dict[str, Any] = {}
        selected_model: str | None = None

        if request.mode == AgentMode.local:
            plan = self._local_plan(request)
        elif not self.ready:
            plan = self._local_plan(request)
            fallback_used = True
            warnings.append(
                "External agent chưa sẵn sàng; dùng local router fallback."
            )
            if self.provider_error:
                warnings.append(self.provider_error)
        else:
            try:
                plan, completion = self._plan_with_model(request, state)
                planner_ms = completion.latency_ms
                usage["planner"] = completion.usage
                selected_model = completion.model
            except Exception as exc:
                plan = self._local_plan(request)
                fallback_used = True
                warnings.append(
                    f"External planner lỗi; dùng local fallback: "
                    f"{type(exc).__name__}: {exc}"
                )

        search_response = None
        search_started = time.perf_counter()
        if plan.search_required:
            ocr_mode = self._effective_mode(request.ocr, plan.ocr)
            asr_mode = self._effective_mode(request.asr, plan.asr)
            search_response = self.search_service.search(
                SearchRequest(
                    query=plan.resolved_query,
                    profile=request.profile,
                    top_k=request.top_k,
                    ocr=ocr_mode,
                    asr=asr_mode,
                    api_planner=ModalityMode.off,
                    adaptive_fallback=request.adaptive_fallback,
                )
            )
            hits = search_response.hits
        else:
            hits = list(state.last_hits)
        search_ms = (time.perf_counter() - search_started) * 1000.0

        if (
            request.mode == AgentMode.full
            and self.ready
            and not fallback_used
        ):
            try:
                answer = self._answer_with_model(
                    request,
                    state,
                    plan,
                    hits,
                )
                reply = answer.content.strip()
                answer_ms = answer.latency_ms
                usage["answer"] = answer.usage
                selected_model = answer.model
            except Exception as exc:
                fallback_used = True
                warnings.append(
                    f"External answer lỗi; dùng evidence summary: "
                    f"{type(exc).__name__}: {exc}"
                )
                reply = self._deterministic_reply(plan, hits)
        else:
            reply = self._deterministic_reply(plan, hits)

        with self._lock:
            state.messages.extend(
                [
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": reply},
                ]
            )
            state.messages = state.messages[
                -self.settings.agent_max_history :
            ]
            if search_response is not None:
                state.last_hits = list(search_response.hits)
            state.updated_at = time.time()

        total_ms = (time.perf_counter() - total_started) * 1000.0
        return AgentChatResponse(
            session_id=session_id,
            reply=reply,
            plan=plan,
            search=search_response,
            agent=AgentRuntimeInfo(
                enabled=self.settings.agent_enabled,
                ready=self.ready,
                provider=self.settings.agent_provider,
                model=selected_model,
                model_tier=request.model_tier,
                fallback_used=fallback_used,
                error=self.provider_error,
                usage=usage,
            ),
            latency_ms={
                "planner_ms": round(planner_ms, 3),
                "search_ms": round(search_ms, 3),
                "answer_ms": round(answer_ms, 3),
                "total_ms": round(total_ms, 3),
            },
            warnings=warnings,
        )
