"""Tải video từ TikTok / Douyin bằng yt-dlp."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp

from ..utils.logging import get_logger

log = get_logger("download")


def normalize_url(url: str) -> str:
    """Chuẩn hoá các dạng URL về dạng yt-dlp hiểu được.

    - Douyin feed:  douyin.com/jingxuan?modal_id=ID  ->  douyin.com/video/ID
    - Douyin user modal: ...?modal_id=ID             ->  douyin.com/video/ID
    """
    if "douyin.com" in url and "modal_id=" in url:
        m = re.search(r"modal_id=(\d+)", url)
        if m:
            return f"https://www.douyin.com/video/{m.group(1)}"
    return url


@dataclass
class VideoItem:
    id: str
    url: str
    title: str
    path: Path           # đường dẫn file video đã tải
    info: dict[str, Any]


class Downloader:
    def __init__(
        self,
        dest_dir: Path,
        fmt: str,
        cookies_file: str | None = None,
        cookies_from_browser: str | None = None,
    ):
        self.dest_dir = dest_dir
        self.fmt = fmt
        self.cookies_file = cookies_file or None
        self.cookies_from_browser = cookies_from_browser or None

    def _ydl_opts(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "format": self.fmt,
            "outtmpl": str(self.dest_dir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": False,
            "merge_output_format": "mp4",
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        elif self.cookies_from_browser:
            # ví dụ "edge" / "chrome" / "firefox"
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        if extra:
            opts.update(extra)
        return opts

    def list_channel_videos(self, channel_url: str, limit: int) -> list[dict[str, Any]]:
        """Lấy metadata các video mới nhất của 1 kênh (chưa tải file)."""
        channel_url = normalize_url(channel_url)
        opts = self._ydl_opts(
            {"extract_flat": "in_playlist", "playlistend": limit}
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
        entries = info.get("entries") if isinstance(info, dict) else None
        if entries is None:  # URL là 1 video đơn lẻ
            return [info]
        return [e for e in entries if e][:limit]

    def download(self, url: str) -> VideoItem:
        """Tải 1 video về đĩa và trả về VideoItem."""
        url = normalize_url(url)
        log.info("Đang tải: %s", url)
        with yt_dlp.YoutubeDL(self._ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=True)
            # với playlist single, info có thể lồng
            if "entries" in info:
                info = info["entries"][0]
            filename = ydl.prepare_filename(info)
        path = Path(filename)
        if not path.exists():
            # yt-dlp có thể đổi ext sau khi merge
            mp4 = path.with_suffix(".mp4")
            path = mp4 if mp4.exists() else path
        return VideoItem(
            id=str(info.get("id")),
            url=info.get("webpage_url", url),
            title=info.get("title") or info.get("description") or info["id"],
            path=path,
            info=info,
        )
