# Báo cáo định hướng Accuracy–Latency cho hệ thống Video Retrieval AIC

## 1. Kết luận điều hành

Thông tin từ người đã tham gia cuộc thi cho thấy điểm của một câu bắt đầu ở mức cao, giảm theo thời gian và có thể dừng khi điểm còn khoảng một nửa. Đây là **kinh nghiệm thực địa, chưa phải văn bản quy chế chính thức**, nhưng đủ mạnh để đặt latency thành yêu cầu thiết kế cấp một.

Hệ thống không nên chọn giữa accuracy và latency. Kiến trúc đúng là:

```text
Fast result sớm
→ người thi có candidate để quan sát
→ refinement chỉ chạy khi cần
→ luôn có local fallback
```

Visual retrieval là fast path mặc định. OCR và ASR được lập chỉ mục offline, sau đó runtime bật theo prompt hoặc manual override. API planner/verifier chỉ dùng cho câu phức tạp hoặc candidate mơ hồ.

## 2. Vấn đề cần giải quyết

### 2.1 Độ chính xác

Hệ thống phải tìm đúng video và timestamp, đồng thời bảo toàn các điều kiện bắt buộc như thương hiệu, biển số, lời nói hoặc thứ tự sự kiện.

### 2.2 Độ trễ

Không chỉ đo thời gian model chạy. End-to-end latency gồm:

- phân tích prompt;
- text embedding;
- visual search;
- OCR/ASR search;
- fusion;
- tải thumbnail;
- verifier/API;
- render UI;
- thời gian người dùng chọn và nộp.

### 2.3 Sự không ổn định của API cloud

Groq hoặc provider khác có thể nhanh ở median nhưng vẫn có tail latency, timeout, rate limit hoặc mất kết nối. API không được là điểm lỗi duy nhất.

## 3. Nguyên tắc thiết kế

1. Visual luôn bật.
2. OCR và ASR mặc định ở trạng thái `Auto`, không phải boolean đơn giản.
3. UI cho phép `Off / Auto / On` để sửa quyết định router.
4. Fast path không gọi vision verifier.
5. Accurate path được phép mở rộng top-k, temporal neighbors và API verifier.
6. Khi API lỗi, hệ thống trả kết quả local thay vì chờ retry dài.
7. Tất cả module ghi latency riêng để biết nút thắt thật.

## 4. Router modality

### 4.1 OCR

Bật OCR khi prompt chứa hoặc ám chỉ:

- chữ, bảng hiệu, biển báo, biển số;
- logo, thương hiệu, nhãn;
- số điện thoại, ngày tháng, giá;
- phụ đề hoặc nội dung màn hình;
- tên riêng, chuỗi in hoa, chuỗi có số hoặc dấu gạch.

Ví dụ `người đánh trống Yamaha` phải bật OCR dù không có từ “chữ”.

### 4.2 ASR

Bật ASR khi nội dung âm thanh là bằng chứng:

- nói, phát biểu, trả lời, hỏi;
- hát, hô, đọc;
- nhắc đến một cụm từ;
- lời thoại hoặc cuộc trò chuyện.

### 4.3 Không hard-disable vĩnh viễn

Một query visual-only có thể chạy visual trước. Khi margin giữa top candidate quá nhỏ hoặc confidence thấp, hệ thống mới bật OCR/ASR fallback.

## 5. Ba profile vận hành

### Fast

- visual luôn bật;
- OCR/ASR chỉ bật ở confidence cao;
- top-k nhỏ;
- không API verifier;
- mục tiêu có gallery đầu tiên sớm nhất.

### Auto

- router quyết định;
- visual/OCR/ASR chạy song song khi cần;
- fallback khi visual mơ hồ;
- profile mặc định khi thi.

### Accurate

- threshold modality thấp hơn;
- top-k lớn;
- temporal expansion;
- có thể gọi planner/verifier;
- dùng khi còn thời gian hoặc query khó.

## 6. Mục tiêu latency nội bộ

Đây là target kỹ thuật, không phải quy định BTC:

| Profile | Target gallery |
|---|---:|
| Fast | ≤ 2 giây |
| Auto | ≤ 5 giây |
| Accurate | ≤ 8 giây |

Cần báo cáo p50, p90, p95, max và timeout rate; không chỉ average.

## 7. Chỉ số accuracy

- Recall@1, Recall@5, Recall@20;
- MRR;
- đúng video;
- temporal error theo giây;
- tỷ lệ OCR anchor đúng;
- tỷ lệ ASR phrase đúng;
- verifier gain và verifier harm;
- số lần nộp sai.

## 8. Thiết kế UI

UI cần có:

- ô prompt;
- profile Fast/Auto/Accurate;
- OCR, ASR, API planner ở trạng thái Off/Auto/On;
- quyết định router và confidence;
- latency breakdown;
- candidate gallery;
- evidence OCR/ASR;
- nút refine hoặc force modality.

Người vận hành phải thấy hệ thống đang bật module nào để sửa ngay khi router hiểu sai.

## 9. Server demo tối thiểu

FastAPI server trong repo cung cấp:

- `GET /api/health`;
- `POST /api/route`;
- `POST /api/search`;
- `POST /api/benchmark`;
- `GET /api/frame/{item_id}`.

Mock mode chạy ngay. Artifact mode đọc:

- visual index manifest;
- embeddings/FAISS;
- OCR SQLite;
- SigLIP2 weights;
- ASR SQLite tùy chọn.

## 10. Tiêu chí hoàn thành MVP

- Mock server khởi động dưới 5 giây.
- Router test đúng các query OCR, ASR và object-only cơ bản.
- Search API trả JSON có route, hits và latency breakdown.
- UI GitHub Pages gọi được backend qua URL cấu hình.
- Artifact adapter không chạy lại embedding hoặc OCR.
- Khi ASR chưa có, server cảnh báo nhưng vẫn trả visual/OCR.
- Khi API planner tắt hoặc lỗi, local retrieval vẫn hoạt động.
