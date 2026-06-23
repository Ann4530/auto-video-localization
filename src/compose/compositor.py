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
            capture_output=True, text=True, encoding="utf-8", errors="replace",
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
        vi_voice: Path | None,
        srt: Path | None,
        out_path: Path,
        overlays: list | None = None,
    ) -> Path:
        """Tạo video cuối cùng.

        - vi_voice != None: trộn giọng Việt (to) + audio gốc (nhỏ, nền).
          vi_voice == None: GIỮ NGUYÊN audio gốc (chế độ chỉ đè chữ màn hình).
        - srt != None & burn_subtitles: gắn cứng phụ đề.
        - overlays: PNG khung trắng + chữ Việt đè lên chữ gốc theo thời gian.
        """
        burn = bool(self.cfg.get("burn_subtitles", True)) and srt is not None
        kov = self.keep_original_volume
        overlays = overlays or []

        inputs: list[str] = ["-i", str(video)]
        filter_parts: list[str] = []

        # --- Audio ---
        if vi_voice is not None:
            inputs += ["-i", str(vi_voice)]
            voice_idx = 1
            next_input = 2
            filter_parts += [
                f"[0:a]volume={kov}[orig]",
                f"[{voice_idx}:a]volume=1.0[voice]",
                "[orig][voice]amix=inputs=2:duration=first:normalize=0[aout]",
            ]
            a_map = "[aout]"
        else:
            next_input = 1
            a_map = "0:a?"   # giữ audio gốc (nếu có)

        # --- Video: subtitles -> đè từng overlay ---
        cur = "[0:v]"
        if burn:
            srt_escaped = str(srt).replace("\\", "/").replace(":", "\\:")
            filter_parts.append(
                f"{cur}subtitles='{srt_escaped}':force_style='{self._subtitle_style()}'[vbase]"
            )
            cur = "[vbase]"

        for idx, ov in enumerate(overlays):
            in_i = next_input + idx
            inputs += ["-i", str(ov.png)]
            out_label = f"[v{idx}]"
            filter_parts.append(
                f"{cur}[{in_i}:v]overlay={ov.x}:{ov.y}:"
                f"enable='between(t,{ov.start:.2f},{ov.end:.2f})'{out_label}"
            )
            cur = out_label

        v_map = "0:v" if cur == "[0:v]" else cur

        cmd = ["ffmpeg", "-y", *inputs]
        if filter_parts:
            cmd += ["-filter_complex", ";".join(filter_parts)]
        cmd += [
            "-map", v_map,
            "-map", a_map,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
        log.info("Render video cuối -> %s", out_path.name)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if proc.returncode != 0:
            log.error("ffmpeg lỗi:\n%s", proc.stderr[-2000:])
            raise RuntimeError("Render thất bại")
        return out_path
