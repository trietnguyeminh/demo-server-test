from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
from typing import Any
from urllib.parse import quote

import numpy as np

from .config import Settings
from .contracts import ASR_INDEX_CONTRACT_VERSION
from .router import normalize_text


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def tokenize(value: str) -> list[str]:
    return [token for token in normalize_text(value).split() if len(token) >= 2]


@dataclass
class EngineStatus:
    mode: str
    visual_ready: bool
    ocr_ready: bool
    asr_ready: bool
    details: dict[str, Any]


class BaseEngine:
    mode = "base"

    def status(self) -> EngineStatus:
        raise NotImplementedError

    def search_visual(self, query: str, top_k: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def search_ocr(
        self, query: str, anchors: list[str], top_k: int
    ) -> list[dict[str, Any]]:
        return []

    def search_asr(self, query: str, top_k: int) -> list[dict[str, Any]]:
        return []

    def frame_path(self, item_id: str) -> Path | None:
        return None


class MockEngine(BaseEngine):
    mode = "mock"

    def __init__(self) -> None:
        self._items = [
            {
                "item_id": "L01_V003_26",
                "video_id": "L01_V003",
                "pts_time": 92.4,
                "visual_score": 0.91,
                "ocr_text": "YAMAHA",
                "asr_text": "",
                "tags": ["drum", "person", "outdoor", "yamaha"],
            },
            {
                "item_id": "L02_V011_08",
                "video_id": "L02_V011",
                "pts_time": 31.2,
                "visual_score": 0.86,
                "ocr_text": "PHARMACY 24H",
                "asr_text": "",
                "tags": ["street", "signboard", "store"],
            },
            {
                "item_id": "L03_V004_19",
                "video_id": "L03_V004",
                "pts_time": 74.8,
                "visual_score": 0.84,
                "ocr_text": "59A-123.45",
                "asr_text": "",
                "tags": ["motorbike", "license plate", "street"],
            },
            {
                "item_id": "L04_V007_12",
                "video_id": "L04_V007",
                "pts_time": 48.0,
                "visual_score": 0.82,
                "ocr_text": "",
                "asr_text": "xin chào quý vị",
                "tags": ["speaker", "stage", "microphone"],
            },
            {
                "item_id": "L05_V002_05",
                "video_id": "L05_V002",
                "pts_time": 20.0,
                "visual_score": 0.80,
                "ocr_text": "",
                "asr_text": "",
                "tags": ["red shirt", "car", "person"],
            },
        ]

    def status(self) -> EngineStatus:
        return EngineStatus(
            mode=self.mode,
            visual_ready=True,
            ocr_ready=True,
            asr_ready=True,
            details={"items": len(self._items), "note": "Deterministic demo data."},
        )

    def _query_terms(self, query: str) -> set[str]:
        return set(tokenize(query))

    def search_visual(self, query: str, top_k: int) -> list[dict[str, Any]]:
        terms = self._query_terms(query)
        rows = []
        for item in self._items:
            haystack = normalize_text(" ".join(item["tags"]))
            overlap = sum(term in haystack for term in terms)
            row = dict(item)
            row["visual_score"] = min(0.99, item["visual_score"] + 0.025 * overlap)
            rows.append(row)
        rows.sort(key=lambda row: row["visual_score"], reverse=True)
        for rank, row in enumerate(rows[:top_k], start=1):
            row["visual_rank"] = rank
        return rows[:top_k]

    def search_ocr(
        self, query: str, anchors: list[str], top_k: int
    ) -> list[dict[str, Any]]:
        terms = set(tokenize(query)) | set(anchors)
        rows = []
        for item in self._items:
            text = normalize_text(item["ocr_text"])
            matched = sorted(term for term in terms if term and term in text)
            if not matched:
                continue
            row = dict(item)
            row["ocr_score"] = 5.0 + len(matched)
            row["matched_anchors"] = matched
            rows.append(row)
        rows.sort(key=lambda row: row["ocr_score"], reverse=True)
        for rank, row in enumerate(rows[:top_k], start=1):
            row["ocr_rank"] = rank
        return rows[:top_k]

    def search_asr(self, query: str, top_k: int) -> list[dict[str, Any]]:
        terms = set(tokenize(query))
        rows = []
        for item in self._items:
            text = normalize_text(item["asr_text"])
            matched = sum(term in text for term in terms)
            if not matched:
                continue
            row = dict(item)
            row["asr_score"] = 3.0 + matched
            rows.append(row)
        for rank, row in enumerate(rows[:top_k], start=1):
            row["asr_rank"] = rank
        return rows[:top_k]


class PathResolver:
    def __init__(self, settings: Settings):
        self.dataset_root = settings.dataset_root
        self.project_roots = settings.project_roots

    def resolve(self, raw: str | Path | None) -> Path | None:
        if not raw:
            return None
        path = Path(str(raw))
        if path.is_file():
            return path

        parts = list(path.parts)
        if "aic2025_full_build" in parts:
            index = parts.index("aic2025_full_build")
            tail = Path(*parts[index + 1 :])
            for root in self.project_roots:
                candidate = root / tail
                if candidate.is_file():
                    return candidate

        if self.dataset_root is not None:
            for marker in ("aic2025", "datasets"):
                if marker in parts:
                    index = parts.index(marker)
                    candidate = self.dataset_root.joinpath(*parts[index + 1 :])
                    if candidate.is_file():
                        return candidate
        return None


class SQLiteReader:
    def __init__(self, path: Path, settings: Settings):
        self.source = path.resolve()
        self.settings = settings
        self.effective = self._prepare()

    def _uri(self, path: Path) -> str:
        return path.resolve().as_uri() + "?mode=ro"

    def _open_readonly(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._uri(path),
            uri=True,
            timeout=max(1.0, self.settings.sqlite_busy_timeout_ms / 1000.0),
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute(
            f"PRAGMA busy_timeout={int(self.settings.sqlite_busy_timeout_ms)}"
        )
        connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        return connection

    def _prepare(self) -> Path:
        if not self.source.is_file():
            raise FileNotFoundError(self.source)
        with self.source.open("rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                raise ValueError(f"Not a SQLite database: {self.source}")
        try:
            connection = self._open_readonly(self.source)
            connection.close()
            return self.source
        except sqlite3.Error:
            if not self.settings.sqlite_copy_fallback:
                raise

        cache = Path("/tmp/aic_demo_sqlite")
        cache.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(
            f"{self.source}:{self.source.stat().st_size}:{self.source.stat().st_mtime_ns}".encode()
        ).hexdigest()[:16]
        target = cache / f"index_{key}.sqlite"
        if not target.is_file():
            shutil.copy2(self.source, target)
            wal = Path(str(self.source) + "-wal")
            if wal.is_file():
                shutil.copy2(wal, Path(str(target) + "-wal"))
        return target

    def connect(self) -> sqlite3.Connection:
        if self.effective == self.source:
            return self._open_readonly(self.effective)
        connection = sqlite3.connect(
            self.effective,
            timeout=max(1.0, self.settings.sqlite_busy_timeout_ms / 1000.0),
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection


class ASRSQLiteIndex:
    """Frame-level ASR FTS adapter.

    The reviewed 03-note notebook builds one document per keyframe in tables
    ``docs`` and ``docs_fts``. Querying segment rows directly is not compatible
    with the fusion layer because segment rows do not have an ``item_id``.
    """

    CONTRACT_VERSION = ASR_INDEX_CONTRACT_VERSION
    REQUIRED_DOC_COLUMNS = {
        "item_id", "video_id", "pts_time", "text", "normalized_text",
    }

    def __init__(
        self,
        path: Path,
        settings: Settings,
        manifest_path: Path | None = None,
    ):
        self.reader = SQLiteReader(path, settings)
        self.manifest_path = manifest_path if manifest_path and manifest_path.is_file() else None
        self.manifest: dict[str, Any] = {}
        self.schema = self._detect_schema()
        if self.manifest_path is not None:
            try:
                payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    self.manifest = payload
                    declared = payload.get("contract_version")
                    if declared not in (None, "", self.CONTRACT_VERSION):
                        raise ValueError(
                            "ASR manifest contract mismatch: "
                            f"{declared!r} != {self.CONTRACT_VERSION!r}"
                        )
            except Exception as exc:
                self.manifest = {"manifest_error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _tables(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        safe = table.replace('"', '""')
        return {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{safe}")').fetchall()
        }

    def _detect_schema(self) -> str:
        connection = self.reader.connect()
        try:
            tables = self._tables(connection)
            if {"docs", "docs_fts"}.issubset(tables):
                columns = self._columns(connection, "docs")
                missing = self.REQUIRED_DOC_COLUMNS - columns
                if missing:
                    raise ValueError(
                        "ASR docs table misses required columns: "
                        + ", ".join(sorted(missing))
                    )
                # Executes an FTS query plan without requiring a matching row.
                connection.execute(
                    "SELECT COUNT(*) FROM docs_fts WHERE docs_fts MATCH ?",
                    ('"__aic_schema_probe__"',),
                ).fetchone()
                return self.CONTRACT_VERSION

            # Legacy compatibility is accepted only when the indexed rows already
            # map to keyframes. A segment-only index cannot be fused safely.
            if {"segments", "segments_fts"}.issubset(tables):
                columns = self._columns(connection, "segments")
                required = {"item_id", "video_id", "pts_time", "text"}
                if required.issubset(columns):
                    return "legacy-frame-segments-v1"

            raise ValueError(
                "Unsupported ASR SQLite schema. Expected docs/docs_fts from "
                "03-note stage 12, or a legacy keyframe-level segments index."
            )
        finally:
            connection.close()

    def status_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "contract_version": self.CONTRACT_VERSION,
            "schema": self.schema,
            "sqlite": str(self.reader.effective),
        }
        for key in (
            "profile", "trust_level", "window_s", "n_documents",
            "n_documents_with_speech", "n_videos_with_speech",
            "n_segments", "n_segments_kept", "derived_timestamp_ratio",
            "asr_model",
        ):
            if key in self.manifest:
                details[key] = self.manifest[key]
        if self.manifest_path is not None:
            details["manifest"] = str(self.manifest_path)
        if "manifest_error" in self.manifest:
            details["manifest_error"] = self.manifest["manifest_error"]
        return details

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        terms = tokenize(query)[:20]
        if not terms:
            return []
        match_query = " OR ".join(
            f'"{term.replace(chr(34), "")}"' for term in terms
        )
        connection = self.reader.connect()
        try:
            if self.schema == self.CONTRACT_VERSION:
                sql = (
                    "SELECT d.*, bm25(docs_fts) AS bm25_score "
                    "FROM docs_fts JOIN docs d ON d.rowid=docs_fts.rowid "
                    "WHERE docs_fts MATCH ? ORDER BY bm25_score LIMIT ?"
                )
            else:
                sql = (
                    "SELECT s.*, bm25(segments_fts) AS bm25_score "
                    "FROM segments_fts JOIN segments s ON s.rowid=segments_fts.rowid "
                    "WHERE segments_fts MATCH ? ORDER BY bm25_score LIMIT ?"
                )
            rows = [
                dict(row)
                for row in connection.execute(
                    sql, (match_query, int(top_k))
                ).fetchall()
            ]
        finally:
            connection.close()

        for rank, row in enumerate(rows, start=1):
            row["asr_rank"] = rank
            row["asr_score"] = -float(row.get("bm25_score", 0.0))
            row["asr_text"] = str(row.get("text", ""))
        return rows


class SigLIPTextEncoder:
    def __init__(self, model_dir: Path):
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.processor = AutoProcessor.from_pretrained(
            str(model_dir), local_files_only=True
        )
        self.model = AutoModel.from_pretrained(
            str(model_dir), local_files_only=True, torch_dtype=dtype
        ).eval().to(self.device)
        self.lock = threading.Lock()

    def _unwrap(self, value: Any, expected_batch: int):
        torch = self.torch
        if torch.is_tensor(value):
            tensor = value
        else:
            tensor = None
            for name in ("text_embeds", "pooler_output"):
                candidate = getattr(value, name, None)
                if torch.is_tensor(candidate):
                    tensor = candidate
                    break
            if tensor is None and isinstance(value, (tuple, list)):
                tensor = next(
                    (
                        item
                        for item in value
                        if torch.is_tensor(item) and item.ndim == 2
                    ),
                    None,
                )
        if tensor is None or tensor.ndim != 2:
            raise TypeError(f"Unsupported text feature output: {type(value)!r}")
        if tensor.shape[0] != expected_batch:
            raise ValueError("Text embedding batch mismatch")
        return tensor

    def encode(self, text: str) -> np.ndarray:
        torch = self.torch
        with self.lock:
            inputs = self.processor(
                text=[text], padding="max_length", return_tensors="pt"
            )
            inputs = {
                key: value.to(self.device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }
            with torch.inference_mode():
                method = getattr(self.model, "get_text_features", None)
                if callable(method):
                    try:
                        raw = method(**inputs)
                    except TypeError:
                        allowed = {
                            key: inputs[key]
                            for key in ("input_ids", "attention_mask", "position_ids")
                            if key in inputs
                        }
                        raw = method(**allowed)
                else:
                    raw = self.model(**inputs)
                features = self._unwrap(raw, 1).to(dtype=torch.float32)
                features = torch.nn.functional.normalize(features, dim=-1)
            return features[0].detach().cpu().numpy().astype(np.float32)


class ArtifactEngine(BaseEngine):
    mode = "artifact"

    def __init__(self, settings: Settings):
        if settings.visual_manifest is None:
            raise ValueError("AIC_VISUAL_MANIFEST is required in artifact mode")
        if settings.ocr_sqlite is None:
            raise ValueError("AIC_OCR_SQLITE is required in artifact mode")
        if settings.model_dir is None:
            raise ValueError("AIC_MODEL_DIR is required in artifact mode")

        self.settings = settings
        self.resolver = PathResolver(settings)
        manifest = json.loads(settings.visual_manifest.read_text(encoding="utf-8"))
        if "result" in manifest:
            manifest = manifest["result"].get("visual_index_manifest", manifest["result"])
        if "visual_index_manifest" in manifest:
            manifest = manifest["visual_index_manifest"]
        self.manifest = manifest
        embeddings_path = self.resolver.resolve(manifest.get("embeddings_path"))
        metadata_path = self.resolver.resolve(manifest.get("metadata_path"))
        if embeddings_path is None:
            raise FileNotFoundError(
                f"Cannot resolve visual embeddings: {manifest.get('embeddings_path')}"
            )
        if metadata_path is None:
            raise FileNotFoundError(
                f"Cannot resolve visual metadata: {manifest.get('metadata_path')}"
            )
        self.manifest["embeddings_path"] = str(embeddings_path)
        self.manifest["metadata_path"] = str(metadata_path)
        self.embeddings = np.load(embeddings_path, mmap_mode="r")
        self.metadata = read_jsonl(metadata_path)
        self.by_item = {str(row["item_id"]): row for row in self.metadata}
        self.encoder = SigLIPTextEncoder(settings.model_dir)
        self.sqlite = SQLiteReader(settings.ocr_sqlite, settings)
        self.faiss = None
        faiss_path = self.resolver.resolve(manifest.get("faiss_path"))
        if faiss_path is not None and faiss_path.is_file():
            self.manifest["faiss_path"] = str(faiss_path)
            try:
                import faiss

                self.faiss = faiss.read_index(str(faiss_path))
            except Exception as exc:
                print("[WARN] FAISS unavailable, using NumPy search:", exc)

        self.asr: ASRSQLiteIndex | None = None
        self.asr_error: str | None = None
        if settings.asr_sqlite and settings.asr_sqlite.is_file():
            try:
                self.asr = ASRSQLiteIndex(
                    settings.asr_sqlite,
                    settings,
                    settings.asr_manifest,
                )
            except Exception as exc:
                self.asr_error = f"{type(exc).__name__}: {exc}"
                print("[WARN] ASR index disabled:", self.asr_error)

    def status(self) -> EngineStatus:
        return EngineStatus(
            mode=self.mode,
            visual_ready=True,
            ocr_ready=True,
            asr_ready=self.asr is not None,
            details={
                "visual_rows": len(self.metadata),
                "dimension": int(self.embeddings.shape[1]),
                "faiss": self.faiss is not None,
                "ocr_sqlite": str(self.sqlite.effective),
                "asr": (
                    self.asr.status_details()
                    if self.asr is not None
                    else {
                        "ready": False,
                        "error": self.asr_error,
                        "requested_path": (
                            str(self.settings.asr_sqlite)
                            if self.settings.asr_sqlite
                            else None
                        ),
                    }
                ),
            },
        )

    def search_visual(self, query: str, top_k: int) -> list[dict[str, Any]]:
        vector = self.encoder.encode(query)
        top_k = min(top_k, len(self.metadata))
        if self.faiss is not None:
            scores, indices = self.faiss.search(vector[None], top_k * 3)
            pairs = zip(indices[0].tolist(), scores[0].tolist())
        else:
            candidates: list[tuple[int, float]] = []
            chunk = self.settings.visual_search_chunk
            for start in range(0, len(self.embeddings), chunk):
                block = np.asarray(self.embeddings[start : start + chunk], dtype=np.float32)
                scores = block @ vector
                local_k = min(top_k * 3, len(scores))
                indices = np.argpartition(scores, -local_k)[-local_k:]
                candidates.extend(
                    (start + int(index), float(scores[index])) for index in indices
                )
            pairs = sorted(candidates, key=lambda pair: pair[1], reverse=True)

        rows: list[dict[str, Any]] = []
        per_video: Counter[str] = Counter()
        for index, score in pairs:
            if index < 0:
                continue
            row = dict(self.metadata[index])
            video_id = str(row.get("video_id", "unknown"))
            if per_video[video_id] >= self.settings.max_results_per_video:
                continue
            per_video[video_id] += 1
            row["visual_score"] = float(score)
            row["visual_rank"] = len(rows) + 1
            rows.append(row)
            if len(rows) >= top_k:
                break
        return rows

    def search_ocr(
        self, query: str, anchors: list[str], top_k: int
    ) -> list[dict[str, Any]]:
        terms = tokenize(query)[:20]
        if not terms and not anchors:
            return []
        match_query = " OR ".join(
            f'"{term.replace(chr(34), "")}"' for term in terms
        )
        connection = self.sqlite.connect()
        try:
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT d.*, bm25(docs_fts) AS bm25_score "
                    "FROM docs_fts JOIN docs d ON d.rowid=docs_fts.rowid "
                    "WHERE docs_fts MATCH ? ORDER BY bm25_score LIMIT ?",
                    (match_query, int(top_k * 3)),
                ).fetchall()
            ] if match_query else []
        finally:
            connection.close()

        for row in rows:
            normalized = str(row.get("normalized_text", ""))
            matched = [anchor for anchor in anchors if anchor in normalized]
            row["matched_anchors"] = matched
            row["ocr_score"] = -float(row.get("bm25_score", 0.0)) + 5.0 * bool(matched)
            row["ocr_text"] = row.get("text", "")
        rows.sort(key=lambda row: row["ocr_score"], reverse=True)
        for rank, row in enumerate(rows[:top_k], start=1):
            row["ocr_rank"] = rank
        return rows[:top_k]

    def search_asr(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if self.asr is None:
            return []
        return self.asr.search(query, top_k)

    def frame_path(self, item_id: str) -> Path | None:
        row = self.by_item.get(item_id)
        return self.resolver.resolve(row.get("image_path")) if row else None


def build_engine(settings: Settings) -> BaseEngine:
    if settings.mode == "mock":
        return MockEngine()
    if settings.mode == "artifact":
        return ArtifactEngine(settings)
    raise ValueError(f"Unsupported AIC_MODE={settings.mode!r}")
