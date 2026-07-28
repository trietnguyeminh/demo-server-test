# AIC Latency-first Retrieval Demo

Repo tối giản để kiểm tra ba thứ cùng lúc:

1. routing visual/OCR/ASR theo prompt;
2. latency breakdown;
3. UI có manual override và chạy được trên GitHub Pages.

## Chạy ngay bằng mock data

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Mở `http://127.0.0.1:8000`.

Kiểm tra API:

```bash
curl http://127.0.0.1:8000/api/health
curl -X POST http://127.0.0.1:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"người đánh trống Yamaha","profile":"auto","top_k":10}'
```

## Chạy test

```bash
pytest -q
```

Benchmark nhanh:

```bash
python scripts/benchmark.py --profile auto --repeats 3
```

## Chạy với artifact Kaggle thật

Attach output notebook `00-note`, `01-note`, `02-note` và dataset AIC2025. Sau đó đặt biến môi trường tương tự `.env.example`:

```python
import os

os.environ["AIC_MODE"] = "artifact"
os.environ["AIC_VISUAL_MANIFEST"] = "/kaggle/input/notebooks/nguyenminhtric/01-note/aic2025_full_build/artifacts/visual_index/visual_index_manifest.json"
os.environ["AIC_OCR_SQLITE"] = "/kaggle/input/notebooks/yixuanisthebest/02-note/aic2025_full_build/artifacts/ocr_index/ocr_index.sqlite"
os.environ["AIC_MODEL_DIR"] = "/kaggle/input/notebooks/nguyenminhtric/00-note/aic2025_full_build/artifacts/weights/siglip2_so400m_patch14_384"
os.environ["AIC_DATASET_ROOT"] = "/kaggle/input/datasets/nguynnc/aic2025"
```

Khởi động FastAPI:

```bash
python run.py
```

Hoặc chạy Gradio share UI trong Kaggle:

```bash
python scripts/gradio_demo.py
```

Gradio chỉ là demo nhanh; UI chính nằm trong `docs/`.

## API

| Endpoint | Mục đích |
|---|---|
| `GET /api/health` | trạng thái backend/index |
| `GET /api/config` | target latency và cấu hình |
| `POST /api/route` | xem router bật module nào |
| `POST /api/search` | retrieval + fusion + latency |
| `POST /api/benchmark` | p50/p90/p95 cho bộ query |
| `GET /api/frame/{item_id}` | trả frame hoặc placeholder |

## GitHub Pages UI

Thư mục `docs/` không có build step. Trên GitHub:

1. Push toàn bộ repo.
2. Mở **Settings → Pages**.
3. Chọn **Deploy from a branch**.
4. Chọn branch `main`, folder `/docs`.
5. Trong UI, nhập URL FastAPI backend vào ô **Server API**.

Backend phải bật CORS cho domain GitHub Pages. Demo mặc định dùng `AIC_CORS_ORIGINS=*`; khi triển khai thật nên giới hạn origin.

## Cấu trúc

```text
server/                 FastAPI + router + artifact adapters
docs/                   static GitHub Pages UI
report/                 báo cáo và test plan
tests/                  router/API smoke tests
scripts/gradio_demo.py  tùy chọn cho Kaggle
notebooks/              notebook start/smoke test trên Kaggle
```

## Phạm vi MVP

- Visual và OCR artifact mode đã có adapter.
- ASR là optional SQLite adapter với schema `segments` + `segments_fts`.
- API planner chỉ được biểu diễn trong router; cloud call cố ý chưa bật để giữ demo đơn giản và latency ổn định.
