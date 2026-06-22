"""Đăng video lên TikTok qua Content Posting API (FILE_UPLOAD).

Quy trình:
  1. POST /v2/post/publish/video/init/  -> nhận publish_id + upload_url
  2. PUT file video lên upload_url (chunk).
  3. (Tuỳ chọn) Poll /v2/post/publish/status/fetch/ để biết trạng thái.

Cần đăng ký app tại https://developers.tiktok.com/ và xin quyền
video.publish. Token lấy qua OAuth (TIKTOK_ACCESS_TOKEN).
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from ..utils.logging import get_logger

log = get_logger("upload.tiktok")
API = "https://open.tiktokapis.com/v2"


class TikTokUploader:
    def __init__(self, access_token: str):
        self.token = access_token

    def available(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def upload(self, video: Path, caption: str) -> dict:
        if not self.available():
            raise ValueError("Thiếu TIKTOK_ACCESS_TOKEN")

        size = os.path.getsize(video)
        # TikTok yêu cầu chia chunk; với video ngắn dùng 1 chunk = cả file
        init = requests.post(
            f"{API}/post/publish/video/init/",
            headers=self._headers(),
            json={
                "post_info": {
                    "title": caption[:150],
                    "privacy_level": "SELF_ONLY",  # đổi sang PUBLIC_TO_EVERYONE khi sẵn sàng
                    "disable_comment": False,
                    "disable_duet": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            },
            timeout=120,
        )
        init.raise_for_status()
        data = init.json()["data"]
        publish_id = data["publish_id"]
        upload_url = data["upload_url"]
        log.info("TikTok init OK, publish_id=%s", publish_id)

        # Bước 2: PUT file
        with open(video, "rb") as f:
            content = f.read()
        put = requests.put(
            upload_url,
            headers={
                "Content-Range": f"bytes 0-{size - 1}/{size}",
                "Content-Type": "video/mp4",
            },
            data=content,
            timeout=600,
        )
        put.raise_for_status()
        log.info("Đã tải video lên TikTok (publish_id=%s)", publish_id)
        return {"publish_id": publish_id}
