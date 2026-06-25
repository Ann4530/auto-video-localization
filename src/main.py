"""CLI điều khiển pipeline.

Ví dụ:
  python -m src.main url "https://www.tiktok.com/@x/video/123"
  python -m src.main file "video.mp4" --mode screen_only
  python -m src.main run            # xử lý các nguồn trong config.yaml 1 lần
  python -m src.main watch          # quét định kỳ theo watch_interval_seconds

Các chế độ (--mode) — đây là lối tắt map sang JobOptions:
  voice_transcript : dịch giọng nói + lồng tiếng + HIỆN phụ đề (mặc định)
  voice_only       : dịch giọng nói + lồng tiếng, KHÔNG phụ đề
  screen_only      : CHỈ dịch chữ trên màn hình (giữ audio gốc)
"""
from __future__ import annotations

import argparse
import time

from .config import load_config
from .jobs import JobOptions
from .pipeline import Pipeline
from .utils.logging import get_logger

log = get_logger("main")

MODES = ["voice_transcript", "voice_only", "screen_only"]

# map mode CLI cũ -> (choice, ocr_overlay)
_MODE_MAP = {
    "voice_transcript": ("both", False),
    "voice_only": ("voice", False),
    "screen_only": ("none", True),
}


def _opts_from_mode(cfg, mode: str | None) -> JobOptions:
    mode = mode or cfg.mode or "voice_transcript"
    choice, ocr = _MODE_MAP.get(mode, ("both", False))
    # screen_only thì luôn OCR; 2 mode voice thì theo config.ocr.enabled
    if not ocr:
        ocr = bool(cfg.ocr.get("enabled", False))
    return JobOptions.from_request(
        choice,
        ocr_overlay=ocr,
        target_language=cfg.translate.get("target_language", "Tiếng Việt"),
        voice=cfg.tts.get("voice", "vi-VN-HoaiMyNeural"),
        rate=cfg.tts.get("rate", "+0%"),
    )


def _progress(stage: str, percent: int) -> None:
    log.info("[%3d%%] %s", percent, stage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tự động Việt hoá & đăng video")
    parser.add_argument("--config", default=None, help="Đường dẫn config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_url = sub.add_parser("url", help="Xử lý 1 URL video cụ thể")
    p_url.add_argument("url")
    p_url.add_argument("--mode", choices=MODES, default=None, help="Chế độ xử lý")

    p_file = sub.add_parser("file", help="Xử lý 1 file video CÓ SẴN trên máy")
    p_file.add_argument("path")
    p_file.add_argument("--title", default=None, help="Tiêu đề/caption (tuỳ chọn)")
    p_file.add_argument("--mode", choices=MODES, default=None, help="Chế độ xử lý")

    sub.add_parser("run", help="Xử lý tất cả nguồn trong config 1 lần")
    sub.add_parser("watch", help="Quét nguồn định kỳ")

    args = parser.parse_args()
    cfg = load_config(args.config)
    pipeline = Pipeline(cfg)

    if args.command == "url":
        opts = _opts_from_mode(cfg, args.mode)
        out = pipeline.process_url(args.url, opts, progress=_progress)
        log.info("Kết quả: %s", out)
    elif args.command == "file":
        opts = _opts_from_mode(cfg, args.mode)
        out = pipeline.process_file(args.path, opts, title=args.title, progress=_progress)
        log.info("Kết quả: %s", out)
    elif args.command == "run":
        opts = _opts_from_mode(cfg, None)
        outs = pipeline.process_sources(opts)
        log.info("Đã tạo %d video", len(outs))
    elif args.command == "watch":
        opts = _opts_from_mode(cfg, None)
        log.info("Chế độ watch, chu kỳ %ds. Ctrl+C để dừng.", cfg.watch_interval)
        while True:
            try:
                pipeline.process_sources(opts)
            except KeyboardInterrupt:
                log.info("Dừng.")
                break
            except Exception as e:  # noqa: BLE001
                log.error("Lỗi vòng quét: %s", e)
            time.sleep(cfg.watch_interval)


if __name__ == "__main__":
    main()
