"""Dịch các segment sang tiếng Việt bằng Claude HOẶC Gemini.

Dịch theo lô và giữ NGUYÊN số lượng segment (1 input -> 1 output) để
timestamp của phụ đề / lồng tiếng vẫn khớp với video gốc.

Chọn nhà cung cấp qua config: translate.provider = "claude" | "gemini".
"""
from __future__ import annotations

import json

import requests

from ..transcribe.transcriber import Segment
from ..utils.logging import get_logger

log = get_logger("translate")

_SYSTEM = """Bạn là dịch giả phụ đề chuyên nghiệp. Nhiệm vụ: dịch lời thoại \
video sang {target}. Phong cách: {style}.

Quy tắc bắt buộc:
- Trả về ĐÚNG số dòng như đầu vào, theo định dạng JSON array các chuỗi.
- Mỗi phần tử là bản dịch của câu cùng chỉ số. KHÔNG gộp, KHÔNG tách câu.
- Dịch thoát ý, tự nhiên như người Việt nói, KHÔNG dịch word-by-word.
- Giữ độ dài tương đương để khớp khẩu hình/lồng tiếng. Không thêm chú thích.
- Chỉ trả JSON array, không kèm văn bản nào khác."""


class BaseTranslator:
    """Lo phần chia lô + giữ khớp segment. Lớp con chỉ cần cài `_call`."""

    def __init__(
        self,
        target_language: str = "Tiếng Việt",
        style: str = "tự nhiên, đời thường",
        batch_size: int = 40,
    ):
        self.target = target_language
        self.style = style
        self.batch_size = batch_size

    # Lớp con cài hàm này: nhận system + user prompt, trả về text thô.
    def _call(self, system: str, user: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def _translate_batch(self, texts: list[str]) -> list[str]:
        system = _SYSTEM.format(target=self.target, style=self.style)
        payload = json.dumps(
            [{"i": i, "text": t} for i, t in enumerate(texts)], ensure_ascii=False
        )
        user = (
            f"Dịch các câu sau. Trả về JSON array gồm {len(texts)} chuỗi đã dịch, "
            f"đúng thứ tự.\n\n{payload}"
        )
        raw = _strip_code_fence(self._call(system, user).strip())
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Không parse được JSON, giữ nguyên lô này")
            return texts
        result = [
            (item["text"] if isinstance(item, dict) else str(item)) for item in out
        ]
        if len(result) != len(texts):
            log.warning(
                "Số dòng dịch (%d) khác đầu vào (%d), căn chỉnh lại",
                len(result), len(texts),
            )
            result = _align(result, len(texts))
        return result

    def translate_segments(self, segments: list[Segment]) -> list[Segment]:
        texts = [s.text for s in segments]
        translated: list[str] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            log.info("Dịch lô %d-%d / %d", start, start + len(batch), len(texts))
            translated.extend(self._translate_batch(batch))
        return [
            Segment(start=s.start, end=s.end, text=vi)
            for s, vi in zip(segments, translated)
        ]


class ClaudeTranslator(BaseTranslator):
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", **kw):
        super().__init__(**kw)
        if not api_key:
            raise ValueError("Thiếu ANTHROPIC_API_KEY trong .env")
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def _call(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text


class GeminiTranslator(BaseTranslator):
    """Gọi Gemini qua REST (không cần SDK riêng, chỉ dùng requests)."""

    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", **kw):
        super().__init__(**kw)
        if not api_key:
            raise ValueError("Thiếu GEMINI_API_KEY trong .env")
        self.api_key = api_key
        self.model = model

    def _call(self, system: str, user: str) -> str:
        url = f"{self.BASE}/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
        resp = requests.post(
            url,
            params={"key": self.api_key},
            json=body,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            log.warning("Phản hồi Gemini bất thường: %s", data)
            return "[]"


def make_translator(
    provider: str,
    *,
    anthropic_key: str = "",
    gemini_key: str = "",
    model: str = "",
    target_language: str = "Tiếng Việt",
    style: str = "tự nhiên",
) -> BaseTranslator:
    """Factory: chọn translator theo provider trong config."""
    provider = (provider or "claude").lower()
    if provider == "gemini":
        return GeminiTranslator(
            api_key=gemini_key,
            model=model or "gemini-2.5-flash",
            target_language=target_language,
            style=style,
        )
    if provider == "claude":
        return ClaudeTranslator(
            api_key=anthropic_key,
            model=model or "claude-haiku-4-5",
            target_language=target_language,
            style=style,
        )
    raise ValueError(f"provider không hỗ trợ: {provider} (dùng 'claude' hoặc 'gemini')")


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()[1:]  # bỏ ```json
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text


def _align(result: list[str], n: int) -> list[str]:
    if len(result) < n:
        result = result + [""] * (n - len(result))
    return result[:n]
