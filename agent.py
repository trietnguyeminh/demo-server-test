from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import threading
import time
import uuid
from typing import Any

from .config import Settings
from .contracts import AGENT_CONTRACT_VERSION
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
from .providers import (
    PROVIDER_REGISTRY_CONTRACT,
    ProviderCompletion,
    ProviderError,
    ProviderRegistry,
)
from .service import SearchService



@dataclass
class SessionState:
    messages: list[dict[str, str]] = field(default_factory=list)
    last_hits: list[SearchHit] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


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
    def __init__(self, search_service: SearchService, settings: Settings):
        self.search_service = search_service
        self.settings = settings
        self.registry = ProviderRegistry(settings)
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionState] = {}

    @property
    def ready(self) -> bool:
        return self.registry.ready

    def status(self) -> dict[str, Any]:
        status = self.registry.status()
        status.update(
            {
                "timeout_s": self.settings.agent_timeout_s,
                "agent_contract_version": AGENT_CONTRACT_VERSION,
                "provider_registry_contract": PROVIDER_REGISTRY_CONTRACT,
                "allow_model_override": self.settings.agent_allow_model_override,
            }
        )
        return status

    def _cleanup_sessions(self) -> None:
        cutoff = time.time() - self.settings.agent_session_ttl_s
        stale = [k for k,v in self._sessions.items() if v.updated_at < cutoff]
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

    def _resolve_provider_and_model(
        self,
        request: AgentChatRequest,
        *,
        answer_stage: bool,
    ):
        provider, spec = self.registry.resolve(request.provider)
        if request.model and self.settings.agent_allow_model_override:
            model = request.model.strip()
        elif request.model_tier == AgentModelTier.fast:
            model = spec.fast_model
        elif request.model_tier == AgentModelTier.quality:
            model = spec.quality_model
        elif answer_stage or request.mode == AgentMode.full:
            model = spec.quality_model
        else:
            model = spec.fast_model
        if not model:
            raise ProviderError(f"Provider {spec.id} has no model configured")
        return provider, spec, model

    @staticmethod
    def _history_text(state: SessionState, limit: int) -> str:
        rows = state.messages[-limit:]
        return "(empty)" if not rows else "\n".join(
            f"{row['role'].upper()}: {row['content']}" for row in rows
        )

    @staticmethod
    def _evidence_rows(hits: list[SearchHit], limit: int) -> str:
        if not hits:
            return "(no evidence)"
        output=[]
        for hit in hits[:limit]:
            evidence=hit.ocr_text or hit.asr_text or "visual evidence"
            output.append(json.dumps({
                "rank":hit.rank,"item_id":hit.item_id,"video_id":hit.video_id,
                "timestamp_s":hit.pts_time,"modalities":hit.modalities,
                "ocr_text":hit.ocr_text,"asr_text":hit.asr_text,
                "evidence":evidence,
            }, ensure_ascii=False))
        return "\n".join(output)

    def _local_plan(self, request: AgentChatRequest) -> AgentPlan:
        return AgentPlan(
            resolved_query=request.message.strip(), search_required=True,
            ocr=request.ocr, asr=request.asr, intent="search",
            rationale="local fallback: external provider unavailable",
            assistant_preface="Tôi sẽ dùng router local để tìm kiếm.",
        )

    def _plan_with_model(self, request: AgentChatRequest, state: SessionState):
        provider, spec, model = self._resolve_provider_and_model(
            request, answer_stage=False
        )
        system = """Bạn là planner cho hệ thống truy xuất video AIC.
Chuyển hội thoại thành truy vấn tìm kiếm ngắn và quyết định OCR/ASR. Không được
khẳng định nội dung video khi chưa có evidence. Trả về đúng một JSON object:
{"resolved_query":"string","search_required":true,"ocr":"off|auto|on",
"asr":"off|auto|on","intent":"search|refine|explain_previous",
"rationale":"short string","assistant_preface":"short Vietnamese string"}.
Tên thương hiệu, khẩu hiệu, biển số, chữ in trên vật thể => OCR. Nội dung nói,
hát, phát biểu, lời thoại => ASR. Từ 'phát' không phải 'hát'."""
        user = f"""CONVERSATION:
{self._history_text(state, self.settings.agent_max_history)}

PREVIOUS_EVIDENCE:
{self._evidence_rows(state.last_hits, 5)}

CURRENT_USER_MESSAGE: {request.message}
"""
        completion = provider.complete(
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            model=model, json_mode=True, max_tokens=420,
        )
        raw=_extract_json_object(completion.content)
        resolved=str(raw.get("resolved_query") or request.message).strip() or request.message.strip()
        def modality(value: Any) -> ModalityMode:
            try: return ModalityMode(str(value or "auto").lower())
            except ValueError: return ModalityMode.auto
        plan=AgentPlan(
            resolved_query=resolved[:1000],
            search_required=bool(raw.get("search_required",True)),
            ocr=modality(raw.get("ocr")), asr=modality(raw.get("asr")),
            intent=str(raw.get("intent") or "search")[:80],
            rationale=str(raw.get("rationale") or "")[:500],
            assistant_preface=str(raw.get("assistant_preface") or "")[:500],
        )
        return plan, completion

    @staticmethod
    def _effective_mode(user_mode: ModalityMode, planned_mode: ModalityMode):
        return user_mode if user_mode in {ModalityMode.off,ModalityMode.on} else planned_mode

    def _deterministic_reply(self, plan: AgentPlan, hits: list[SearchHit]) -> str:
        prefix=plan.assistant_preface.strip()
        if not hits:
            body="Chưa có candidate đủ phù hợp. Hãy thử bật OCR/ASR hoặc mô tả thêm."
        else:
            top=hits[0]; evidence=top.ocr_text or top.asr_text
            body=f"Candidate đầu là {top.video_id} tại {top.pts_time:.2f}s (rank {top.rank})."
            if evidence: body += f" Evidence: {evidence[:240]}"
            body += " Hãy kiểm tra ảnh và frame lân cận trước khi nộp."
        return " ".join(part for part in (prefix,body) if part)

    def _answer_with_model(
        self, request: AgentChatRequest, state: SessionState,
        plan: AgentPlan, hits: list[SearchHit],
    ) -> ProviderCompletion:
        provider, spec, model = self._resolve_provider_and_model(
            request, answer_stage=True
        )
        system="""Bạn là trợ lý video retrieval AIC. Chỉ trả lời dựa trên EVIDENCE.
Không bịa. Trả lời tiếng Việt ngắn gọn: candidate, video_id/timestamp, OCR/ASR
evidence và bước kiểm tra tiếp theo."""
        user=f"""USER_MESSAGE:
{request.message}

PLAN:
{plan.model_dump_json()}

EVIDENCE:
{self._evidence_rows(hits or state.last_hits, self.settings.agent_max_evidence_hits)}
"""
        return provider.complete(
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            model=model,json_mode=False,max_tokens=520,
        )

    def chat(self, request: AgentChatRequest) -> AgentChatResponse:
        total_started=time.perf_counter(); session_id,state=self._session(request.session_id)
        warnings=[]; fallback_used=False; planner_ms=0.0; answer_ms=0.0
        usage={}; selected_provider="local"; selected_model=None; provider_error=None

        if request.mode == AgentMode.local:
            plan=self._local_plan(request)
        elif not self.ready:
            plan=self._local_plan(request); fallback_used=True
            warnings.append("Không có external provider sẵn sàng; dùng local fallback.")
        else:
            try:
                plan, completion=self._plan_with_model(request,state)
                planner_ms=completion.latency_ms; usage["planner"]=completion.usage
                selected_provider=completion.provider; selected_model=completion.model
            except Exception as exc:
                plan=self._local_plan(request); fallback_used=True
                provider_error=f"{type(exc).__name__}: {exc}"
                warnings.append(f"External planner lỗi; dùng local fallback: {provider_error}")

        search_response=None; search_started=time.perf_counter()
        if plan.search_required:
            search_response=self.search_service.search(SearchRequest(
                query=plan.resolved_query,profile=request.profile,top_k=request.top_k,
                ocr=self._effective_mode(request.ocr,plan.ocr),
                asr=self._effective_mode(request.asr,plan.asr),
                api_planner=ModalityMode.off,
                adaptive_fallback=request.adaptive_fallback,
            )); hits=search_response.hits
        else: hits=list(state.last_hits)
        search_ms=(time.perf_counter()-search_started)*1000.0

        if request.mode == AgentMode.full and self.ready and not fallback_used:
            try:
                answer=self._answer_with_model(request,state,plan,hits)
                reply=answer.content.strip(); answer_ms=answer.latency_ms
                usage["answer"]=answer.usage; selected_provider=answer.provider
                selected_model=answer.model
            except Exception as exc:
                fallback_used=True; provider_error=f"{type(exc).__name__}: {exc}"
                warnings.append(f"External answer lỗi; dùng evidence summary: {provider_error}")
                reply=self._deterministic_reply(plan,hits)
        else: reply=self._deterministic_reply(plan,hits)

        with self._lock:
            state.messages.extend([{"role":"user","content":request.message},{"role":"assistant","content":reply}])
            state.messages=state.messages[-self.settings.agent_max_history:]
            if search_response is not None: state.last_hits=list(search_response.hits)
            state.updated_at=time.time()

        total_ms=(time.perf_counter()-total_started)*1000.0
        return AgentChatResponse(
            session_id=session_id,reply=reply,plan=plan,search=search_response,
            agent=AgentRuntimeInfo(
                enabled=self.settings.agent_enabled,ready=self.ready,
                requested_provider=request.provider,provider=selected_provider,
                model=selected_model,model_tier=request.model_tier,
                fallback_used=fallback_used,error=provider_error,usage=usage,
            ),
            latency_ms={"planner_ms":round(planner_ms,3),"search_ms":round(search_ms,3),
                        "answer_ms":round(answer_ms,3),"total_ms":round(total_ms,3)},
            warnings=warnings,
        )
