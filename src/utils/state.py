"""Theo dõi các video đã xử lý để tránh làm lại / đăng trùng."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}
        self._data.setdefault("processed", {})

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def is_processed(self, video_id: str) -> bool:
        return video_id in self._data["processed"]

    def mark(self, video_id: str, info: dict[str, Any]) -> None:
        self._data["processed"][video_id] = info
        self._save()

    def get(self, video_id: str) -> dict[str, Any] | None:
        return self._data["processed"].get(video_id)
