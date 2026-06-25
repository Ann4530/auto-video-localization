"""Tuỳ chọn xử lý cho MỘT job dịch video.

Trước đây pipeline dùng 1 cờ `mode` cứng (voice_transcript / voice_only /
screen_only). Khi lên web, mỗi request cần tuỳ biến riêng (chọn dịch text/
tiếng/cả 2, chọn giọng, ngôn ngữ đích...). `JobOptions` gói toàn bộ các tuỳ
chọn đó để truyền xuyên suốt pipeline.

Quy tắc merge: field nào để None/không đặt -> rơi về mặc định trong config.yaml
tại nơi sử dụng (xem pipeline._get_translator / _run).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Các "choice" hiển thị trên web -> map sang toggle bên dưới.
#   text  : chỉ dịch & hiện phụ đề (không lồng tiếng)
#   voice : chỉ lồng tiếng (không phụ đề)
#   both  : cả phụ đề + lồng tiếng
#   none  : không đụng tới lời thoại (vd chỉ chạy OCR đè chữ màn hình)
CHOICES = ("text", "voice", "both", "none")


@dataclass
class JobOptions:
    # --- Làm gì ---
    dub: bool = False              # dịch TIẾNG (lồng tiếng edge-tts)
    subtitles: bool = False        # dịch TEXT lời thoại -> phụ đề
    ocr_overlay: bool = False      # dịch CHỮ trên màn hình (OCR đè)

    # --- Dịch ---
    target_language: str = "Tiếng Việt"
    provider: str | None = None    # None -> dùng config.translate.provider
    model: str | None = None       # None -> dùng config.translate.model
    style: str | None = None       # None -> dùng config.translate.style

    # --- Giọng đọc (chỉ dùng khi dub=True) ---
    voice: str = "vi-VN-HoaiMyNeural"
    rate: str = "+0%"
    keep_original_volume: float | None = None  # None -> config.tts.keep_original_volume

    # --- Đăng (tuỳ chọn, dùng cho automation n8n) ---
    upload_targets: list[str] = field(default_factory=list)  # ["tiktok","facebook"]
    caption: str | None = None

    @classmethod
    def from_request(cls, choice: str, **overrides: Any) -> "JobOptions":
        """Tạo JobOptions từ lựa chọn trên web ('text'/'voice'/'both'/'none').

        `overrides` là các field khác (voice, target_language, ocr_overlay...).
        Field nào không truyền sẽ giữ mặc định của dataclass (hoặc None để rơi
        về config ở tầng pipeline).
        """
        choice = (choice or "both").lower()
        if choice not in CHOICES:
            raise ValueError(f"choice không hợp lệ: {choice} (dùng {CHOICES})")
        want_text = choice in ("text", "both")
        want_voice = choice in ("voice", "both")
        return cls(
            dub=want_voice,
            subtitles=want_text,
            **overrides,
        )

    @property
    def needs_speech(self) -> bool:
        """Có cần bóc lời + dịch lời thoại không? (cho phụ đề hoặc lồng tiếng)"""
        return self.dub or self.subtitles

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobOptions":
        """Khôi phục từ dict đã lưu (DB). Bỏ qua key lạ để an toàn khi nâng cấp."""
        valid = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in valid})
