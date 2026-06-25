"""Lưu trữ job trên SQLite (dùng làm hàng đợi giữa API và worker).

Vì sao SQLite làm queue: máy yếu, chạy local, không muốn thêm Redis/broker.
SQLite (WAL) cho phép API ghi/đọc trong khi worker đang xử lý. Sau này muốn
scale thì chỉ cần thay file này + worker/queue.py bằng Redis/RQ.

Chỉ dùng sqlite3 trong thư viện chuẩn — không thêm dependency.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Trạng thái job: queued -> running -> done | failed (| canceled)
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
  id           TEXT PRIMARY KEY,
  source_type  TEXT NOT NULL,        -- 'url' | 'file'
  source_ref   TEXT NOT NULL,        -- url hoặc đường dẫn file upload
  title        TEXT,
  options_json TEXT NOT NULL,        -- JobOptions đã serialize
  state        TEXT NOT NULL,
  stage        TEXT,                 -- tên bước hiện tại
  percent      INTEGER DEFAULT 0,
  output_path  TEXT,
  error        TEXT,
  result_json  TEXT,                 -- kết quả (vd kết quả upload)
  project_id   TEXT,                 -- thuộc project nào
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, created_at);

CREATE TABLE IF NOT EXISTS projects (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  settings_json TEXT,                -- {source_channel, auto_upload[], default_options{}, ...}
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


class JobDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.db_path) as conn:
            conn.executescript(_DDL)
            # Migration: thêm cột project_id nếu DB cũ chưa có (TRƯỚC khi tạo index)
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(jobs)")]
            if "project_id" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN project_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at)")

    # --- ghi ---
    def create(
        self,
        source_type: str,
        source_ref: str,
        title: str | None,
        options: dict[str, Any],
        project_id: str | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        now = _now()
        with _connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO jobs (id, source_type, source_ref, title, options_json,"
                " state, stage, percent, project_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, source_type, source_ref, title,
                 json.dumps(options, ensure_ascii=False),
                 STATE_QUEUED, "queued", 0, project_id, now, now),
            )
        return job_id

    # --- Projects ---
    def create_project(self, name: str, settings: dict | None = None) -> str:
        pid = uuid.uuid4().hex
        now = _now()
        with _connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO projects (id, name, settings_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (pid, name, json.dumps(settings or {}, ensure_ascii=False), now, now),
            )
        return pid

    def update_project(self, pid: str, name: str | None = None,
                       settings: dict | None = None) -> None:
        sets, params = [], []
        if name is not None:
            sets.append("name=?"); params.append(name)
        if settings is not None:
            sets.append("settings_json=?"); params.append(json.dumps(settings, ensure_ascii=False))
        if not sets:
            return
        sets.append("updated_at=?"); params.append(_now())
        params.append(pid)
        with _connect(self.db_path) as conn:
            conn.execute(f"UPDATE projects SET {','.join(sets)} WHERE id=?", params)

    def get_project(self, pid: str) -> dict[str, Any] | None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            return dict(row) if row else None

    def list_projects(self) -> list[dict[str, Any]]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT p.*, "
                " (SELECT COUNT(*) FROM jobs j WHERE j.project_id=p.id) AS job_count "
                "FROM projects p ORDER BY p.created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_project(self, pid: str) -> None:
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM projects WHERE id=?", (pid,))
            conn.execute("UPDATE jobs SET project_id=NULL WHERE project_id=?", (pid,))

    def claim_next(self) -> dict[str, Any] | None:
        """Lấy 1 job queued cũ nhất, chuyển sang running (atomic)."""
        now = _now()
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE jobs SET state=?, stage='starting', updated_at=?"
                " WHERE id = (SELECT id FROM jobs WHERE state=?"
                "             ORDER BY created_at LIMIT 1)"
                " RETURNING *",
                (STATE_RUNNING, now, STATE_QUEUED),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def set_progress(self, job_id: str, stage: str, percent: int) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET stage=?, percent=?, updated_at=? WHERE id=?",
                (stage, int(percent), _now(), job_id),
            )

    def finish(self, job_id: str, output_path: str, result: dict | None = None) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET state=?, stage='done', percent=100,"
                " output_path=?, result_json=?, updated_at=? WHERE id=?",
                (STATE_DONE, output_path,
                 json.dumps(result or {}, ensure_ascii=False), _now(), job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET state=?, stage='failed', error=?, updated_at=? WHERE id=?",
                (STATE_FAILED, error[:4000], _now(), job_id),
            )

    def requeue_stuck(self) -> int:
        """Đưa các job 'running' mồ côi (worker chết giữa chừng) về queued.

        Gọi 1 lần khi worker khởi động."""
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE jobs SET state=?, stage='queued', percent=0, updated_at=?"
                " WHERE state=?",
                (STATE_QUEUED, _now(), STATE_RUNNING),
            )
            return cur.rowcount

    # --- đọc ---
    def get(self, job_id: str) -> dict[str, Any] | None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list(
        self, limit: int = 50, offset: int = 0, state: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM jobs"
        where, params = [], []
        if state:
            where.append("state=?"); params.append(state)
        if project_id:
            where.append("project_id=?"); params.append(project_id)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with _connect(self.db_path) as conn:
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) c FROM jobs GROUP BY state"
            ).fetchall()
            return {r["state"]: r["c"] for r in rows}
