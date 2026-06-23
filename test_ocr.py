"""Test nhanh riêng phần OCR overlay (không chạy Whisper/TTS).

Chạy: python test_ocr.py "duong_dan_video.mp4"
Kết quả: data/output/ocr_test.mp4 (chỉ đè chữ Việt, giữ audio gốc).
"""
import subprocess
import sys
from pathlib import Path

from src.config import load_config
from src.ocr.screen_text import ScreenTextTranslator
from src.translate.translator import make_translator

cfg = load_config()
video = Path(sys.argv[1])
work = cfg.work_dir / "ocr_test"
work.mkdir(parents=True, exist_ok=True)

translator = make_translator(
    provider=cfg.translate.get("provider", "gemini"),
    anthropic_key=cfg.env("ANTHROPIC_API_KEY"),
    gemini_key=cfg.env("GEMINI_API_KEY"),
    model=cfg.translate.get("model", ""),
    target_language="Tiếng Việt",
    style=cfg.translate.get("style", "tự nhiên"),
)

stt = ScreenTextTranslator(
    translator=translator,
    sample_fps=cfg.ocr.get("sample_fps", 2.0),
    min_score=cfg.ocr.get("min_score", 0.6),
    only_cjk=cfg.ocr.get("only_cjk", True),
    font_path=cfg.ocr.get("font", "C:/Windows/Fonts/arial.ttf"),
)
overlays = stt.process(video, work)

# In thử các cặp dịch
for ov in overlays:
    print(f"  t={ov.start:.1f}-{ov.end:.1f} @({ov.x},{ov.y}) -> {ov.png}")

# Ghép overlay lên video (giữ nguyên audio gốc)
out = cfg.output_dir / "ocr_test.mp4"
inputs = ["-i", str(video)]
parts = []
cur = "[0:v]"
for i, ov in enumerate(overlays):
    inputs += ["-i", ov.png]
    lbl = f"[v{i}]"
    parts.append(f"{cur}[{i+1}:v]overlay={ov.x}:{ov.y}:enable='between(t,{ov.start:.2f},{ov.end:.2f})'{lbl}")
    cur = lbl
vmap = cur if overlays else "0:v"
cmd = ["ffmpeg", "-y", *inputs]
if parts:
    cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", "0:a?"]
else:
    cmd += ["-map", "0:v", "-map", "0:a?"]
cmd += ["-c:v", "libx264", "-crf", "20", "-c:a", "copy", "-shortest", str(out)]
r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
if r.returncode != 0:
    print("FFMPEG ERR:\n", r.stderr[-1500:])
else:
    print("OK ->", out)
