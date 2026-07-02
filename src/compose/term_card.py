"""Thẻ THUẬT NGỮ y khoa: vẽ 1 PNG (English + IPA + nghĩa VN) để đè lên video.

Trả về Overlay (cùng kiểu với OCR) nên Compositor.compose(overlays=[...]) chèn
được ngay, có thời điểm hiện/ẩn riêng. Render bằng Pillow, KHÔNG dùng AI -> chính
xác, rẻ, nhẹ. Chuyển động "trượt/pop vào" để dành cho lớp ASS; ở đây overlay hiện
tĩnh trong [start, end] (đủ tốt cho bản đầu).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..ocr.screen_text import Overlay

# Thử lần lượt các font; ưu tiên bold cho từ tiếng Anh.
_BOLD_FONTS = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf"]
_REG_FONTS = ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"]


def _load(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def render_term_card(
    term: str,
    ipa: str,
    meaning: str,
    out_png: Path,
    *,
    video_w: int,
    video_h: int,
    start: float,
    end: float,
    y_ratio: float = 0.62,
) -> Overlay:
    """Vẽ thẻ term ra PNG, trả Overlay đã canh giữa ngang ở y_ratio chiều cao.

    term    : từ/cụm tiếng Anh (in đậm, lớn).
    ipa     : phiên âm IPA, ví dụ "/daɪəɡˈnəʊsɪs/" (truyền "" để bỏ).
    meaning : nghĩa tiếng Việt.
    start/end: khoảng thời gian (giây) thẻ hiện trên video.
    """
    pad = max(24, int(video_w * 0.04))
    gap = max(8, int(video_h * 0.008))
    card_w = int(video_w * 0.86)

    f_term = _load(_BOLD_FONTS, max(34, int(video_h * 0.05)))
    f_ipa = _load(_REG_FONTS, max(24, int(video_h * 0.032)))
    f_mean = _load(_REG_FONTS, max(26, int(video_h * 0.036)))

    # Đo chiều cao nội dung trên 1 canvas tạm.
    tmp = Image.new("RGBA", (card_w, 10))
    d = ImageDraw.Draw(tmp)
    rows: list[tuple[str, ImageFont.FreeTypeFont, str]] = [(term, f_term, "FFFFFF")]
    if ipa.strip():
        rows.append((ipa, f_ipa, "9CD2FF"))      # xanh nhạt cho IPA
    if meaning.strip():
        rows.append((meaning, f_mean, "FFE680"))  # vàng nhạt cho nghĩa VN

    heights = [_text_size(d, t, f)[1] for t, f, _ in rows]
    card_h = pad * 2 + sum(heights) + gap * (len(rows) - 1)

    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(18, int(card_h * 0.16))
    draw.rounded_rectangle(
        [0, 0, card_w - 1, card_h - 1], radius=radius, fill=(15, 18, 28, 210)
    )

    y = pad
    for (text, font, color), h in zip(rows, heights):
        w, _ = _text_size(draw, text, font)
        x = (card_w - w) // 2
        rgb = tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
        # Viền mỏng cho dễ đọc.
        draw.text((x, y), text, font=font, fill=(0, 0, 0, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0, 255))
        draw.text((x, y), text, font=font, fill=(*rgb, 255))
        y += h + gap

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)

    x = (video_w - card_w) // 2
    y_pos = int(video_h * y_ratio - card_h / 2)
    return Overlay(png=str(out_png), x=x, y=max(0, y_pos), start=start, end=end)
