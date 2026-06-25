"""Dịch các segment sang tiếng Việt bằng Claude HOẶC Gemini.

Dịch theo lô và giữ NGUYÊN số lượng segment (1 input -> 1 output) để
timestamp của phụ đề / lồng tiếng vẫn khớp với video gốc.

Chọn nhà cung cấp qua config: translate.provider = "claude" | "gemini".
"""
from __future__ import annotations

import json
import time

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
    def _raw_call(self, system: str, user: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def _call(self, system: str, user: str, retries: int = 5) -> str:
        """Gọi API có retry + backoff cho lỗi tạm thời (429/5xx)."""
        delay = 2.0
        for attempt in range(retries):
            try:
                return self._raw_call(system, user)
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                if code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    log.warning("API %s, thử lại sau %.0fs (lần %d)", code, delay, attempt + 1)
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise
            except Exception as e:  # noqa: BLE001 - lỗi mạng tạm thời
                if attempt < retries - 1:
                    log.warning("Lỗi gọi API (%s), thử lại sau %.0fs", e, delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise
        raise RuntimeError("Hết lượt retry API dịch")

    def _translate_batch(self, texts: list[str]) -> list[str]:
        """Dịch 1 lô. Trả về JSON OBJECT keyed theo index để KHÔNG bị lệch
        dòng nếu model lỡ bỏ/gộp 1 câu (mỗi bản dịch gắn đúng id của nó)."""
        system = _SYSTEM.format(target=self.target, style=self.style)
        payload = json.dumps(
            {str(i): t for i, t in enumerate(texts)}, ensure_ascii=False
        )
        user = (
            "Dịch các câu trong JSON object sau (key là id, value là câu gốc). "
            "Trả về JSON object CÙNG các key đó, value là bản dịch tiếng Việt. "
            f"Phải có đủ {len(texts)} key.\n\n{payload}"
        )
        raw = _strip_code_fence(self._call(system, user).strip())
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Không parse được JSON, giữ nguyên lô này")
            return texts
        # Map theo id; câu nào thiếu thì giữ nguyên bản gốc
        result: list[str] = []
        missing = 0
        for i, original in enumerate(texts):
            val = out.get(str(i)) if isinstance(out, dict) else None
            if isinstance(val, str) and val.strip():
                result.append(val.strip())
            else:
                result.append(original)
                missing += 1
        if missing:
            log.warning("%d/%d câu không có bản dịch, giữ nguyên gốc", missing, len(texts))
        return result

    def translate_texts(self, texts: list[str]) -> list[str]:
        """Dịch danh sách chuỗi (khử trùng lặp để tiết kiệm + nhất quán)."""
        uniq: dict[str, None] = {}
        for t in texts:
            uniq.setdefault(t, None)
        unique_texts = list(uniq.keys())

        translated_unique: list[str] = []
        for start in range(0, len(unique_texts), self.batch_size):
            batch = unique_texts[start : start + self.batch_size]
            log.info("Dịch lô %d-%d / %d", start, start + len(batch), len(unique_texts))
            translated_unique.extend(self._translate_batch(batch))

        mapping = dict(zip(unique_texts, translated_unique))
        return [mapping.get(t, t) for t in texts]

    def translate_segments(self, segments: list[Segment]) -> list[Segment]:
        translated = self.translate_texts([s.text for s in segments])
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

    def _raw_call(self, system: str, user: str) -> str:
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
        # Cho phép NHIỀU key (phân tách bằng dấu phẩy) -> tự xoay khi 1 key 429.
        self.keys = [k.strip() for k in (api_key or "").split(",") if k.strip()]
        if not self.keys:
            raise ValueError("Thiếu GEMINI_API_KEY trong .env")
        self.idx = 0
        self.model = model

    def _raw_call(self, system: str, user: str) -> str:
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
        # Thử lần lượt từng key trong 1 vòng: gặp 429 (hết quota) thì xoay key kế.
        # Nếu CẢ vòng đều 429 -> raise HTTPError để lớp _call backoff rồi thử lại
        # (chờ quota theo-phút hồi lại). Lỗi khác (503...) raise ngay để _call xử lý.
        last_429: requests.HTTPError | None = None
        for _ in range(len(self.keys)):
            key = self.keys[self.idx]
            resp = requests.post(url, params={"key": key}, json=body, timeout=120)
            if resp.status_code == 429:
                log.warning(
                    "Key Gemini #%d hết quota (429), xoay sang key kế", self.idx + 1
                )
                try:
                    resp.raise_for_status()
                except requests.HTTPError as e:
                    last_429 = e
                self.idx = (self.idx + 1) % len(self.keys)
                continue
            resp.raise_for_status()
            data = resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                log.warning("Phản hồi Gemini bất thường: %s", data)
                return "[]"
        # Tất cả key đều 429 -> để lớp trên backoff & retry
        raise last_429 if last_429 is not None else RuntimeError("Gemini 429 mọi key")


class EchoTranslator(BaseTranslator):
    """Không dịch — trả nguyên văn. Dùng để TEST không cần API key, hoặc khi
    muốn phụ đề/lồng tiếng CÙNG ngôn ngữ gốc (vd bóc lời để làm caption)."""

    def translate_texts(self, texts: list[str]) -> list[str]:
        return list(texts)

    def translate_segments(self, segments: list[Segment]) -> list[Segment]:
        return [Segment(start=s.start, end=s.end, text=s.text) for s in segments]


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
    if provider == "echo":
        return EchoTranslator(target_language=target_language, style=style)
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
    raise ValueError(f"provider không hỗ trợ: {provider} (dùng 'claude' | 'gemini' | 'echo')")


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
