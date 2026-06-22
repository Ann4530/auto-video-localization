# Video Workflow — Tự động Việt hoá & đăng video

Tự động: **tải video (TikTok / Douyin) → bóc lời → dịch sang tiếng Việt → lồng tiếng + phụ đề → đăng lên TikTok / Facebook / Instagram**.

```
Tải (yt-dlp) ─▶ Bóc lời (faster-whisper) ─▶ Dịch (Claude API)
        │                                          │
        ▼                                          ▼
   Lồng tiếng (edge-tts)  ◀── Phụ đề .srt ──▶ Ghép video (ffmpeg) ─▶ Đăng
```

## ⚠️ Lưu ý bản quyền
Tải lại video của kênh khác rồi đăng lên nền tảng của bạn **có thể vi phạm bản quyền và điều khoản nền tảng**. Chỉ dùng cho:
- Kênh **bạn sở hữu**, hoặc
- Nội dung **có giấy phép / được cho phép**, hoặc
- Nội dung do bạn tự sản xuất.
Việc đảm bảo quyền sử dụng nội dung là trách nhiệm của bạn.

## 1. Cài đặt

**Yêu cầu hệ thống:**
- Python 3.10+
- **ffmpeg** + **ffprobe** có trong PATH — tải tại https://ffmpeg.org/download.html
  (Windows: `winget install Gyan.FFmpeg` hoặc `choco install ffmpeg`)

```powershell
cd d:\anhhp\Project\Video-workflow
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Cấu hình

```powershell
copy .env.example .env      # rồi điền API key / token
```
- **ANTHROPIC_API_KEY** (bắt buộc, để dịch) — lấy tại https://console.anthropic.com/
- Token TikTok / Facebook / Instagram chỉ cần khi bật đăng tự động.

Sửa `config.yaml`:
- `sources`: dán URL kênh hoặc URL video cần xử lý.
- `transcribe.model`: `small` (nhanh) → `large-v3` (chính xác nhất).
- `tts.voice`: giọng đọc tiếng Việt (`vi-VN-HoaiMyNeural` nữ / `vi-VN-NamMinhNeural` nam).
- `upload`: bật `true` từng nền tảng khi đã có token. Để `false` = chỉ xuất file ra `data/output/`.

## 3. Chạy

```powershell
# Thử 1 video cụ thể (khuyến nghị chạy thử trước)
python -m src.main url "https://www.tiktok.com/@tenkenh/video/123456"

# Xử lý tất cả nguồn trong config.yaml, 1 lần
python -m src.main run

# Quét định kỳ tự động (theo watch_interval_seconds)
python -m src.main watch
```

Video kết quả nằm ở `data/output/<id>_vi.mp4`. Trạng thái đã xử lý lưu ở `data/state.json` (tránh làm trùng).

## 4. Cấu trúc code

| Module | Chức năng |
|--------|-----------|
| `src/download/downloader.py` | Tải video & liệt kê video mới của kênh (yt-dlp) |
| `src/transcribe/transcriber.py` | Bóc lời + timestamp (faster-whisper) |
| `src/translate/translator.py` | Dịch sang tiếng Việt, giữ khớp segment (Claude) |
| `src/compose/subtitles.py` | Tạo file phụ đề `.srt` |
| `src/tts/synthesizer.py` | Lồng tiếng Việt, đồng bộ thời gian (edge-tts + ffmpeg) |
| `src/compose/compositor.py` | Trộn audio + burn phụ đề, render video cuối |
| `src/upload/*` | Đăng lên TikTok / Facebook / Instagram |
| `src/pipeline.py` | Điều phối toàn bộ quy trình |
| `src/main.py` | Giao diện dòng lệnh (CLI) |

## 5. Ghi chú quan trọng

- **Instagram Graph API** chỉ nhận video qua **URL công khai**, không upload file trực tiếp. Cần host video (S3/Cloudinary…) rồi truyền `video_url`. Xem `src/upload/instagram.py`.
- **TikTok**: mặc định đặt `privacy_level=SELF_ONLY` để test an toàn. Đổi sang `PUBLIC_TO_EVERYONE` trong `src/upload/tiktok.py` khi sẵn sàng. App phải được duyệt quyền `video.publish`.
- **Tốc độ**: lần đầu chạy whisper sẽ tải model về. Có GPU (CUDA) thì đặt `device: cuda`, `compute_type: float16` trong config để nhanh hơn nhiều.
- **Đồng bộ lồng tiếng**: giọng Việt dài hơn câu gốc sẽ được tăng tốc tối đa 2x cho khớp. Video càng nhiều thoại nhanh càng khó khớp tuyệt đối.

## 6. Hướng phát triển tiếp
- Thêm nguồn YouTube (yt-dlp hỗ trợ sẵn).
- Tự động host video để đăng Instagram.
- Tách giọng nói khỏi nhạc nền (Demucs) để lồng tiếng sạch hơn.
- Hàng đợi xử lý song song nhiều video.
