from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SearchProfile(str, Enum):
    fast = "fast"
    auto = "auto"
    accurate = "accurate"


class ModalityMode(str, Enum):
    off = "off"
    auto = "auto"
    on = "on"


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    profile: SearchProfile = SearchProfile.auto
    top_k: int = Field(default=20, ge=1, le=100)
    ocr: ModalityMode = ModalityMode.auto
    asr: ModalityMode = ModalityMode.auto
    api_planner: ModalityMode = ModalityMode.off
    adaptive_fallback: bool = True


class RouteRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    profile: SearchProfile = SearchProfile.auto
    ocr: ModalityMode = ModalityMode.auto
    asr: ModalityMode = ModalityMode.auto
    api_planner: ModalityMode = ModalityMode.off


class ModalityDecision(BaseModel):
    enabled: bool
    # Backward-compatible alias retained for older frontends. These are
    # heuristic routing scores, not calibrated probabilities.
    confidence: float = Field(ge=0.0, le=1.0)
    routing_score: float = Field(ge=0.0, le=1.0)
    mode: ModalityMode
    execution_state: str
    reason: str
    anchors: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    query: str
    visual_query: str
    ocr_query: str
    asr_query: str
    visual: ModalityDecision
    ocr: ModalityDecision
    asr: ModalityDecision
    api_planner: ModalityDecision
    complexity: float = Field(ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    rank: int
    item_id: str
    video_id: str
    pts_time: float
    image_url: str
    fused_score: float
    visual_score: float | None = None
    ocr_score: float | None = None
    asr_score: float | None = None
    ocr_text: str = ""
    asr_text: str = ""
    matched_anchors: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    profile: SearchProfile
    route: RouteDecision
    hits: list[SearchHit]
    latency_ms: dict[str, float]
    fallback_used: bool
    warnings: list[str]
    backend_mode: str


class BenchmarkRequest(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=100)
    profile: SearchProfile = SearchProfile.auto
    repeats: int = Field(default=1, ge=1, le=10)
    top_k: int = Field(default=10, ge=1, le=50)
