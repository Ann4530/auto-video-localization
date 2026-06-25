# Localize Studio — Web app

Biến pipeline dịch video thành website: upload/dán-link → chọn dịch phụ đề /
lồng tiếng / cả hai → xem tiến trình → tải kết quả. Kèm tự động hoá kênh qua n8n.

## Kiến trúc (3 tiến trình)

```
 Trình duyệt / n8n
        │  HTTP
        ▼
   ┌──────────┐   ghi/đọc job   ┌───────────┐   lấy job   ┌────────────┐
   │   API    │ ──────────────► │ SQLite    │ ──────────► │  Worker    │
   │ FastAPI  │   data/jobs.db  │ (hàng đợi)│             │ pipeline   │
   │ (nhẹ)    │ ◄────────────── │           │ ◄────────── │ (nặng,RAM) │
   └──────────┘   trạng thái    └───────────┘  cập nhật   └────────────┘
```

- **API** (`web/app.py`): phục vụ UI + REST, KHÔNG nạp model → nhẹ.
- **Worker** (`worker/run.py`): chạy whisper/dịch/edge-tts/ffmpeg, concurrency=1,
  model nạp 1 lần. Đây là phần sau này tách lên VPS/GPU.
- **SQLite** (`data/jobs.db`, WAL) làm hàng đợi. Đổi sang Redis/RQ chỉ cần sửa
  `worker/queue.py`.

## Cài đặt

```bash
pip install -r requirements.txt      # đã có fastapi, uvicorn, jinja2, python-multipart
cp .env.example .env                  # rồi điền GEMINI_API_KEY (bắt buộc để dịch)
```
Cần sẵn **ffmpeg** trong PATH.

## Chạy (mở 2 cửa sổ terminal)

```bash
# 1) API + giao diện web
uvicorn web.app:app --host 127.0.0.1 --port 8000
#    -> mở http://127.0.0.1:8000

# 2) Worker xử lý job
python -m worker.run
```
Trên Windows có thể bấm đúp `run_web.bat` và `run_worker.bat`.

## Tuỳ chọn dịch (trên web & API)

| choice | Phụ đề | Lồng tiếng |
|--------|:-----:|:----------:|
| `text` | ✅ | ❌ |
| `voice`| ❌ | ✅ |
| `both` | ✅ | ✅ |

Thêm: `ocr_overlay` (dịch chữ cứng trên màn hình), `voice` (giọng edge-tts),
`rate`, `keep_original_volume`, `target_language`.

### Bóc lời: whisper (local) hay Gemini (cloud)
Trong `config.yaml` → `transcribe.provider`:
- `whisper`: chạy local, offline, nhưng ngốn RAM (máy yếu dùng model `base`).
- `gemini`: đẩy audio lên Gemini API → **không tốn RAM**, ~1500 lượt free/ngày,
  dùng chung `GEMINI_API_KEY`. **Khuyến nghị cho máy yếu.**

## API chính (cho n8n)

Khi `API_KEY` trong `.env` được đặt, mọi `/api/*` cần header `X-API-Key`.

| Method | Path | Mô tả |
|--------|------|------|
| POST | `/api/jobs/upload` | tạo job từ file (multipart) |
| POST | `/api/jobs/url` | tạo job từ link (JSON) |
| GET  | `/api/jobs/{id}` | trạng thái + % |
| GET  | `/api/jobs/{id}/result` | tải video kết quả |
| GET  | `/api/voices?locale=vi` | danh sách giọng edge-tts |
| GET  | `/api/channels/scan?channel=&limit=` | liệt kê video mới của kênh |
| POST | `/api/channels/scan` | quét + tạo job cho video mới |
| POST | `/api/jobs/{id}/upload` | đăng kết quả lên nền tảng |
| GET  | `/healthz` | tình trạng + số job |

## Tự động hoá kênh bằng n8n

```bash
docker compose up -d           # mở http://localhost:5678
```
Import `n8n/channel-automation.workflow.json`, sửa URL kênh nguồn, kích hoạt.
Luồng: Cron → quét kênh → lọc video mới → tạo job → poll → đăng kênh đích.

⚠️ **Douyin không tải tự động được** (chặn `a_bogus`). Với Douyin: tải file thủ
công rồi dùng trang **Upload** trên web — không dùng workflow tự động.

## Mở web ra internet (link công khai để dùng thật)

⚠️ **Phần xử lý nặng (whisper/ffmpeg) bắt buộc chạy trên máy có CPU/ffmpeg** — KHÔNG
chạy được trên Supabase/Vercel/Netlify (chỉ host web tĩnh + serverless ngắn). Vì vậy:

### Cách 1 — Cloudflare Tunnel (rẻ nhất, dùng ngay) ✅ khuyến nghị
Giữ API + worker chạy trên PC của bạn, mở ra internet qua tunnel:
```
1) Đặt APP_PASSWORD trong .env  (BẮT BUỘC — kẻo người lạ xài ké máy + key)
2) Chạy run_web.bat + run_worker.bat
3) Chạy run_tunnel.bat  -> in ra link https://....trycloudflare.com
```
Mở link đó trên bất kỳ máy/điện thoại nào, đăng nhập bằng APP_PASSWORD là dùng được.
- Link **quick tunnel đổi mỗi lần chạy**. Muốn **link cố định** (vd `video.tenban.com`):
  cần tài khoản Cloudflare + 1 domain, tạo *named tunnel* (`cloudflared tunnel login` →
  `cloudflared tunnel create` → trỏ DNS). Miễn phí.
- PC phải bật khi dùng. Tốc độ phụ thuộc mạng nhà bạn.

### Cách 2 — VPS/Cloud (ổn định 24/7, tốn phí)
Thuê VPS (vd 4 vCPU/8GB) có ffmpeg, chạy `run_web` + `run_worker` ở đó, gắn domain.
Whisper trên CPU VPS vẫn nặng → cân nhắc `transcribe.provider: gemini` để đỡ tải.

### Supabase dùng vào việc gì?
Supabase = **database + auth + storage**, KHÔNG chạy được pipeline. Có thể thêm sau để:
- **Storage**: đẩy video kết quả lên Supabase Storage → có link CDN công khai (tiện
  chia sẻ + Instagram yêu cầu public URL). *(chưa tích hợp — báo nếu bạn muốn làm)*
- **Auth/DB**: thay đăng nhập mật khẩu đơn giản bằng Supabase Auth, lưu lịch sử job.

Tóm lại: **link web thật = Cloudflare Tunnel (giờ) hoặc VPS (sau)**; Supabase chỉ bổ trợ
storage/auth, không thay được máy chạy ffmpeg.

## CLI cũ vẫn dùng được
```bash
python -m src.main file video.mp4 --mode voice_transcript
python -m src.main url "https://tiktok.com/@x/video/123"
```
