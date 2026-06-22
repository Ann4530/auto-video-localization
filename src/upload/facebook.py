"""Đăng video lên Facebook Page qua Graph API (Resumable Upload / video endpoint)."""
from __future__ import annotations

from pathlib import Path

import requests

from ..utils.logging import get_logger

log = get_logger("upload.facebook")
GRAPH = "https://graph-video.facebook.com/v21.0"


class FacebookUploader:
    def __init__(self, page_id: str, access_token: str):
        self.page_id = page_id
        self.token = access_token

    def available(self) -> bool:
        return bool(self.page_id and self.token)

    def upload(self, video: Path, caption: str) -> dict:
        """Đăng video lên Page. Dùng upload trực tiếp (phù hợp video ngắn)."""
        if not self.available():
            raise ValueError("Thiếu FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN")
        url = f"{GRAPH}/{self.page_id}/videos"
        log.info("Đăng Facebook Page %s", self.page_id)
        with open(video, "rb") as f:
            resp = requests.post(
                url,
                data={"description": caption, "access_token": self.token},
                files={"source": f},
                timeout=600,
            )
        resp.raise_for_status()
        data = resp.json()
        log.info("Đã đăng Facebook, id=%s", data.get("id"))
        return data
