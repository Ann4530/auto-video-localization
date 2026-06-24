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
        # BorderStyle=3 = Ô NỀN ĐẶC ôm sát chữ (không phải thanh kéo ngang màn hình).
        # Ô màu = OutlineColour (trắng), chữ = PrimaryColour (đen). Outline = đệm
        # quanh chữ -> ô to/nhỏ. 1 style duy nhất -> font & cỡ chữ ĐỒNG NHẤT.
        return (
            f"FontName={c.get('subtitle_font', 'Arial')},"
            f"FontSize={c.get('subtitle_fontsize', 21)},"
            f"PrimaryColour={c.get('subtitle_text_color', '&H00000000')},"   # chữ đen
            f"OutlineColour={c.get('subtitle_box_color', '&H00FFFFFF')},"    # ô nền trắng
            f"BackColour={c.get('subtitle_box_color', '&H00FFFFFF')},"
            f"BorderStyle=3,Outline={c.get('subtitle_box_padding', 6)},Shadow=0,"
            f"Alignment=2,MarginV={c.get('subtitle_margin_v', 12)}"
        )

    def _video_filters(self, srt: Path) -> str:
        """Burn phụ đề Việt với ô nền trắng ôm chữ."""
        srt_escaped = str(srt).replace("\\", "/").replace(":", "\\:")
        return f"subtitles='{srt_escaped}':force_style='{self._subtitle_style()}'"

    def compose(
        self,
        video: Path,
        vi_voice: Path,
        srt: Path | None,
        out_path: Path,
        background: Path | None = None,
    ) -> Path:
        """Tạo video cuối cùng.

        - Trộn giọng Việt (to) + audio nền (nhỏ) bằng amix.
        - `background`: track nhạc nền đã tách giọng (no_vocals). Nếu None ->
          dùng audio gốc của video (vẫn còn giọng gốc ở mức nhỏ).
        - Nếu burn_subtitles=True và có srt -> gắn cứng phụ đề.
        """
        burn = bool(self.cfg.get("burn_subtitles", True)) and srt is not None
        kov = self.keep_original_volume

        # Nguồn audio nền: track đã tách giọng (input 2) hoặc audio gốc (input 0).
        bg_label = "[2:a]" if background is not None else "[0:a]"
        filter_parts = [
            f"{bg_label}volume={kov}[orig]",
            "[1:a]volume=1.0[voice]",
            "[orig][voice]amix=inputs=2:duration=first:normalize=0[aout]",
        ]
        v_map = "0:v"
        if burn:
            filter_parts.append(f"[0:v]{self._video_filters(srt)}[vout]")
            v_map = "[vout]"

        filter_complex = ";".join(filter_parts)
        inputs = ["-i", str(video), "-i", str(vi_voice)]
        if background is not None:
            inputs += ["-i", str(background)]
        cmd = [
            "ffmpeg", "-y",
            *inputs,
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
