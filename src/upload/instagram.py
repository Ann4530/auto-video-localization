"""Đăng Reels lên Instagram qua Graph API.

Quy trình Graph API gồm 2 bước:
  1. Tạo media container (truyền URL video công khai + caption).
  2. Publish container.

LƯU Ý: Instagram yêu cầu video phải truy cập được qua 1 URL công khai
(không nhận upload file trực tiếp). Bạn cần đẩy file lên 1 nơi có URL
(S3, Cloudinary, hosting...) rồi truyền video_url vào đây.
"""
from __future__ import annotations

import time

import requests

from ..utils.logging import get_logger

log = get_logger("upload.instagram")
GRAPH = "https://graph.facebook.com/v21.0"


class InstagramUploader:
    def __init__(self, ig_user_id: str, access_token: str):
        self.ig_user_id = ig_user_id
        self.token = access_token

    def available(self) -> bool:
        return bool(self.ig_user_id and self.token)

    def upload(self, video_url: str, caption: str) -> dict:
        if not self.available():
            raise ValueError("Thiếu IG_USER_ID / IG_ACCESS_TOKEN")

        # Bước 1: tạo container
        create = requests.post(
            f"{GRAPH}/{self.ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": self.token,
            },
            timeout=120,
        )
        create.raise_for_status()
        container_id = create.json()["id"]
        log.info("Tạo IG container %s, chờ xử lý...", container_id)

        # Bước 2: chờ container sẵn sàng
        for _ in range(30):
            status = requests.get(
                f"{GRAPH}/{container_id}",
                params={"fields": "status_code", "access_token": self.token},
                timeout=60,
            ).json()
            if status.get("status_code") == "FINISHED":
                break
            if status.get("status_code") == "ERROR":
                raise RuntimeError(f"IG xử lý lỗi: {status}")
            time.sleep(5)

        # Bước 3: publish
        publish = requests.post(
            f"{GRAPH}/{self.ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": self.token},
            timeout=120,
        )
        publish.raise_for_status()
        data = publish.json()
        log.info("Đã đăng Instagram Reels, id=%s", data.get("id"))
        return data
