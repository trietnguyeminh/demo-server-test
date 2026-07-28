from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import statistics
import time
from typing import Any
from urllib.parse import quote

from .config import Settings
from .engine import BaseEngine
from .models import (
    BenchmarkRequest,
    RouteRequest,
    SearchHit,
    SearchProfile,
    SearchRequest,
    SearchResponse,
)
from .router import QueryRouter


PROFILE_LIMITS = {
    SearchProfile.fast: {"visual": 35, "ocr": 20, "asr": 15},
    SearchProfile.auto: {"visual": 80, "ocr": 45, "asr": 35},
    SearchProfile.accurate: {"visual": 150, "ocr": 100, "asr": 80},
}


class SearchService:
    def __init__(self, engine: BaseEngine, settings: Settings):
        self.engine = engine
        self.settings = settings
        self.router = QueryRouter()

    @staticmethod
    def _timed(callable_):
        started = time.perf_counter()
        value = callable_()
        return value, (time.perf_counter() - started) * 1000.0

    def route(self, request: RouteRequest):
        return self.router.route(request)

    def _fuse(
        self,
        visual: list[dict[str, Any]],
        ocr: list[dict[str, Any]],
        asr: list[dict[str, Any]],
        anchors: list[str],
        top_k: int,
        *,
        ocr_allow_standalone: bool,
    ) -> list[SearchHit]:
        visual_by = {str(row["item_id"]): row for row in visual}
        ocr_by = {str(row["item_id"]): row for row in ocr}
        asr_by = {str(row["item_id"]): row for row in asr}
        # Low-score Auto OCR is a cheap shadow search. It may enrich visual
        # candidates, but cannot inject unrelated OCR-only items into the gallery.
        ocr_ids = set(ocr_by) if ocr_allow_standalone else (set(ocr_by) & set(visual_by))
        item_ids = set(visual_by) | ocr_ids | set(asr_by)

        fused: list[dict[str, Any]] = []
        rrf_k = 60.0
        for item_id in item_ids:
            v = visual_by.get(item_id)
            o = ocr_by.get(item_id)
            a = asr_by.get(item_id)
            score = 0.0
            modalities: list[str] = []
            if v:
                score += 1.0 / (rrf_k + float(v.get("visual_rank", 999)))
                modalities.append("visual")
            if o:
                score += 1.0 / (rrf_k + float(o.get("ocr_rank", 999)))
                modalities.append("ocr")
            if a:
                score += 1.0 / (rrf_k + float(a.get("asr_rank", 999)))
                modalities.append("asr")
            if len(modalities) >= 2:
                score += 0.015 * (len(modalities) - 1)
            matched = list(o.get("matched_anchors", [])) if o else []
            if matched:
                score += 0.08
            if anchors and not matched:
                score *= 0.45

            base = dict(v or o or a or {})
            if o:
                base.update({
                    "ocr_text": o.get("ocr_text", o.get("text", "")),
                    "ocr_score": o.get("ocr_score"),
                    "matched_anchors": matched,
                })
            if a:
                base.update({
                    "asr_text": a.get("asr_text", a.get("text", "")),
                    "asr_score": a.get("asr_score"),
                })
            if v:
                base["visual_score"] = v.get("visual_score")
            base["item_id"] = item_id
            base["modalities"] = modalities
            base["fused_score"] = float(score)
            fused.append(base)

        fused.sort(key=lambda row: row["fused_score"], reverse=True)
        results: list[SearchHit] = []
        for rank, row in enumerate(fused[:top_k], start=1):
            item_id = str(row["item_id"])
            results.append(
                SearchHit(
                    rank=rank,
                    item_id=item_id,
                    video_id=str(row.get("video_id", "unknown")),
                    pts_time=float(row.get("pts_time", 0.0)),
                    image_url=f"/api/frame/{quote(item_id, safe='')}",
                    fused_score=float(row["fused_score"]),
                    visual_score=(
                        float(row["visual_score"])
                        if row.get("visual_score") is not None
                        else None
                    ),
                    ocr_score=(
                        float(row["ocr_score"])
                        if row.get("ocr_score") is not None
                        else None
                    ),
                    asr_score=(
                        float(row["asr_score"])
                        if row.get("asr_score") is not None
                        else None
                    ),
                    ocr_text=str(row.get("ocr_text", "")),
                    asr_text=str(row.get("asr_text", "")),
                    matched_anchors=list(row.get("matched_anchors", [])),
                    modalities=list(row.get("modalities", [])),
                    metadata={
                        key: value
                        for key, value in row.items()
                        if key
                        not in {
                            "item_id", "video_id", "pts_time", "image_path",
                            "visual_score", "ocr_score", "asr_score",
                            "ocr_text", "asr_text", "matched_anchors",
                            "modalities", "fused_score",
                        }
                    },
                )
            )
        return results

    def search(self, request: SearchRequest) -> SearchResponse:
        total_started = time.perf_counter()
        warnings: list[str] = []

        route_started = time.perf_counter()
        route = self.router.route(
            RouteRequest(
                query=request.query,
                profile=request.profile,
                ocr=request.ocr,
                asr=request.asr,
                api_planner=request.api_planner,
            )
        )
        route_ms = (time.perf_counter() - route_started) * 1000.0

        status = self.engine.status()
        if route.asr.enabled and not status.asr_ready:
            warnings.append("ASR được router yêu cầu nhưng chưa có ASR index.")
        if route.api_planner.enabled:
            warnings.append(
                "API planner chưa kết nối model cloud nên không được thực thi."
            )

        limits = PROFILE_LIMITS[request.profile]
        tasks: dict[str, Any] = {
            "visual": lambda: self.engine.search_visual(
                route.visual_query, limits["visual"]
            )
        }
        if route.ocr.enabled and status.ocr_ready:
            tasks["ocr"] = lambda: self.engine.search_ocr(
                route.ocr_query, route.ocr.anchors, limits["ocr"]
            )
        if route.asr.enabled and status.asr_ready:
            tasks["asr"] = lambda: self.engine.search_asr(
                route.asr_query, limits["asr"]
            )

        outputs: dict[str, list[dict[str, Any]]] = {
            "visual": [], "ocr": [], "asr": []
        }
        timings: dict[str, float] = {}
        retrieval_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
            futures = {
                name: executor.submit(self._timed, callback)
                for name, callback in tasks.items()
            }
            for name, future in futures.items():
                outputs[name], timings[f"{name}_ms"] = future.result()
        retrieval_wall_ms = (time.perf_counter() - retrieval_started) * 1000.0

        fallback_used = False
        if (
            request.adaptive_fallback
            and not route.ocr.enabled
            and status.ocr_ready
            and 0.30 <= route.ocr.confidence < 0.65
        ):
            visual_rows = outputs["visual"]
            margin = 1.0
            if len(visual_rows) >= 2:
                margin = abs(
                    float(visual_rows[0].get("visual_score", 0.0))
                    - float(visual_rows[1].get("visual_score", 0.0))
                )
            if margin < 0.025:
                outputs["ocr"], fallback_ms = self._timed(
                    lambda: self.engine.search_ocr(
                        route.ocr_query, route.ocr.anchors, limits["ocr"]
                    )
                )
                timings["ocr_fallback_ms"] = fallback_ms
                fallback_used = True
                warnings.append("OCR fallback chạy vì margin visual quá nhỏ.")

        fusion_started = time.perf_counter()
        ocr_allow_standalone = bool(
            request.ocr.value == "on"
            or request.profile == SearchProfile.accurate
            or route.ocr.anchors
            or route.ocr.routing_score >= 0.40
        )
        if (
            route.ocr.enabled
            and status.ocr_ready
            and not ocr_allow_standalone
        ):
            warnings.append(
                "OCR chạy song song ở chế độ shadow và chỉ bổ sung cho candidate visual."
            )

        hits = self._fuse(
            outputs["visual"],
            outputs["ocr"],
            outputs["asr"],
            route.ocr.anchors,
            min(request.top_k, self.settings.max_top_k),
            ocr_allow_standalone=ocr_allow_standalone,
        )
        fusion_ms = (time.perf_counter() - fusion_started) * 1000.0
        total_ms = (time.perf_counter() - total_started) * 1000.0

        latency = {
            "router_ms": round(route_ms, 3),
            "retrieval_wall_ms": round(retrieval_wall_ms, 3),
            **{key: round(value, 3) for key, value in timings.items()},
            "fusion_ms": round(fusion_ms, 3),
            "total_ms": round(total_ms, 3),
        }
        return SearchResponse(
            query=request.query,
            profile=request.profile,
            route=route,
            hits=hits,
            latency_ms=latency,
            fallback_used=fallback_used,
            warnings=warnings,
            backend_mode=status.mode,
        )

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] * (1 - fraction) + ordered[upper] * fraction

    def benchmark(self, request: BenchmarkRequest) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for query in request.queries:
            for repeat in range(request.repeats):
                response = self.search(
                    SearchRequest(
                        query=query,
                        profile=request.profile,
                        top_k=request.top_k,
                    )
                )
                rows.append({
                    "query": query,
                    "repeat": repeat + 1,
                    "total_ms": response.latency_ms["total_ms"],
                    "hit_count": len(response.hits),
                    "ocr_enabled": response.route.ocr.enabled,
                    "asr_enabled": response.route.asr.enabled,
                })
        totals = [row["total_ms"] for row in rows]
        return {
            "profile": request.profile,
            "runs": rows,
            "summary": {
                "count": len(rows),
                "mean_ms": round(statistics.fmean(totals), 3),
                "p50_ms": round(self._percentile(totals, 0.50), 3),
                "p90_ms": round(self._percentile(totals, 0.90), 3),
                "p95_ms": round(self._percentile(totals, 0.95), 3),
                "max_ms": round(max(totals), 3),
            },
        }
