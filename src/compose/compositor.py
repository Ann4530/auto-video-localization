"""Ghép video cuối: thay/trộn audio bằng giọng Việt + (tuỳ chọn) burn phụ đề."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..utils.logging import get_logger

log = get_logger("compose")


class Compositor:
    def __init__(self, compose_cfg: dict, keep_original_volume: float = 0.12):
        self.cfg = compose_cfg
        self.keep_original_volume = keep_original_volume

    def video_duration(self, video: Path) -> float:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True,
        )
        try:
            return float(r.stdout.strip())
        except ValueError:
            return 0.0

    def _subtitle_style(self) -> str:
        c = self.cfg
        return (
            f"FontName={c.get('subtitle_font', 'Arial')},"
            f"FontSize={c.get('subtitle_fontsize', 18)},"
            f"PrimaryColour={c.get('subtitle_color', '&H00FFFFFF')},"
            f"OutlineColour={c.get('subtitle_outline_color', '&H00000000')},"
            f"BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=40"
        )

    def compose(
        self,
        video: Path,
        vi_voice: Path,
        srt: Path | None,
        out_path: Path,
    ) -> Path:
        """Tạo video cuối cùng.

        - Trộn giọng Việt (to) + audio gốc (nhỏ, làm nền) bằng amix.
        - Nếu burn_subtitles=True và có srt -> gắn cứng phụ đề.
        """
        burn = bool(self.cfg.get("burn_subtitles", True)) and srt is not None
        kov = self.keep_original_volume

        filter_parts = [
            f"[0:a]volume={kov}[orig]",
            "[1:a]volume=1.0[voice]",
            "[orig][voice]amix=inputs=2:duration=first:normalize=0[aout]",
        ]
        v_map = "0:v"
        if burn:
            srt_escaped = str(srt).replace("\\", "/").replace(":", "\\:")
            filter_parts.append(
                f"[0:v]subtitles='{srt_escaped}':force_style='{self._subtitle_style()}'[vout]"
            )
            v_map = "[vout]"

        filter_complex = ";".join(filter_parts)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", str(vi_voice),
            "-filter_complex", filter_complex,
            "-map", v_map,
            "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
        log.info("Render video cuối -> %s", out_path.name)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            log.error("ffmpeg lỗi:\n%s", proc.stderr[-2000:])
            raise RuntimeError("Render thất bại")
        return out_path
