"""Dịch chữ HIỂN THỊ TRÊN MÀN HÌNH (hardcoded text) sang tiếng Việt.

Quy trình:
  1. Lấy mẫu khung hình theo sample_fps.
  2. OCR mỗi khung (RapidOCR) -> các hộp chữ + toạ độ.
  3. Gom các lần xuất hiện giống nhau theo thời gian thành "sự kiện chữ"
     (text + hộp + [start, end]) để tránh dịch lặp và nhấp nháy.
  4. Dịch các đoạn chữ (Gemini) — chỉ dịch đoạn có ký tự CJK nếu cấu hình vậy.
  5. Render PNG khung trắng + chữ Việt đen vừa hộp (PIL).
  6. Trả danh sách overlay để compositor ghép đè lên video.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..translate.translator import BaseTranslator
from ..transcribe.transcriber import Segment
from ..utils.logging import get_logger

log = get_logger("ocr")

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


def has_cjk(s: str) -> bool:
    return bool(_CJK.search(s))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


@dataclass
class TextEvent:
    text: str                      # chữ gốc (đã ghép qua các frame)
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 (bounding rect)
    start: float
    end: float
    samples: int = 1
    vi: str = ""                    # bản dịch tiếng Việt
    png: str = ""                   # đường dẫn ảnh overlay


@dataclass
class Overlay:
    png: str
    x: int
    y: int
    start: float
    end: float


def _bbox(quad) -> tuple[int, int, int, int]:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _similar(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() > 0.7


class ScreenTextTranslator:
    def __init__(
        self,
        translator: BaseTranslator,
        sample_fps: float = 2.0,
        min_score: float = 0.6,
        only_cjk: bool = True,
        font_path: str = "C:/Windows/Fonts/arial.ttf",
        min_duration: float = 0.4,
        ocr_max_width: int = 960,
        scene_diff: float = 2.5,
    ):
        from rapidocr_onnxruntime import RapidOCR

        self.translator = translator
        self.sample_fps = sample_fps
        self.min_score = min_score
        self.only_cjk = only_cjk
        self.font_path = font_path
        self.min_duration = min_duration
        self.ocr_max_width = ocr_max_width   # thu nhỏ khung trước khi OCR cho nhanh
        self.scene_diff = scene_diff         # ngưỡng coi 2 khung là "cùng cảnh"
        log.info("Nạp RapidOCR")
        self._ocr = RapidOCR()

    # --- B1-3: quét + gom sự kiện chữ ---------------------------------
    def _scan(self, video_path: Path) -> list[TextEvent]:
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = total / fps if fps else 0
        step = 1.0 / self.sample_fps
        log.info("Quét OCR: %.1fs, lấy mẫu %.1f fps", duration, self.sample_fps)

        active: list[TextEvent] = []
        done: list[TextEvent] = []
        t = 0.0
        gap = step * 2.5  # cho phép hụt vài frame vẫn coi là cùng 1 chữ
        prev_small = None
        scanned = skipped = 0

        while duration == 0 or t <= duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break

            # Bỏ qua khung gần như giống khung trước (cùng cảnh) -> chỉ kéo
            # dài các sự kiện đang mở, không OCR lại cho nhanh.
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (160, 90))
            if prev_small is not None:
                diff = float(np.mean(np.abs(small.astype(np.int16) - prev_small)))
                if diff < self.scene_diff and active:
                    for ev in active:
                        ev.end = t
                    skipped += 1
                    t += step
                    continue
            prev_small = small.astype(np.int16)

            # Thu nhỏ khung trước khi OCR (nhanh hơn nhiều), rồi map toạ độ về gốc
            h0, w0 = frame.shape[:2]
            scale = min(1.0, self.ocr_max_width / w0)
            ocr_img = cv2.resize(frame, (int(w0 * scale), int(h0 * scale))) if scale < 1 else frame
            result, _ = self._ocr(ocr_img)
            scanned += 1
            inv = 1.0 / scale
            dets = []
            for quad, text, score in (result or []):
                if scale < 1:
                    quad = [[p[0] * inv, p[1] * inv] for p in quad]
                if score < self.min_score:
                    continue
                if self.only_cjk and not has_cjk(text):
                    continue
                if len(_norm(text)) < 1:
                    continue
                dets.append((_bbox(quad), text))

            # khớp với sự kiện đang mở
            for box, text in dets:
                matched = None
                for ev in active:
                    if _similar(ev.text, text):
                        # gần nhau về vị trí
                        c1, c2 = _center(ev.box), _center(box)
                        if abs(c1[0] - c2[0]) < frame.shape[1] * 0.25 and \
                           abs(c1[1] - c2[1]) < frame.shape[0] * 0.12:
                            matched = ev
                            break
                if matched:
                    matched.end = t
                    matched.samples += 1
                    # mở rộng hộp bao
                    x1 = min(matched.box[0], box[0]); y1 = min(matched.box[1], box[1])
                    x2 = max(matched.box[2], box[2]); y2 = max(matched.box[3], box[3])
                    matched.box = (x1, y1, x2, y2)
                    if len(text) > len(matched.text):
                        matched.text = text
                else:
                    active.append(TextEvent(text=text, box=box, start=t, end=t))

            # đóng các sự kiện đã lâu không thấy
            still: list[TextEvent] = []
            for ev in active:
                if t - ev.end > gap:
                    done.append(ev)
                else:
                    still.append(ev)
            active = still
            t += step

        done.extend(active)
        cap.release()
        # lọc sự kiện quá ngắn (nhiễu)
        events = [e for e in done if (e.end - e.start) >= self.min_duration or e.samples >= 2]
        log.info(
            "OCR %d khung (bỏ qua %d khung trùng cảnh) -> %d vùng chữ",
            scanned, skipped, len(events),
        )
        return events

    # --- B4: dịch -----------------------------------------------------
    def _translate(self, events: list[TextEvent]) -> None:
        if not events:
            return
        vi_list = self.translator.translate_texts([e.text for e in events])
        for ev, vi in zip(events, vi_list):
            ev.vi = vi.strip()

    # --- B5: render PNG khung trắng + chữ đen -------------------------
    def _render(self, events: list[TextEvent], out_dir: Path, vid_w: int, vid_h: int) -> list[Overlay]:
        out_dir.mkdir(parents=True, exist_ok=True)
        overlays: list[Overlay] = []
        for i, ev in enumerate(events):
            if not ev.vi:
                continue
            x1, y1, x2, y2 = ev.box
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            orig_h = max(12, y2 - y1)
            png = out_dir / f"ovl_{i:04d}.png"
            bw, bh = self._draw_box(ev.vi, orig_h, vid_w, vid_h, png)
            # đặt khung sao cho TÂM trùng tâm chữ gốc -> che đúng chỗ
            x = int(min(max(0, cx - bw / 2), max(0, vid_w - bw)))
            y = int(min(max(0, cy - bh / 2), max(0, vid_h - bh)))
            overlays.append(Overlay(png=str(png), x=x, y=y, start=ev.start, end=ev.end + 0.3))
        return overlays

    def _draw_box(self, text: str, orig_h: int, vid_w: int, vid_h: int, out: Path) -> tuple[int, int]:
        """Render khung tự co giãn theo chữ Việt, cỡ chữ dựa trên chiều cao
        chữ gốc. Trả về (rộng, cao) của khung."""
        measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        margin = 10
        max_w = int(vid_w * 0.9) - 2 * margin
        max_total_h = int(vid_h * 0.45)

        # cỡ chữ mục tiêu ~ chiều cao chữ gốc, kẹp trong [18, 46]
        target = max(18, min(46, int(orig_h * 0.95)))
        font = lines = None
        line_h = 0
        for size in range(target, 13, -2):
            font = ImageFont.truetype(self.font_path, size)
            lines = self._wrap(text, font, max_w, measure)
            asc, desc = font.getmetrics()
            line_h = asc + desc + 4
            total_h = line_h * len(lines)
            widest = max((measure.textlength(ln, font=font) for ln in lines), default=0)
            if total_h <= max_total_h and widest <= max_w:
                break

        widest = max((measure.textlength(ln, font=font) for ln in lines), default=1)
        bw = int(min(max_w, widest) + 2 * margin)
        bh = int(line_h * len(lines) + 2 * margin)

        img = Image.new("RGBA", (bw, bh), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        y = margin
        for ln in lines:
            lw = draw.textlength(ln, font=font)
            draw.text(((bw - lw) / 2, y), ln, fill=(0, 0, 0, 255), font=font)
            y += line_h
        img.save(out)
        return bw, bh

    @staticmethod
    def _wrap(text: str, font, max_w: int, draw) -> list[str]:
        words = text.split()
        if not words:
            return [text]
        lines, cur = [], ""
        for wd in words:
            trial = (cur + " " + wd).strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines

    # --- cache kết quả quét (quét OCR rất tốn thời gian) --------------
    def _load_scan(self, cache: Path) -> list[TextEvent] | None:
        if not cache.exists():
            return None
        try:
            import json
            data = json.loads(cache.read_text(encoding="utf-8"))
            return [TextEvent(text=d["text"], box=tuple(d["box"]),
                              start=d["start"], end=d["end"], samples=d.get("samples", 1))
                    for d in data]
        except Exception:  # noqa: BLE001
            return None

    def _save_scan(self, cache: Path, events: list[TextEvent]) -> None:
        import json
        cache.write_text(json.dumps(
            [{"text": e.text, "box": list(e.box), "start": e.start,
              "end": e.end, "samples": e.samples} for e in events],
            ensure_ascii=False, indent=1), encoding="utf-8")

    # --- API chính ----------------------------------------------------
    def process(self, video_path: Path, work_dir: Path, use_cache: bool = True) -> list[Overlay]:
        cap = cv2.VideoCapture(str(video_path))
        vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        work_dir.mkdir(parents=True, exist_ok=True)
        cache = work_dir / "ocr_scan.json"
        events = self._load_scan(cache) if use_cache else None
        if events is not None:
            log.info("Dùng lại kết quả quét OCR đã cache (%d vùng)", len(events))
        else:
            events = self._scan(video_path)
            self._save_scan(cache, events)

        self._translate(events)
        overlays = self._render(events, work_dir / "ocr_overlays", vid_w, vid_h)
        log.info("Tạo %d overlay chữ", len(overlays))
        return overlays
