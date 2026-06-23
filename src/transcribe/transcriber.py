"""Bóc lời thoại từ video bằng faster-whisper -> danh sách segment có timestamp."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from ..utils.logging import get_logger

log = get_logger("transcribe")


@dataclass
class Segment:
    start: float   # giây
    end: float
    text: str


class Transcriber:
    def __init__(
        self,
        model: str = "small",
        language: str | None = None,
        device: str = "auto",
        compute_type: str = "int8",
        cpu_threads: int = 4,
    ):
        self.language = language
        log.info("Nạp model whisper '%s' (device=%s)", model, device)
        # Giới hạn luồng để tránh MKL xin quá nhiều RAM (mkl_malloc failed)
        self._model = WhisperModel(
            model,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=1,
        )

    def transcribe(self, media_path: Path) -> tuple[list[Segment], str]:
        """Trả về (danh sách segment, mã ngôn ngữ phát hiện được)."""
        log.info("Bóc lời: %s", media_path.name)
        segments_iter, info = self._model.transcribe(
            str(media_path),
            language=self.language,
            vad_filter=True,             # lọc khoảng lặng -> timestamp gọn hơn
            beam_size=5,
        )
        segments = [
            Segment(start=s.start, end=s.end, text=s.text.strip())
            for s in segments_iter
            if s.text.strip()
        ]
        log.info("Phát hiện ngôn ngữ: %s | %d segment", info.language, len(segments))
        return segments, info.language
