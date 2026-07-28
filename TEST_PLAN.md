# Kế hoạch test hệ thống

## Bộ query tối thiểu

1. `người mặc áo đỏ đứng cạnh ô tô` — visual-only.
2. `người đánh trống Yamaha ngoài trời` — visual + OCR brand.
3. `xe máy có biển số bắt đầu bằng 59` — visual + OCR license plate.
4. `người đàn ông nói về thời tiết` — visual + ASR.
5. `MC nói xin chào quý vị trên sân khấu` — visual + ASR, OCR phụ đề tùy prompt.
6. `sau khi mở cửa người phụ nữ đặt túi lên bàn` — temporal/complex query.

## Test router

- Query object-only không bật OCR/ASR trong Fast.
- Brand hoặc chuỗi số bật OCR.
- Speech terms bật ASR.
- Manual `On` luôn thắng router.
- Manual `Off` luôn tắt module.

## Test latency

Chạy mỗi query 10 lần và ghi:

- router;
- visual;
- OCR;
- ASR;
- fusion;
- total;
- p50/p90/p95/max.

## Test resilience

- ASR index không tồn tại;
- OCR SQLite read-only;
- FAISS không cài, fallback NumPy;
- API planner bị tắt;
- image path không resolve được;
- query rỗng hoặc quá dài;
- server restart và cold start.

## Test accuracy

Mỗi query benchmark phải có:

```json
{
  "query_id": "ocr_brand_001",
  "expected_video_id": "L01_V003",
  "expected_time_s": 92.4,
  "tolerance_s": 3.0
}
```

Báo cáo Recall@K, MRR và temporal error theo từng nhóm query.
