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
import re
import subprocess
from pathlib import Path

import edge_tts
from edge_tts.exceptions import NoAudioReceived

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

    async def _tts_one(self, text: str, out: Path, voice: str | None = None) -> None:
        communicate = edge_tts.Communicate(text, voice or self.voice, rate=self.rate)
        await communicate.save(str(out))

    @staticmethod
    def _speakable(text: str) -> bool:
        """Có ít nhất 1 chữ/số để đọc không? (chỉ dấu câu/ký hiệu -> bỏ qua)."""
        return bool(re.search(r"[^\W_]", text, flags=re.UNICODE))

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

    def synthesize(
        self,
        segments: list[Segment],
        total_duration: float,
        voices: list[str] | None = None,
    ) -> Path:
        """Tạo audio lồng tiếng. `voices`: giọng cho từng đoạn (cùng độ dài với
        segments) để đa giọng theo người nói; None = dùng 1 giọng `self.voice`."""
        seg_dir = self.work_dir / "tts_segments"
        seg_dir.mkdir(parents=True, exist_ok=True)

        skipped = 0

        async def gen_all() -> None:
            nonlocal skipped
            for i, seg in enumerate(segments):
                text = seg.text.strip()
                # Bỏ qua đoạn rỗng hoặc chỉ có dấu câu/ký hiệu (edge-tts không đọc được).
                if not self._speakable(text):
                    skipped += 1
                    continue
                voice = voices[i] if voices and i < len(voices) else None
                out = seg_dir / f"raw_{i:04d}.mp3"
                # Cache: đã tạo rồi (và không rỗng) thì bỏ qua -> chạy lại nhanh.
                if out.exists() and out.stat().st_size > 0:
                    continue
                # Thử lại nhiều lần: edge-tts (dịch vụ free) hay throttle trả
                # NoAudioReceived tạm thời -> backoff rồi thử lại, hết mới bỏ.
                last_err: Exception | None = None
                for attempt in range(5):
                    try:
                        await self._tts_one(text, out, voice)
                        last_err = None
                        break
                    except Exception as e:  # noqa: BLE001  (gồm NoAudioReceived)
                        last_err = e
                        await asyncio.sleep(1.5 * (attempt + 1))
                if last_err is not None:
                    log.warning("Bỏ qua đoạn %d (%r): %s", i, text, last_err)
                    skipped += 1
                    # Dọn file rỗng edge-tts để lại, tránh làm hỏng bước ghép.
                    if out.exists() and out.stat().st_size == 0:
                        out.unlink()
                # Nhịp nhẹ giữa các lần gọi để giảm throttle.
                await asyncio.sleep(0.15)

        log.info("Tạo giọng đọc cho %d segment", len(segments))
        asyncio.run(gen_all())
        if skipped:
            log.info("Đã bỏ qua %d segment không lồng tiếng được", skipped)

        # Đặt từng đoạn theo con trỏ thời gian: không chồng lấn, tự re-sync ở khoảng lặng.
        placed: list[tuple[float, Path]] = []
        cursor = 0.0
        n = len(segments)
        for i, seg in enumerate(segments):
            raw = seg_dir / f"raw_{i:04d}.mp3"
            # Bỏ qua nếu thiếu hoặc file rỗng/hỏng (không có audio).
            if not raw.exists() or raw.stat().st_size == 0:
                continue
            dur = self._duration(raw)
            if dur <= 0:
                continue
            start = max(seg.start, cursor)
            # Khoảng trống tới mốc bắt đầu (gốc) của đoạn kế tiếp.
            next_start = segments[i + 1].start if i + 1 < n else total_duration
            room = next_start - start
            fitted = seg_dir / f"fit_{i:04d}.mp3"
            final_dur = self._fit_to_room(raw, dur, room, fitted)
            # Chỉ đưa vào timeline khi file fit thực sự được tạo hợp lệ.
            if not fitted.exists() or fitted.stat().st_size == 0:
                log.warning("Không tạo được fit cho đoạn %d, bỏ qua", i)
                continue
            placed.append((start, fitted))
            cursor = start + final_dur

        return self._build_timeline(placed, total_duration, seg_dir)

    # Số đoạn tối đa mỗi lần gọi ffmpeg -> giữ command line dưới giới hạn
    # Windows (~32k ký tự). Nhiều đoạn hơn sẽ được ghép theo nhiều lô.
    _BATCH = 50

    def _build_timeline(
        self, placed: list[tuple[float, Path]], total: float, seg_dir: Path
    ) -> Path:
        """Đặt mỗi đoạn vào đúng mốc start (adelay) rồi amix.

        Khi có quá nhiều đoạn, ghép theo lô để không vượt giới hạn độ dài lệnh
        của Windows, sau đó amix các lô lại với nhau.
        """
        out = self.work_dir / "vi_voice.m4a"
        if not placed:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i",
                 "anullsrc=r=44100:cl=stereo", "-t", str(total), str(out)],
                capture_output=True,
            )
            return out

        log.info("Ghép timeline lồng tiếng (%d đoạn) -> %s", len(placed), out.name)
        # 1) Ghép từng lô thành các track dài bằng video (im lặng ở chỗ trống).
        batch_tracks: list[Path] = []
        for b, s in enumerate(range(0, len(placed), self._BATCH)):
            chunk = placed[s : s + self._BATCH]
            track = seg_dir / f"timeline_{b:03d}.m4a"
            self._mix_delayed(chunk, total, track)
            batch_tracks.append(track)

        # 2) Trộn các track lô lại (số lô nhỏ -> 1 lệnh là đủ).
        if len(batch_tracks) == 1:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(batch_tracks[0]), "-c", "copy", str(out)],
                capture_output=True,
            )
        else:
            self._mix_tracks(batch_tracks, total, out)
        return out

    def _mix_delayed(
        self, placed: list[tuple[float, Path]], total: float, out: Path
    ) -> None:
        """1 lô: adelay mỗi đoạn về đúng mốc thời gian rồi amix -> 1 track."""
        inputs: list[str] = []
        filters: list[str] = []
        for idx, (start, path) in enumerate(placed):
            inputs += ["-i", str(path)]
            delay_ms = int(start * 1000)
            filters.append(
                f"[{idx}:a]adelay={delay_ms}|{delay_ms},aresample=44100[a{idx}]"
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
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            log.warning("Lỗi ghép lô timeline: %s", r.stderr.decode(errors="ignore")[-300:])

    def _mix_tracks(self, tracks: list[Path], total: float, out: Path) -> None:
        """Trộn nhiều track (đã đúng vị trí) thành 1 (không cần adelay nữa)."""
        inputs: list[str] = []
        for t in tracks:
            inputs += ["-i", str(t)]
        mix_inputs = "".join(f"[{i}:a]" for i in range(len(tracks)))
        filter_complex = f"{mix_inputs}amix=inputs={len(tracks)}:normalize=0[mixed]"
        cmd = (
            ["ffmpeg", "-y"]
            + inputs
            + ["-filter_complex", filter_complex, "-map", "[mixed]",
               "-t", str(total), str(out)]
        )
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            log.warning("Lỗi trộn track timeline: %s", r.stderr.decode(errors="ignore")[-300:])
