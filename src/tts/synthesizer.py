"""Tạo audio lồng tiếng Việt từ các segment đã dịch, bằng edge-tts.

Cách làm để giữ đồng bộ với video:
  1. Với mỗi segment, tạo 1 file audio tiếng Việt.
  2. Nếu audio dài hơn khoảng thời gian gốc -> tăng tốc (atempo) cho vừa.
  3. Đặt mỗi đoạn vào đúng mốc thời gian start trên 1 timeline,
     chèn khoảng lặng giữa các đoạn.
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
    ):
        self.voice = voice
        self.rate = rate
        self.work_dir = work_dir or Path(".")

    async def _tts_one(self, text: str, out: Path, retries: int = 3) -> bool:
        """Tạo TTS cho 1 đoạn, có retry. Trả True nếu thành công.

        edge-tts đôi khi trả 'NoAudioReceived' (chập chờn / giới hạn máy chủ MS,
        hoặc giọng không đọc được text) -> thử lại, vẫn lỗi thì bỏ qua đoạn này
        (coi như im lặng) để KHÔNG làm hỏng cả job."""
        delay = 1.5
        for attempt in range(retries):
            try:
                communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
                await communicate.save(str(out))
                if out.exists() and out.stat().st_size > 0:
                    return True
            except Exception as e:  # noqa: BLE001
                log.warning("TTS đoạn lỗi (lần %d): %s", attempt + 1, e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
        log.error("Bỏ qua đoạn không tạo được giọng: %.40s", text)
        return False

    def _duration(self, path: Path) -> float:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            return float(r.stdout.strip())
        except ValueError:
            return 0.0

    def _fit_to_slot(self, src: Path, slot: float, dst: Path) -> None:
        """Tăng tốc audio nếu dài hơn slot thời gian gốc (giữ pitch)."""
        dur = self._duration(src)
        if slot <= 0 or dur <= slot or dur == 0:
            # đủ chỗ -> copy nguyên
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), "-c", "copy", str(dst)],
                capture_output=True,
            )
            return
        speed = min(dur / slot, 2.0)  # giới hạn 2x cho dễ nghe
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={speed:.3f}",
             str(dst)],
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
            # (lỗi từng đoạn đã được nuốt trong _tts_one -> không raise ở đây)

        log.info("Tạo giọng đọc cho %d segment", len(segments))
        asyncio.run(gen_all())

        # Khớp từng đoạn vào slot thời lượng gốc
        placed: list[tuple[float, Path]] = []
        for i, seg in enumerate(segments):
            raw = seg_dir / f"raw_{i:04d}.mp3"
            if not raw.exists():
                continue
            fitted = seg_dir / f"fit_{i:04d}.mp3"
            self._fit_to_slot(raw, seg.end - seg.start, fitted)
            placed.append((seg.start, fitted))

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
