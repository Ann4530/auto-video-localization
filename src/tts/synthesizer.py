"""Tạo audio lồng tiếng Việt từ các segment đã dịch, bằng edge-tts.

Cách làm để giữ đồng bộ với video (thuật toán con trỏ thời gian):
  1. Với mỗi segment, tạo 1 file audio tiếng Việt.
  2. Dùng 1 con trỏ `cursor` = thời điểm kết thúc đoạn vừa đặt. Đoạn kế tiếp
     bắt đầu tại max(start gốc, cursor) -> KHÔNG bao giờ chồng lên đoạn trước.
  3. Nếu giọng Việt dài hơn khoảng trống tới đoạn sau -> tăng tốc (atempo,
     giữ cao độ) cho vừa, tối đa `max_speed`. Phần tràn được đẩy sang sau và
     tự re-sync lại mỗi khi gặp khoảng lặng -> không trôi tích luỹ vô hạn.
  4. Ghép tất cả thành 1 file audio dài bằng video.

Yêu cầu: ffmpeg phải có sẵn trong PATH.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import edge_tts

from ..transcribe.transcriber import Segment
from ..utils.logging import get_logger

log = get_logger("tts")


class Synthesizer:
    def __init__(
        self,
        voice: str = "vi-VN-HoaiMyNeural",
        rate: str = "+0%",
        work_dir: Path | None = None,
        max_speed: float = 2.0,
    ):
        self.voice = voice
        self.rate = rate
        self.work_dir = work_dir or Path(".")
        # atempo của ffmpeg chỉ chạy 0.5–2.0 mỗi lần -> giới hạn ở 2.0 cho an toàn.
        self.max_speed = max(1.0, min(max_speed, 2.0))

    async def _tts_one(self, text: str, out: Path) -> None:
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
        await communicate.save(str(out))

    def _duration(self, path: Path) -> float:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True,
        )
        try:
            return float(r.stdout.strip())
        except ValueError:
            return 0.0

    def _fit_to_room(self, src: Path, dur: float, room: float, dst: Path) -> float:
        """Đặt audio vào khoảng trống `room` (giây), tăng tốc nếu cần (giữ pitch).

        Trả về thời lượng audio SAU khi xử lý (để con trỏ thời gian tiến đúng).
        """
        if dur <= 0:
            self._copy(src, dst)
            return dur
        if room <= 0:
            # Đã bị trôi (cursor vượt mốc đoạn sau) -> nén tối đa để bắt kịp.
            speed = self.max_speed
        elif dur > room:
            speed = min(dur / room, self.max_speed)
        else:
            speed = 1.0

        if speed <= 1.0:
            self._copy(src, dst)
            return dur
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={speed:.3f}",
             str(dst)],
            capture_output=True,
        )
        return dur / speed

    @staticmethod
    def _copy(src: Path, dst: Path) -> None:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-c", "copy", str(dst)],
            capture_output=True,
        )

    def synthesize(self, segments: list[Segment], total_duration: float) -> Path:
        seg_dir = self.work_dir / "tts_segments"
        seg_dir.mkdir(parents=True, exist_ok=True)

        async def gen_all() -> None:
            for i, seg in enumerate(segments):
                if not seg.text.strip():
                    continue
                await self._tts_one(seg.text, seg_dir / f"raw_{i:04d}.mp3")

        log.info("Tạo giọng đọc cho %d segment", len(segments))
        asyncio.run(gen_all())

        # Đặt từng đoạn theo con trỏ thời gian: không chồng lấn, tự re-sync ở khoảng lặng.
        placed: list[tuple[float, Path]] = []
        cursor = 0.0
        n = len(segments)
        for i, seg in enumerate(segments):
            raw = seg_dir / f"raw_{i:04d}.mp3"
            if not raw.exists():
                continue
            dur = self._duration(raw)
            start = max(seg.start, cursor)
            # Khoảng trống tới mốc bắt đầu (gốc) của đoạn kế tiếp.
            next_start = segments[i + 1].start if i + 1 < n else total_duration
            room = next_start - start
            fitted = seg_dir / f"fit_{i:04d}.mp3"
            final_dur = self._fit_to_room(raw, dur, room, fitted)
            placed.append((start, fitted))
            cursor = start + final_dur

        return self._build_timeline(placed, total_duration, seg_dir)

    def _build_timeline(
        self, placed: list[tuple[float, Path]], total: float, seg_dir: Path
    ) -> Path:
        """Dùng filter adelay để đặt mỗi đoạn vào đúng mốc start rồi amix."""
        out = self.work_dir / "vi_voice.m4a"
        if not placed:
            # tạo audio im lặng
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i",
                 f"anullsrc=r=44100:cl=stereo", "-t", str(total), str(out)],
                capture_output=True,
            )
            return out

        inputs: list[str] = []
        filters: list[str] = []
        for idx, (start, path) in enumerate(placed):
            inputs += ["-i", str(path)]
            delay_ms = int(start * 1000)
            filters.append(
                f"[{idx}:a]adelay={delay_ms}|{delay_ms},"
                f"aresample=44100[a{idx}]"
            )
        mix_inputs = "".join(f"[a{i}]" for i in range(len(placed)))
        filter_complex = (
            ";".join(filters)
            + f";{mix_inputs}amix=inputs={len(placed)}:normalize=0[mixed]"
        )
        cmd = (
            ["ffmpeg", "-y"]
            + inputs
            + ["-filter_complex", filter_complex, "-map", "[mixed]",
               "-t", str(total), str(out)]
        )
        log.info("Ghép timeline lồng tiếng -> %s", out.name)
        subprocess.run(cmd, capture_output=True)
        return out
