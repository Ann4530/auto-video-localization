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
- Trả về JSON array, MỖI phần tử là object {{"i": <chỉ số gốc>, "text": "<bản dịch>"}}.
- GIỮ NGUYÊN chỉ số "i" của câu gốc. Phải đủ tất cả chỉ số, KHÔNG gộp, KHÔNG tách câu.
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
        if not isinstance(out, list):
            log.warning("JSON không phải array, giữ nguyên lô này")
            return texts

        # Ưu tiên ghép theo chỉ số "i" -> 1 câu thiếu/thừa KHÔNG làm lệch các câu khác.
        by_index: dict[int, str] = {}
        for item in out:
            if isinstance(item, dict) and "i" in item:
                try:
                    idx = int(item["i"])
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(texts):
                    by_index[idx] = str(item.get("text", "")).strip() or texts[idx]

        if by_index:
            missing = [i for i in range(len(texts)) if i not in by_index]
            if missing:
                log.warning("Thiếu %d câu dịch, giữ nguyên gốc các câu: %s",
                            len(missing), missing)
            # Câu nào model bỏ sót -> giữ nguyên văn bản gốc (không để trống).
            return [by_index.get(i, texts[i]) for i in range(len(texts))]

        # Dự phòng: model trả mảng phẳng (không có "i") -> ghép theo thứ tự.
        result = [
            (item["text"] if isinstance(item, dict) else str(item)) for item in out
        ]
        if len(result) != len(texts):
            log.warning(
                "Số dòng dịch (%d) khác đầu vào (%d), căn chỉnh lại",
                len(result), len(texts),
            )
            result = _align(result, texts)
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
        # Cho phép nhiều key (ngăn cách bằng dấu phẩy) để xoay khi hết quota.
        self.keys = [k.strip() for k in str(api_key).split(",") if k.strip()]
        if not self.keys:
            raise ValueError("Thiếu GEMINI_API_KEY trong .env")
        self._idx = 0  # key đang dùng
        self.model = model
        if len(self.keys) > 1:
            log.info("Gemini: có %d key, sẽ tự xoay khi hết quota", len(self.keys))

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
        # Chiến lược:
        #  - 429 (rate-limit/quota): xoay sang key kế tiếp. Nếu CẢ vòng key đều
        #    429 -> chờ (rate-limit theo phút sẽ tự reset) rồi thử lại vòng mới.
        #  - 5xx / lỗi mạng (server tạm thời): chờ rồi thử lại, có xoay key.
        #  - Lỗi khác (vd 400/403): báo ngay.
        n = len(self.keys)
        max_rounds = 6          # số vòng quét hết các key trước khi bỏ cuộc
        server_retries = 0      # số lần thử lại do lỗi 5xx/mạng
        max_server_retries = 6
        backoff = 2.0
        rate_limited_in_round = 0
        last_err: Exception | None = None

        while True:
            key = self.keys[self._idx]
            try:
                resp = requests.post(
                    url, params={"key": key}, json=body, timeout=120
                )
            except requests.RequestException as e:  # lỗi mạng -> thử lại
                last_err = e
                server_retries += 1
                if server_retries > max_server_retries:
                    raise RuntimeError(f"Lỗi mạng liên tục khi gọi Gemini: {e}")
                log.warning("Lỗi mạng khi gọi Gemini (%s), thử lại sau %.0fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue

            if resp.status_code == 429:
                rate_limited_in_round += 1
                self._idx = (self._idx + 1) % n
                if rate_limited_in_round >= n:
                    # Cả vòng key đều bị giới hạn -> chờ reset (free tier reset theo phút).
                    max_rounds -= 1
                    if max_rounds <= 0:
                        raise RuntimeError(
                            f"Tất cả {n} key Gemini vẫn bị 429 sau nhiều lần chờ. "
                            "Có thể đã hết quota NGÀY. Thêm key mới vào GEMINI_API_KEY "
                            "hoặc chờ quota reset."
                        )
                    wait = 65  # rate-limit theo phút -> chờ ~1 phút
                    log.warning(
                        "Cả %d key đều bị 429, chờ %ds cho quota reset rồi thử lại "
                        "(còn %d vòng)", n, wait, max_rounds,
                    )
                    time.sleep(wait)
                    rate_limited_in_round = 0
                else:
                    log.warning(
                        "Key Gemini #%d bị 429, xoay sang key kế tiếp",
                        (self._idx - 1) % n + 1,
                    )
                continue

            if resp.status_code in (500, 502, 503, 504):
                last_err = requests.HTTPError(
                    f"{resp.status_code} {resp.reason}", response=resp
                )
                server_retries += 1
                if server_retries > max_server_retries:
                    raise RuntimeError(
                        f"Gemini lỗi server liên tục: {last_err}"
                    )
                log.warning(
                    "Gemini lỗi server tạm thời (%d), thử lại sau %.0fs (lần %d/%d)",
                    resp.status_code, backoff, server_retries, max_server_retries,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                self._idx = (self._idx + 1) % n  # đổi key phòng sự cố cục bộ
                continue

            # Thành công -> reset bộ đếm 429 của vòng.
            rate_limited_in_round = 0
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


def _align(result: list[str], texts: list[str]) -> list[str]:
    """Căn chỉnh độ dài khi ghép theo thứ tự: thiếu thì bù bằng câu gốc, thừa thì cắt."""
    n = len(texts)
    if len(result) < n:
        result = result + texts[len(result):]
    return result[:n]
