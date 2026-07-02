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

    def video_size(self, video: Path) -> tuple[int, int]:
        """Lấy (width, height) của video. Mặc định 1080x1920 nếu probe lỗi."""
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
             str(video)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            w, h = r.stdout.strip().split("x")[:2]
            return int(w), int(h)
        except (ValueError, IndexError):
            return 1080, 1920

    def compose_voiceover(
        self,
        voice: Path,
        ass: Path,
        out_path: Path,
        *,
        width: int = 720,
        height: int = 1280,
        duration: float,
        bg: str = "0f1220",
        bg_image: Path | None = None,
    ) -> Path:
        """Dựng video cho mode LỒNG TIẾNG AI: nền (màu/ảnh) + giọng + caption ASS.

        Không có video gốc -> tạo nền bằng lavfi (màu) hoặc ảnh tĩnh lặp. Audio
        chỉ là giọng AI. Burn phụ đề karaoke .ass.

        Máy RAM yếu: x264 ở khung dọc lớn dễ 'malloc failed'. Thử nhiều bậc nhẹ
        dần (preset nhanh hơn + hạ độ phân giải) cho tới khi encode được.
        """
        ass_escaped = str(ass).replace("\\", "/").replace(":", "\\:")

        def build(preset: str, w: int, h: int, threads: int) -> list[str]:
            cmd = ["ffmpeg", "-y"]
            if bg_image is not None:
                cmd += ["-loop", "1", "-framerate", "25", "-t", f"{duration:.2f}",
                        "-i", str(bg_image)]
                vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                      f"crop={w}:{h},subtitles='{ass_escaped}'")
            else:
                cmd += ["-f", "lavfi", "-t", f"{duration:.2f}",
                        "-i", f"color=c=0x{bg}:s={w}x{h}:r=25"]
                vf = f"subtitles='{ass_escaped}'"
            cmd += [
                "-i", str(voice), "-vf", vf,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", preset, "-crf", "23",
                "-pix_fmt", "yuv420p", "-threads", str(threads),
                "-c:a", "aac", "-b:a", "160k", "-shortest", str(out_path),
            ]
            return cmd

        # Bậc nhẹ dần: (preset, rộng, cao, luồng). 9:16 giữ nguyên tỉ lệ.
        # Encode trên máy RAM thấp hay fail chập chờn (malloc, hoặc 'Conversion
        # failed' lúc finalize) -> cứ gặp lỗi là thử bậc nhẹ hơn, không phân biệt.
        import gc

        tiers = [
            ("ultrafast", width, height, 1),
            ("ultrafast", 540, 960, 1),
            ("ultrafast", 360, 640, 1),
        ]
        last_err = ""
        for i, (preset, w, h, threads) in enumerate(tiers):
            log.info("Render lồng tiếng AI (bậc %d: %s %dx%d) -> %s",
                     i + 1, preset, w, h, out_path.name)
            gc.collect()
            proc = subprocess.run(build(preset, w, h, threads),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                if i > 0:
                    log.warning("Đã hạ cấp encode xuống %s %dx%d", preset, w, h)
                return out_path
            last_err = (proc.stderr or "")[-1500:]
            log.warning("Encode bậc %d fail, thử bậc nhẹ hơn", i + 1)
        log.error("ffmpeg lỗi (mọi bậc):\n%s", last_err)
        raise RuntimeError("Render lồng tiếng AI thất bại")

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
        ass: Path | None = None,
    ) -> Path:
        """Tạo video cuối cùng.

        - vi_voice != None: trộn giọng Việt (to) + audio gốc (nhỏ, nền).
          vi_voice == None: GIỮ NGUYÊN audio gốc (chế độ chỉ đè chữ màn hình).
        - ass != None: burn phụ đề ĐỘNG karaoke (.ass, dùng style trong file).
          Ưu tiên hơn srt; bỏ qua force_style vì ASS tự mang style.
        - srt != None & burn_subtitles: gắn cứng phụ đề câu tĩnh.
        - overlays: PNG (khung chữ / thẻ term) đè lên video theo thời gian.
        """
        burn = bool(self.cfg.get("burn_subtitles", True)) and srt is not None and ass is None
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
        if ass is not None:
            ass_escaped = str(ass).replace("\\", "/").replace(":", "\\:")
            filter_parts.append(f"{cur}subtitles='{ass_escaped}'[vbase]")
            cur = "[vbase]"
        elif burn:
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
