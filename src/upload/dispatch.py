"""Điều phối đăng video lên các nền tảng — dùng chung cho cả pipeline và web API.

Tách ra từ Pipeline._upload để n8n (qua endpoint /api/jobs/{id}/upload) và
pipeline tự động đều gọi cùng một chỗ.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..utils.logging import get_logger
from .facebook import FacebookUploader
from .instagram import InstagramUploader
from .tiktok import TikTokUploader

if TYPE_CHECKING:
    from ..config import Config

log = get_logger("upload")


def upload_result(
    cfg: "Config",
    video: Path,
    targets: list[str],
    caption: str,
) -> dict:
    """Đăng `video` lên các `targets` (vd ["tiktok","facebook"]).

    Trả về dict {platform: kết-quả|lỗi}. Lỗi 1 nền tảng không làm hỏng nền
    tảng khác.
    """
    result: dict = {}
    targets = [t.lower() for t in (targets or [])]

    if "tiktok" in targets:
        try:
            tt = TikTokUploader(cfg.env("TIKTOK_ACCESS_TOKEN"))
            result["tiktok"] = tt.upload(video, caption)
        except Exception as e:  # noqa: BLE001
            log.error("Đăng TikTok lỗi: %s", e)
            result["tiktok"] = {"error": str(e)}

    if "facebook" in targets:
        try:
            fb = FacebookUploader(
                cfg.env("FB_PAGE_ID"), cfg.env("FB_PAGE_ACCESS_TOKEN")
            )
            result["facebook"] = fb.upload(video, caption)
        except Exception as e:  # noqa: BLE001
            log.error("Đăng Facebook lỗi: %s", e)
            result["facebook"] = {"error": str(e)}

    if "instagram" in targets:
        # IG cần URL công khai của video, không nhận file trực tiếp.
        log.warning(
            "Instagram cần URL công khai của video. Hãy host file rồi gọi "
            "InstagramUploader.upload(video_url, caption)."
        )
        result["instagram"] = {"skipped": "cần public URL"}

    return result
