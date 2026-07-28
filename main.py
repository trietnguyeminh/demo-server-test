from __future__ import annotations

from contextlib import asynccontextmanager
import html
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import build_engine
from .models import BenchmarkRequest, RouteRequest, SearchRequest
from .service import SearchService


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "docs"
settings = Settings.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = build_engine(settings)
    app.state.service = SearchService(engine, settings)
    yield


app = FastAPI(
    title="AIC Latency-first Retrieval Demo",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def service(request: Request) -> SearchService:
    return request.app.state.service


@app.get("/api/health")
def health(request: Request):
    status = service(request).engine.status()
    return {
        "status": "ok",
        "backend": status.__dict__,
        "default_top_k": settings.default_top_k,
        "profiles": ["fast", "auto", "accurate"],
        "api_planner_ready": False,
    }


@app.get("/api/config")
def config(request: Request):
    status = service(request).engine.status()
    return {
        "mode": settings.mode,
        "backend": status.__dict__,
        "api_planner_ready": False,
        "profile_descriptions": {
            "fast": "Visual-first; OCR chỉ khi tín hiệu rất mạnh.",
            "auto": "Visual + OCR nhẹ song song; cân bằng recall và latency.",
            "accurate": "Candidate pool lớn; OCR độc lập được phép ảnh hưởng ranking.",
        },
        "latency_targets_ms": {
            "fast": settings.fast_total_target_ms,
            "auto": settings.auto_total_target_ms,
            "accurate": settings.accurate_total_target_ms,
        },
    }


@app.post("/api/route")
def route(payload: RouteRequest, request: Request):
    return service(request).route(payload)


@app.post("/api/search")
def search(payload: SearchRequest, request: Request):
    try:
        return service(request).search(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/benchmark")
def benchmark(payload: BenchmarkRequest, request: Request):
    return service(request).benchmark(payload)


@app.get("/api/frame/{item_id}")
def frame(item_id: str, request: Request):
    path = service(request).engine.frame_path(item_id)
    if path is not None and path.is_file():
        return FileResponse(path)

    safe = html.escape(item_id)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
      <rect width="640" height="360" fill="#111827"/>
      <rect x="28" y="28" width="584" height="304" rx="18" fill="#1f2937" stroke="#64748b"/>
      <text x="320" y="165" text-anchor="middle" fill="#f8fafc" font-family="system-ui" font-size="28">AIC demo frame</text>
      <text x="320" y="210" text-anchor="middle" fill="#cbd5e1" font-family="monospace" font-size="20">{safe}</text>
    </svg>'''
    return Response(svg, media_type="image/svg+xml")


# Mount last so /api routes remain authoritative. The same files are GitHub Pages ready.
app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="ui")
