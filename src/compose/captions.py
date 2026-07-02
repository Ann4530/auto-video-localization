"""Phụ đề ĐỘNG (.ass) kiểu karaoke cho video TỰ QUAY (pipeline tạo video).

Khác với subtitles.write_srt (phụ đề câu tĩnh của pipeline DỊCH), module này
sinh file ASS với:
  - karaoke ``\\k``: tô sáng TỪNG TỪ đúng lúc đọc. Cần word-timestamp từ whisper
    (WhisperTranscriber(word_timestamps=True)); segment nào không có thì CHIA ĐỀU
    thời lượng cho các từ (vẫn chạy, chỉ kém khít).
  - pop-in mỗi dòng (phóng to + fade) cho cảm giác "động" như short hiện đại.
  - tô màu riêng các THUẬT NGỮ tiếng Anh y khoa để người học bắt ngay.

Màu ASS theo định dạng &HBBGGRR (BGR, KHÔNG phải RGB).
Render bằng ffmpeg filter ``subtitles`` (libass) trong Compositor.compose(ass=...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..transcribe.transcriber import Segment, Word

# --- Bảng màu mặc định (BGR) -------------------------------------------------
WHITE = "FFFFFF"
YELLOW = "00FFFF"   # R255 G255 B0  -> từ đang đọc (highlight)
CYAN = "FFFF00"     # R0  G255 B255 -> thuật ngữ tiếng Anh (lúc CHƯA đọc tới)

# Từ tiếng Anh: chuỗi chữ Latin thuần (không dấu tiếng Việt), >= 3 ký tự.
_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]{2,}$")
_VN_DIACRITIC = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)


def _ass_ts(seconds: float) -> str:
    """Giây -> 'H:MM:SS.cc' (centisecond) cho dòng Dialogue ASS."""
    if seconds < 0:
        seconds = 0
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


@dataclass
class _WToken:
    text: str
    start: float
    end: float


def _flatten_words(segments: list[Segment]) -> list[_WToken]:
    """Gộp mọi segment thành 1 chuỗi từ liên tục, kèm mốc thời gian.

    Segment có ``words`` -> dùng trực tiếp. Không có -> tách text theo khoảng
    trắng rồi CHIA ĐỀU [start, end] cho các từ.
    """
    out: list[_WToken] = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                t = w.text.strip()
                if t:
                    out.append(_WToken(t, w.start, w.end))
            continue
        toks = [t for t in re.split(r"\s+", seg.text.strip()) if t]
        if not toks:
            continue
        dur = max(seg.end - seg.start, 0.01)
        step = dur / len(toks)
        for i, t in enumerate(toks):
            out.append(_WToken(t, seg.start + i * step, seg.start + (i + 1) * step))
    return out


def _is_term(word: str, terms: set[str] | None) -> bool:
    """Từ này có phải thuật ngữ tiếng Anh cần tô nổi bật không?

    - Nếu truyền ``terms`` (từ script): chỉ tô đúng các từ trong đó (đáng tin nhất).
    - Nếu không: đoán = chữ Latin thuần, không dấu tiếng Việt, >= 3 ký tự.
    """
    core = word.strip().strip(".,!?;:\"'()[]").lower()
    if not core:
        return False
    if terms:
        return core in terms
    return bool(_LATIN_WORD.match(core)) and not _VN_DIACRITIC.search(core)


def _chunk_lines(
    words: list[_WToken], max_words: int, line_gap: float
) -> list[list[_WToken]]:
    """Cắt chuỗi từ thành các DÒNG ngắn: theo số từ, khoảng lặng, hoặc dấu câu."""
    lines: list[list[_WToken]] = []
    cur: list[_WToken] = []
    for i, w in enumerate(words):
        if cur:
            gap = w.start - cur[-1].end
            ends_sentence = cur[-1].text[-1:] in ".!?…"
            if len(cur) >= max_words or gap > line_gap or ends_sentence:
                lines.append(cur)
                cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _line_to_dialogue(
    line: list[_WToken], terms: set[str] | None, prim: str, sec: str, term_sec: str
) -> str:
    """Dựng phần Text của 1 dòng Dialogue: pop-in + karaoke từng từ."""
    # Pop-in toàn dòng: bắt đầu 60% rồi phóng to về 100% trong 160ms + fade.
    prefix = r"{\fad(80,60)\fscx60\fscy60\t(0,160,\fscx100\fscy100)}"
    parts: list[str] = []
    for i, w in enumerate(line):
        # Giữ highlight tới khi từ KẾ tiếp bắt đầu -> tự nuốt khoảng lặng giữa từ.
        hl_end = line[i + 1].start if i + 1 < len(line) else w.end
        k = max(1, int(round((hl_end - w.start) * 100)))
        word_sec = term_sec if _is_term(w.text, terms) else sec
        # \1c = màu khi ĐÃ đọc (highlight); \2c = màu khi CHƯA đọc tới.
        parts.append(rf"{{\1c&H{prim}&\2c&H{word_sec}&\k{k}}}{w.text}")
    return prefix + " ".join(parts)


def write_karaoke_ass(
    segments: list[Segment],
    out_path: Path,
    *,
    video_w: int,
    video_h: int,
    font: str = "Arial",
    fontsize: int | None = None,
    max_words_per_line: int = 5,
    line_gap: float = 0.7,
    primary: str = YELLOW,
    base: str = WHITE,
    term_color: str = CYAN,
    margin_v: int | None = None,
    terms: set[str] | None = None,
) -> Path:
    """Sinh file phụ đề karaoke .ass.

    video_w/h : kích thước video (để PlayRes khớp, \\pos/scale đúng tỉ lệ).
    primary   : màu từ ĐANG đọc (mặc định vàng).
    base      : màu từ thường CHƯA đọc tới (trắng).
    term_color: màu thuật ngữ tiếng Anh chưa đọc tới (cyan) -> nổi bật để học.
    terms     : tập thuật ngữ tiếng Anh cần tô (chữ thường). None -> tự đoán.
    """
    if fontsize is None:
        fontsize = max(28, int(video_h * 0.045))   # ~ 86px ở video 1920 cao
    if margin_v is None:
        margin_v = int(video_h * 0.28)              # đặt caption ở ~1/3 dưới
    if terms:
        terms = {t.strip().lower() for t in terms if t.strip()}

    style = (
        f"Style: Default,{font},{fontsize},"
        f"&H00{primary}&,&H00{base}&,&H00000000&,&H64000000&,"
        # Bold=-1, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,
        f"-1,0,0,0,100,100,0,0,"
        # BorderStyle=1, Outline=4 (viền dày dễ đọc trên mặt người), Shadow=1,
        f"1,4,1,"
        # Alignment=2 (đáy-giữa), MarginL, MarginR, MarginV, Encoding=1
        f"2,40,40,{margin_v},1"
    )

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {video_w}",
        f"PlayResY: {video_h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
         "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding"),
        style,
        "",
        "[Events]",
        ("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
         "Effect, Text"),
    ]

    words = _flatten_words(segments)
    lines = _chunk_lines(words, max_words_per_line, line_gap)
    events: list[str] = []
    for i, ln in enumerate(lines):
        if not ln:
            continue
        end_t = ln[-1].end + 0.35   # giữ dòng thêm chút sau từ cuối
        if i + 1 < len(lines) and lines[i + 1]:
            # không để đè dòng kế -> hết hạn ngay trước khi dòng sau hiện
            end_t = min(end_t, lines[i + 1][0].start - 0.02)
        end_t = max(end_t, ln[-1].end)   # nhưng không sớm hơn lúc từ cuối kết thúc
        text = _line_to_dialogue(ln, terms, primary, base, term_color)
        events.append(
            f"Dialogue: 0,{_ass_ts(ln[0].start)},{_ass_ts(end_t)},Default,,0,0,0,,{text}"
        )

    out_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    return out_path
