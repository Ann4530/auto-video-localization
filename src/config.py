"""Đọc cấu hình từ config.yaml + biến môi trường (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = ROOT

    # Thư mục dữ liệu
    downloads_dir: Path = field(init=False)
    work_dir: Path = field(init=False)
    output_dir: Path = field(init=False)
    state_file: Path = field(init=False)

    def __post_init__(self) -> None:
        data = self.root / "data"
        self.downloads_dir = data / "downloads"
        self.work_dir = data / "work"
        self.output_dir = data / "output"
        self.state_file = data / "state.json"
        for d in (self.downloads_dir, self.work_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)

    # Truy cập nhanh các nhánh cấu hình
    @property
    def sources(self) -> list[str]:
        return self.raw.get("sources", []) or []

    @property
    def download(self) -> dict[str, Any]:
        return self.raw.get("download", {})

    @property
    def transcribe(self) -> dict[str, Any]:
        return self.raw.get("transcribe", {})

    @property
    def translate(self) -> dict[str, Any]:
        return self.raw.get("translate", {})

    @property
    def tts(self) -> dict[str, Any]:
        return self.raw.get("tts", {})

    @property
    def compose(self) -> dict[str, Any]:
        return self.raw.get("compose", {})

    @property
    def ocr(self) -> dict[str, Any]:
        return self.raw.get("ocr", {})

    @property
    def upload(self) -> dict[str, Any]:
        return self.raw.get("upload", {})

    @property
    def mode(self) -> str:
        return self.raw.get("mode", "voice_transcript")

    @property
    def watch_interval(self) -> int:
        return int(self.raw.get("watch_interval_seconds", 1800))

    # Biến môi trường
    def env(self, key: str, default: str = "") -> str:
        return os.getenv(key, default)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else (ROOT / "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config(raw=raw)
