"""Danh sách ngôn ngữ cho web UI.

- TARGET_LANGUAGES: ngôn ngữ ĐÍCH để dịch (tên hiển thị, truyền thẳng cho
  translator dưới dạng văn bản mô tả ngôn ngữ).
- SOURCE_LANGUAGES: ngôn ngữ NGUỒN của lời thoại (label + mã ISO cho whisper/
  Gemini). "" = tự động nhận diện.
"""
from __future__ import annotations

# Ngôn ngữ đích (tên tiếng Việt). Tiếng Việt & Tiếng Anh để đầu cho tiện.
TARGET_LANGUAGES: list[str] = [
    "Tiếng Việt",
    "Tiếng Anh",
    "Tiếng Trung (Giản thể)",
    "Tiếng Trung (Phồn thể)",
    "Tiếng Nhật",
    "Tiếng Hàn",
    "Tiếng Thái",
    "Tiếng Indonesia",
    "Tiếng Tây Ban Nha",
    "Tiếng Pháp",
    "Tiếng Đức",
    "Tiếng Bồ Đào Nha",
    "Tiếng Nga",
    "Tiếng Ý",
    "Tiếng Ả Rập",
    "Tiếng Hindi",
    "Tiếng Thổ Nhĩ Kỳ",
    "Tiếng Hà Lan",
    "Tiếng Ba Lan",
    "Tiếng Khmer",
    "Tiếng Lào",
    "Tiếng Philippines (Tagalog)",
]

# Ngôn ngữ nguồn: (label, mã ISO). "" = tự động.
SOURCE_LANGUAGES: list[tuple[str, str]] = [
    ("Tự động nhận diện", ""),
    ("Tiếng Anh", "en"),
    ("Tiếng Trung", "zh"),
    ("Tiếng Nhật", "ja"),
    ("Tiếng Hàn", "ko"),
    ("Tiếng Việt", "vi"),
    ("Tiếng Thái", "th"),
    ("Tiếng Tây Ban Nha", "es"),
    ("Tiếng Pháp", "fr"),
    ("Tiếng Đức", "de"),
    ("Tiếng Nga", "ru"),
    ("Tiếng Indonesia", "id"),
    ("Tiếng Bồ Đào Nha", "pt"),
    ("Tiếng Ý", "it"),
    ("Tiếng Ả Rập", "ar"),
    ("Tiếng Hindi", "hi"),
]
