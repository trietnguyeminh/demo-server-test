from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _env_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw) if raw else None


def _env_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    mode: str
    host: str
    port: int
    cors_origins: tuple[str, ...]

    visual_manifest: Path | None
    ocr_sqlite: Path | None
    model_dir: Path | None
    asr_sqlite: Path | None
    dataset_root: Path | None
    project_roots: tuple[Path, ...]

    default_top_k: int
    max_top_k: int
    max_results_per_video: int
    visual_search_chunk: int
    sqlite_copy_fallback: bool
    sqlite_busy_timeout_ms: int

    fast_total_target_ms: float
    auto_total_target_ms: float
    accurate_total_target_ms: float

    @classmethod
    def from_env(cls) -> "Settings":
        roots = tuple(Path(value) for value in _env_list("AIC_PROJECT_ROOTS"))
        return cls(
            mode=os.getenv("AIC_MODE", "mock").strip().lower(),
            host=os.getenv("AIC_HOST", "0.0.0.0"),
            port=_env_int("AIC_PORT", 8000),
            cors_origins=_env_list("AIC_CORS_ORIGINS", ("*",)),
            visual_manifest=_env_path("AIC_VISUAL_MANIFEST"),
            ocr_sqlite=_env_path("AIC_OCR_SQLITE"),
            model_dir=_env_path("AIC_MODEL_DIR"),
            asr_sqlite=_env_path("AIC_ASR_SQLITE"),
            dataset_root=_env_path("AIC_DATASET_ROOT"),
            project_roots=roots,
            default_top_k=_env_int("AIC_DEFAULT_TOP_K", 20),
            max_top_k=_env_int("AIC_MAX_TOP_K", 100),
            max_results_per_video=_env_int("AIC_MAX_RESULTS_PER_VIDEO", 4),
            visual_search_chunk=_env_int("AIC_VISUAL_SEARCH_CHUNK", 20_000),
            sqlite_copy_fallback=_env_bool("AIC_SQLITE_COPY_FALLBACK", True),
            sqlite_busy_timeout_ms=_env_int("AIC_SQLITE_BUSY_TIMEOUT_MS", 30_000),
            fast_total_target_ms=_env_float("AIC_FAST_TARGET_MS", 2_000.0),
            auto_total_target_ms=_env_float("AIC_AUTO_TARGET_MS", 5_000.0),
            accurate_total_target_ms=_env_float("AIC_ACCURATE_TARGET_MS", 8_000.0),
        )
