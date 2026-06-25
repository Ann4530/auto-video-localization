"""Bóc lời thoại từ video -> danh sách segment có timestamp.

Hai backend, chọn qua config.transcribe.provider:
  - "whisper" (mặc định): faster-whisper chạy local. Chính xác, offline, nhưng
    NGỐN RAM (máy yếu chỉ dùng nổi model 'base').
  - "gemini": đẩy audio lên Gemini API để bóc lời. KHÔNG tốn RAM local, độ
    chính xác tốt, ~1500 lượt free/ngày. Cần GEMINI_API_KEY (đã có sẵn để dịch).

Cả hai trả cùng kiểu (list[Segment], mã_ngôn_ngữ) nên phần còn lại của pipeline
không cần biết dùng backend nào.
"""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from ..utils.logging import get_logger

log = get_logger("transcribe")


@dataclass
class Segment:
    start: float   # giây
    end: float
    text: str


class BaseTranscriber:
    def transcribe(self, media_path: Path) -> tuple[list[Segment], str]:  # pragma: no cover
        raise NotImplementedError


# ----------------------------------------------------------------------------
# Backend 1: faster-whisper (local)
# ----------------------------------------------------------------------------
class WhisperTranscriber(BaseTranscriber):
    def __init__(
        self,
        model: str = "small",
        language: str | None = None,
        device: str = "auto",
        compute_type: str = "int8",
        cpu_threads: int = 4,
    ):
        from faster_whisper import WhisperModel

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
        log.info("Bóc lời (whisper): %s", media_path.name)
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


# ----------------------------------------------------------------------------
# Backend 2: Gemini API (đẩy audio lên cloud, không tốn RAM local)
# ----------------------------------------------------------------------------
_ASR_PROMPT = """Bạn là công cụ nhận dạng giọng nói (ASR) chính xác. Hãy nghe \
audio và bóc TOÀN BỘ lời thoại theo NGÔN NGỮ GỐC (không dịch).

Trả về DUY NHẤT một JSON object dạng:
{"language":"<mã ISO ngôn ngữ, vd 'zh','en','vi'>","segments":[{"start":<giây float>,"end":<giây float>,"text":"<lời thoại>"}]}

Quy tắc:
- Chia segment theo câu/ngắt nghỉ tự nhiên, mỗi segment 1-2 câu ngắn.
- start/end là số giây (float) tính từ đầu audio, phải tăng dần, KHÔNG chồng lấn.
- KHÔNG thêm chú thích, KHÔNG markdown, chỉ JSON thuần."""


class GeminiTranscriber(BaseTranscriber):
    """Bóc lời bằng Gemini. Hỗ trợ nhiều key (phân tách dấu phẩy) tự xoay khi 429."""

    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        language: str | None = None,
    ):
        self.keys = [k.strip() for k in (api_key or "").split(",") if k.strip()]
        if not self.keys:
            raise ValueError("Thiếu GEMINI_API_KEY để dùng Gemini ASR")
        self.idx = 0
        self.model = model
        self.language = language

    def _extract_audio(self, media_path: Path) -> Path:
        """Trích audio mono 16kHz mp3 (nhẹ) để gửi lên Gemini."""
        tmp = Path(tempfile.gettempdir()) / f"asr_{media_path.stem}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(media_path), "-vn", "-ac", "1",
             "-ar", "16000", "-b:a", "64k", str(tmp)],
            capture_output=True,
        )
        return tmp

    def _call(self, audio_b64: str, retries: int = 5) -> dict:
        url = f"{self.BASE}/{self.model}:generateContent"
        prompt = _ASR_PROMPT
        if self.language:
            prompt += f"\nNgôn ngữ audio là: {self.language}."
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "audio/mp3", "data": audio_b64}},
                ],
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
        delay = 2.0
        for attempt in range(retries):
            # xoay key trong 1 vòng nếu 429
            last_429: requests.HTTPError | None = None
            for _ in range(len(self.keys)):
                key = self.keys[self.idx]
                resp = requests.post(url, params={"key": key}, json=body, timeout=300)
                if resp.status_code == 429:
                    log.warning("Key Gemini #%d hết quota (429), xoay key", self.idx + 1)
                    try:
                        resp.raise_for_status()
                    except requests.HTTPError as e:
                        last_429 = e
                    self.idx = (self.idx + 1) % len(self.keys)
                    continue
                if resp.status_code in (500, 502, 503, 504):
                    raise requests.HTTPError(response=resp)
                resp.raise_for_status()
                data = resp.json()
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    log.warning("Phản hồi Gemini ASR bất thường: %s", data)
                    return {"language": "", "segments": []}
                return json.loads(_strip_fence(text))
            # cả vòng 429 -> backoff
            if attempt < retries - 1:
                log.warning("Gemini ASR 429 mọi key, chờ %.0fs", delay)
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            if last_429 is not None:
                raise last_429
        raise RuntimeError("Gemini ASR hết lượt retry")

    def transcribe(self, media_path: Path) -> tuple[list[Segment], str]:
        log.info("Bóc lời (Gemini): %s", media_path.name)
        audio = self._extract_audio(media_path)
        with open(audio, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("ascii")
        result = self._call(audio_b64)
        try:
            audio.unlink(missing_ok=True)
        except OSError:
            pass
        lang = result.get("language", "") or ""
        segments = []
        for s in result.get("segments", []):
            txt = (s.get("text") or "").strip()
            if not txt:
                continue
            try:
                start = float(s.get("start", 0))
                end = float(s.get("end", start))
            except (TypeError, ValueError):
                continue
            segments.append(Segment(start=start, end=end, text=txt))
        log.info("Gemini ASR: ngôn ngữ=%s | %d segment", lang, len(segments))
        return segments, lang


# Alias tương thích ngược (code cũ import Transcriber)
Transcriber = WhisperTranscriber


def make_transcriber(
    provider: str = "whisper",
    *,
    gemini_key: str = "",
    model: str = "",
    language: str | None = None,
    # các tham số whisper
    whisper_model: str = "base",
    device: str = "auto",
    compute_type: str = "int8",
    cpu_threads: int = 4,
) -> BaseTranscriber:
    """Factory: chọn backend bóc lời theo config.transcribe.provider."""
    provider = (provider or "whisper").lower()
    if provider == "gemini":
        return GeminiTranscriber(
            api_key=gemini_key,
            model=model or "gemini-2.5-flash",
            language=language,
        )
    if provider == "whisper":
        return WhisperTranscriber(
            model=whisper_model,
            language=language,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
        )
    raise ValueError(f"transcribe.provider không hỗ trợ: {provider} (whisper|gemini)")


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text
