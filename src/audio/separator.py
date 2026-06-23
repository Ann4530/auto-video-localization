"""Tách giọng nói khỏi nhạc nền bằng Demucs (TUỲ CHỌN).

Mục đích: lấy track nhạc nền SẠCH (không còn giọng gốc) để làm nền cho giọng
Việt -> tránh nghe lẫn 2 giọng. Đây là cách pyvideotrans làm cho bản lồng tiếng
nghe gọn.

Gọi Demucs qua CLI (`python -m demucs`) thay vì import trực tiếp để không nạp
torch nặng vào tiến trình chính và để lỗi thiếu thư viện không làm sập pipeline.
Nếu chưa cài Demucs hoặc tách thất bại -> trả về None, pipeline sẽ tự dùng audio
gốc như bình thường.

Cài (tuỳ chọn):  pip install demucs
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..utils.logging import get_logger

log = get_logger("separator")


class VocalRemover:
    def __init__(self, model: str = "htdemucs"):
        self.model = model

    def instrumental(self, media: Path, work_dir: Path) -> Path | None:
        """Trả về đường dẫn track KHÔNG có giọng (no_vocals), hoặc None nếu thất bại."""
        out_dir = work_dir / "demucs"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "demucs",
            "-n", self.model,
            "--two-stems", "vocals",   # chỉ tách vocals vs phần còn lại -> nhanh hơn
            "-o", str(out_dir),
            str(media),
        ]
        log.info("Tách giọng nền (Demucs) cho %s ...", media.name)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            log.warning("Chưa cài Demucs (pip install demucs) -> dùng audio gốc.")
            return None
        if proc.returncode != 0:
            log.warning("Demucs lỗi -> dùng audio gốc.\n%s", proc.stderr[-1000:])
            return None

        # Demucs xuất: <out>/<model>/<tên file>/no_vocals.wav
        stem = media.stem
        candidate = out_dir / self.model / stem / "no_vocals.wav"
        if candidate.exists():
            return candidate
        # Phòng khi cấu trúc thư mục khác phiên bản -> dò tìm.
        found = list(out_dir.rglob("no_vocals.*"))
        if found:
            return found[0]
        log.warning("Không tìm thấy track no_vocals -> dùng audio gốc.")
        return None
