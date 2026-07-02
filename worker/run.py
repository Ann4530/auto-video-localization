"""Worker: vòng lặp lấy job từ hàng đợi và chạy pipeline.

Chạy:  python -m worker.run

Concurrency = 1 (vòng lặp đơn) là CỐ Ý: máy yếu, model whisper/OCR ngốn RAM,
nên xử lý tuần tự. Một Pipeline duy nhất -> model nạp 1 lần, dùng lại cho mọi
job. Đây là tiến trình DUY NHẤT cần RAM/GPU; sau này tách lên VPS/GPU chính là nó.
"""
from __future__ import annotations

import gc
import json
import time

from src.config import load_config
from src.jobs import JobOptions
from src.pipeline import Pipeline
from src.utils.logging import get_logger

from .queue import Queue

log = get_logger("worker")

POLL_SECONDS = 3.0


def run_one(pipeline: Pipeline, queue: Queue, row: dict) -> None:
    job_id = row["id"]
    opts = JobOptions.from_dict(json.loads(row["options_json"]))

    def progress(stage: str, percent: int) -> None:
        queue.set_progress(job_id, stage, percent)

    log.info("Bắt đầu job %s (%s: %s)", job_id, row["source_type"], row["source_ref"])
    try:
        if row["source_type"] == "script":
            out = pipeline.process_script(
                row["source_ref"], opts, title=row.get("title"), progress=progress,
            )
        elif row["source_type"] == "url":
            out = pipeline.process_url(
                row["source_ref"], opts, progress=progress,
                skip_state=bool(opts.segments_path),
            )
        else:
            out = pipeline.process_file(
                row["source_ref"], opts, title=row.get("title"),
                progress=progress, skip_state=True,
            )
        if out is None:
            queue.fail(job_id, "Pipeline không trả về kết quả")
        else:
            queue.complete(job_id, str(out))
            log.info("Xong job %s -> %s", job_id, out)
    except Exception as e:  # noqa: BLE001
        log.exception("Job %s lỗi", job_id)
        queue.fail(job_id, str(e))
    finally:
        gc.collect()  # giải phóng buffer whisper/OCR sau mỗi job


def main() -> None:
    cfg = load_config()
    queue = Queue()
    n = queue.requeue_stuck()
    if n:
        log.info("Đưa %d job 'running' mồ côi về hàng đợi", n)
    pipeline = Pipeline(cfg)  # model nạp lười ở job đầu tiên
    log.info("Worker sẵn sàng. Poll mỗi %.0fs. Ctrl+C để dừng.", POLL_SECONDS)
    while True:
        try:
            row = queue.claim()
        except Exception as e:  # noqa: BLE001
            log.error("Lỗi đọc hàng đợi: %s", e)
            time.sleep(POLL_SECONDS)
            continue
        if not row:
            time.sleep(POLL_SECONDS)
            continue
        run_one(pipeline, queue, row)


if __name__ == "__main__":
    main()
