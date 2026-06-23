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
# Thử 1 video cụ thể qua URL (TikTok chạy tốt; Douyin xem mục dưới)
python -m src.main url "https://www.tiktok.com/@tenkenh/video/123456"

# Xử lý 1 FILE video CÓ SẴN trên máy (cách ổn định nhất cho Douyin)
python -m src.main file "D:\path\video.mp4" --title "Tieu de"

# Xử lý tất cả nguồn trong config.yaml, 1 lần
python -m src.main run

# Quét định kỳ tự động (theo watch_interval_seconds)
python -m src.main watch
```

### Chế độ xử lý (`--mode`)
Chọn cách xử lý cho mỗi video (mặc định đặt ở `config.yaml` mục `mode`, hoặc
override bằng `--mode` trên CLI):

| Mode | Ý nghĩa |
|------|---------|
| `voice_transcript` | Dịch giọng nói + lồng tiếng Việt + **hiện phụ đề** (mặc định) |
| `voice_only` | Dịch giọng nói + lồng tiếng Việt, **không phụ đề** |
| `screen_only` | **Chỉ dịch chữ trên màn hình** (giữ audio gốc, không lồng tiếng) |

```powershell
python -m src.main file "video.mp4" --mode voice_only
python -m src.main file "video.mp4" --mode screen_only
```
OCR đè chữ màn hình bật/tắt riêng ở mục `ocr.enabled`. Ở `screen_only` thì OCR
luôn bật; ở 2 chế độ voice, OCR chạy nếu `ocr.enabled: true`.

### ⚠️ Về Douyin (抖音)
Douyin chặn tải tự động bằng chữ ký `a_bogus` (sinh bởi JavaScript). yt-dlp hiện
**không vượt được** tường này kể cả khi đã có cookies đăng nhập. Vì vậy với Douyin:
- **Tải file về thủ công** (web hỗ trợ tải Douyin, hoặc tiện ích trình duyệt), rồi
  dùng `python -m src.main file "duong_dan.mp4"` để chạy phần còn lại (dịch + lồng
  tiếng + phụ đề + đăng).
- TikTok quốc tế thì `url` chạy bình thường.

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
| `src/ocr/screen_text.py` | OCR chữ trên màn hình → dịch → đè khung trắng + chữ Việt |
| `src/upload/*` | Đăng lên TikTok / Facebook / Instagram |
| `src/pipeline.py` | Điều phối toàn bộ quy trình |
| `src/main.py` | Giao diện dòng lệnh (CLI) |

## 4b. Dịch chữ TRÊN MÀN HÌNH (OCR overlay)

Ngoài phụ đề từ giọng nói, pipeline có thể **dịch chữ hardcoded hiển thị trong
video** (tiêu đề, caption tiếng Trung...) rồi **đè khung trắng + chữ Việt đen**
lên đúng vị trí chữ gốc. Bật/tắt trong `config.yaml` mục `ocr`:

```yaml
ocr:
  enabled: true
  sample_fps: 2.0       # khung/giây để quét (giảm = nhanh hơn, có thể sót)
  only_cjk: true        # chỉ đè chữ Trung/Nhật/Hàn, bỏ qua watermark Latin
  ocr_max_width: 720    # thu nhỏ khung trước khi OCR cho nhanh
  scene_diff: 2.5       # bỏ qua khung trùng cảnh
```

- Dùng **RapidOCR** (ONNX, chạy CPU, nhận chữ Trung+Anh tốt, không cần GPU/PyTorch).
- Tự **gom các lần xuất hiện giống nhau** theo thời gian (chống nhấp nháy/dịch lặp).
- **Cache kết quả quét** ở `data/work/<id>/ocr_scan.json` — lần chạy lại không phải
  quét OCR lại (rất tốn thời gian trên CPU). Xoá file này nếu muốn quét lại.
- ⚠️ **Chậm trên CPU**: ~5-7 phút cho video 2 phút ở 2 fps. Giảm `sample_fps`
  xuống 1.0 để nhanh gấp đôi.

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
