"""Phụ thuộc dùng chung cho API: config, queue, auth, danh sách giọng đọc.

API process CỐ Ý không import Pipeline (tránh nạp model nặng) — nó chỉ ghi/đọc
job vào hàng đợi.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from fastapi import Header, HTTPException

from src.config import Config, load_config
from worker.queue import Queue, get_queue

# --- Config / Queue singletons ---


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()


def queue() -> Queue:
    return get_queue()


# --- Auth ---
# Quy ước: nếu API_KEY trong .env để TRỐNG -> chế độ local mở (không cần key).
# Khi deploy thì đặt API_KEY, mọi request /api/* phải kèm header X-API-Key.

def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    key = get_config().env("API_KEY")
    if not key:
        return  # local mode, mở
    if x_api_key != key:
        raise HTTPException(status_code=401, detail="API key không hợp lệ")


# --- Danh sách giọng edge-tts (cache) ---
_voices_cache: list[dict[str, Any]] | None = None


async def list_voices(locale: str | None = None) -> list[dict[str, Any]]:
    """Lấy danh sách giọng edge-tts (cache sau lần đầu). locale lọc theo tiền tố."""
    global _voices_cache
    if _voices_cache is None:
        import edge_tts

        try:
            raw = await edge_tts.list_voices()
        except Exception:  # noqa: BLE001 - mạng lỗi -> trả tối thiểu
            raw = []
        _voices_cache = [
            {
                "ShortName": v.get("ShortName", ""),
                "Gender": v.get("Gender", ""),
                "Locale": v.get("Locale", ""),
                "FriendlyName": v.get("FriendlyName", ""),
            }
            for v in raw
        ]
        # fallback nếu rỗng (offline): vài giọng VN phổ biến
        if not _voices_cache:
            _voices_cache = [
                {"ShortName": "vi-VN-HoaiMyNeural", "Gender": "Female",
                 "Locale": "vi-VN", "FriendlyName": "HoaiMy (Nữ)"},
                {"ShortName": "vi-VN-NamMinhNeural", "Gender": "Male",
                 "Locale": "vi-VN", "FriendlyName": "NamMinh (Nam)"},
            ]
    voices = _voices_cache
    if locale:
        loc = locale.lower()
        voices = [v for v in voices if v["Locale"].lower().startswith(loc)]
    return voices


def list_voices_sync(locale: str | None = None) -> list[dict[str, Any]]:
    return asyncio.run(list_voices(locale))
