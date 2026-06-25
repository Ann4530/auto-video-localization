"""Lớp hàng đợi mỏng bọc quanh JobDB.

Đây là RANH GIỚI để sau này đổi sang Redis/RQ: chỉ cần viết lại class Queue với
cùng các method (enqueue/claim/set_progress/complete/fail/get/list), phần API và
worker không phải sửa.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import ROOT
from src.jobs import JobOptions

from .db import JobDB

# Mặc định: data/jobs.db cạnh state.json
DEFAULT_DB_PATH = ROOT / "data" / "jobs.db"


class Queue:
    def __init__(self, db_path: Path | None = None):
        self.db = JobDB(db_path or DEFAULT_DB_PATH)

    def enqueue(
        self,
        source_type: str,
        source_ref: str,
        opts: JobOptions,
        title: str | None = None,
        project_id: str | None = None,
    ) -> str:
        return self.db.create(source_type, source_ref, title, opts.to_dict(),
                              project_id=project_id)

    # --- Projects (uỷ quyền cho JobDB) ---
    def create_project(self, name: str, settings: dict | None = None) -> str:
        return self.db.create_project(name, settings)

    def update_project(self, pid: str, name=None, settings=None) -> None:
        self.db.update_project(pid, name, settings)

    def get_project(self, pid: str):
        return self.db.get_project(pid)

    def list_projects(self):
        return self.db.list_projects()

    def delete_project(self, pid: str) -> None:
        self.db.delete_project(pid)

    def ensure_default_project(self) -> str:
        """Lấy (hoặc tạo) project 'Mặc định' để mọi job đều thuộc 1 project."""
        for p in self.db.list_projects():
            if p["name"] == "Mặc định":
                return p["id"]
        return self.db.create_project("Mặc định", {})

    def claim(self) -> dict[str, Any] | None:
        return self.db.claim_next()

    def set_progress(self, job_id: str, stage: str, percent: int) -> None:
        self.db.set_progress(job_id, stage, percent)

    def complete(self, job_id: str, output_path: str, result: dict | None = None) -> None:
        self.db.finish(job_id, output_path, result)

    def fail(self, job_id: str, error: str) -> None:
        self.db.fail(job_id, error)

    def requeue_stuck(self) -> int:
        return self.db.requeue_stuck()

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.db.get(job_id)

    def list(self, limit: int = 50, offset: int = 0, state: str | None = None,
             project_id: str | None = None):
        return self.db.list(limit, offset, state, project_id)

    def counts(self) -> dict[str, int]:
        return self.db.counts()


_queue: Queue | None = None


def get_queue() -> Queue:
    """Singleton dùng chung cho API process."""
    global _queue
    if _queue is None:
        _queue = Queue()
    return _queue
