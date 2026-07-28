# Đưa UI lên GitHub Pages

UI nằm hoàn toàn trong thư mục `docs/`, không cần npm hoặc React build.

## 1. Tạo repository

```bash
git init
git add .
git commit -m "Add AIC latency-first demo"
git branch -M main
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git push -u origin main
```

## 2. Bật GitHub Pages

Trong repository:

1. Mở **Settings**.
2. Chọn **Pages**.
3. Source: **Deploy from a branch**.
4. Branch: `main`.
5. Folder: `/docs`.

## 3. Kết nối backend

GitHub Pages chỉ host frontend. FastAPI phải chạy ở một URL HTTPS công khai.
Nhập URL đó vào ô **Server API** của UI, hoặc mở trang bằng query parameter:

```text
https://<username>.github.io/<repo>/?api=https://<backend-domain>
```

Backend phải cho phép CORS từ domain GitHub Pages. Trong demo có thể dùng:

```bash
AIC_CORS_ORIGINS=https://<username>.github.io
```

Không nên gọi backend HTTP từ GitHub Pages HTTPS vì trình duyệt sẽ chặn mixed content.

## 4. Quy tắc UI khi thi

- `Auto` là mặc định.
- `Fast` dùng khi cần candidate tức thời.
- `Accurate` dùng khi query khó hoặc còn thời gian.
- OCR/ASR có ba trạng thái `Off / Auto / On`.
- Router decision và latency phải luôn hiển thị, không ẩn trong log.
