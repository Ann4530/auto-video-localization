"""CLI điều khiển pipeline.

Ví dụ:
  python -m src.main url "https://www.tiktok.com/@x/video/123"
  python -m src.main run            # xử lý các nguồn trong config.yaml 1 lần
  python -m src.main watch          # quét định kỳ theo watch_interval_seconds
"""
from __future__ import annotations

import argparse
import time

from .config import load_config
from .pipeline import Pipeline
from .utils.logging import get_logger

log = get_logger("main")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tự động Việt hoá & đăng video")
    parser.add_argument("--config", default=None, help="Đường dẫn config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_url = sub.add_parser("url", help="Xử lý 1 URL video cụ thể")
    p_url.add_argument("url")

    sub.add_parser("run", help="Xử lý tất cả nguồn trong config 1 lần")
    sub.add_parser("watch", help="Quét nguồn định kỳ")

    args = parser.parse_args()
    cfg = load_config(args.config)
    pipeline = Pipeline(cfg)

    if args.command == "url":
        out = pipeline.process_url(args.url)
        log.info("Kết quả: %s", out)
    elif args.command == "run":
        outs = pipeline.process_sources()
        log.info("Đã tạo %d video", len(outs))
    elif args.command == "watch":
        log.info("Chế độ watch, chu kỳ %ds. Ctrl+C để dừng.", cfg.watch_interval)
        while True:
            try:
                pipeline.process_sources()
            except KeyboardInterrupt:
                log.info("Dừng.")
                break
            except Exception as e:  # noqa: BLE001
                log.error("Lỗi vòng quét: %s", e)
            time.sleep(cfg.watch_interval)


if __name__ == "__main__":
    main()
