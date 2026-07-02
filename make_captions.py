"""Tạo phụ đề KARAOKE ĐỘNG cho video tự quay rồi burn ra video hoàn chỉnh.

Đây là bước "auto-edit" lõi của pipeline TẠO video (talking-head y khoa).
KHÔNG đụng pipeline dịch. Quy trình:
  footage -> whisper (word-timestamp) -> phụ đề .ass karaoke -> ffmpeg burn.

Chạy thử:
  python make_captions.py "data/input/quay.mp4"
  python make_captions.py "data/input/quay.mp4" --terms "diagnosis,prognosis,symptom"
  python make_captions.py "data/input/quay.mp4" --lang vi --model small
  # kèm 1 thẻ term hiện từ giây 5 đến 9:
  python make_captions.py "data/input/quay.mp4" \
      --term diagnosis --ipa "/ˌdaɪəɡˈnoʊsɪs/" --meaning "chẩn đoán" --card-at 5:9

Mặc định ngôn ngữ footage = "vi" (bạn giảng tiếng Việt, chèn term Anh).
Kết quả lưu vào data/output/<tên>_caption.mp4.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from src.compose.captions import write_karaoke_ass
from src.compose.compositor import Compositor
from src.compose.term_card import render_term_card
from src.transcribe.transcriber import WhisperTranscriber


def video_size(path: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        w, h = r.stdout.strip().split("x")[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return 1080, 1920


def main() -> int:
    ap = argparse.ArgumentParser(description="Phụ đề karaoke động cho video tự quay")
    ap.add_argument("video", help="đường dẫn footage (mp4)")
    ap.add_argument("--lang", default="vi", help="ngôn ngữ đọc (mặc định vi)")
    ap.add_argument("--model", default="base", help="model whisper (base/small/...)")
    ap.add_argument("--terms", default="", help="thuật ngữ Anh cần tô, cách nhau dấu phẩy")
    ap.add_argument("--out", default="", help="file ra (mặc định data/output/<tên>_caption.mp4)")
    ap.add_argument("--max-words", type=int, default=5, help="số từ tối đa mỗi dòng")
    # Thẻ term tuỳ chọn (1 thẻ demo):
    ap.add_argument("--term", default="", help="thẻ term: từ tiếng Anh")
    ap.add_argument("--ipa", default="", help="thẻ term: phiên âm IPA")
    ap.add_argument("--meaning", default="", help="thẻ term: nghĩa tiếng Việt")
    ap.add_argument("--card-at", default="", help="thẻ term hiện khi nào, dạng start:end (giây)")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        print(f"Không thấy file: {video}", file=sys.stderr)
        return 1

    work = Path("data/work") / video.stem
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path("data/output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / f"{video.stem}_caption.mp4"

    vw, vh = video_size(video)
    print(f"[1/3] Bóc lời mức từ (whisper {args.model}, lang={args.lang})...")
    tr = WhisperTranscriber(model=args.model, language=args.lang, word_timestamps=True)
    segments, lang = tr.transcribe(video)
    print(f"      {len(segments)} segment, ngôn ngữ={lang}")
    if not segments:
        print("Không bóc được lời nào.", file=sys.stderr)
        return 2

    terms = {t.strip() for t in args.terms.split(",") if t.strip()}
    ass = write_karaoke_ass(
        segments, work / "captions.ass",
        video_w=vw, video_h=vh, max_words_per_line=args.max_words, terms=terms or None,
    )
    print(f"[2/3] Phụ đề karaoke -> {ass}")

    overlays = []
    if args.term and args.card_at:
        try:
            s, e = (float(x) for x in args.card_at.split(":"))
            overlays.append(render_term_card(
                args.term, args.ipa, args.meaning, work / "term_card.png",
                video_w=vw, video_h=vh, start=s, end=e,
            ))
            print(f"      + thẻ term '{args.term}' [{s}-{e}s]")
        except ValueError:
            print("--card-at sai định dạng (cần start:end), bỏ qua thẻ term.")

    print("[3/3] Burn ra video...")
    comp = Compositor({"burn_subtitles": True})
    comp.compose(video=video, vi_voice=None, srt=None, ass=ass,
                 out_path=out_path, overlays=overlays)
    print(f"XONG -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
