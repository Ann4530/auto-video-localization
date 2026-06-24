"""Ước lượng giới tính người nói theo cao độ giọng (pitch F0) cho từng đoạn.

Mục đích: gán giọng lồng tiếng Việt khác nhau cho nam / nữ tương ứng với
người nói gốc. Chỉ dùng ffmpeg + numpy (không cần model nặng / token).

Cách làm:
  1. Giải mã toàn bộ audio gốc 1 lần thành PCM mono 16kHz.
  2. Với mỗi đoạn, cắt ra, chia khung ~40ms, ước lượng F0 bằng tự tương quan
     (autocorrelation), lấy trung vị F0 các khung có giọng.
  3. F0 thấp -> nam, F0 cao -> nữ (ngưỡng cấu hình được, mặc định ~165Hz).

Lưu ý: nhạc nền / nhiễu có thể làm sai lệch -> độ chính xác vừa phải.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..transcribe.transcriber import Segment
from ..utils.logging import get_logger

log = get_logger("gender")

_SR = 16000


def _load_audio(path: Path) -> np.ndarray:
    """Giải mã audio -> mảng float32 mono 16kHz trong khoảng [-1, 1]."""
    cmd = [
        "ffmpeg", "-i", str(path),
        "-f", "s16le", "-ac", "1", "-ar", str(_SR),
        "-loglevel", "quiet", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    raw = np.frombuffer(proc.stdout, dtype=np.int16)
    return raw.astype(np.float32) / 32768.0


def _f0_of_frame(frame: np.ndarray, fmin: float = 70, fmax: float = 400) -> float:
    """F0 của 1 khung bằng tự tương quan; 0 nếu là khoảng lặng/không có giọng."""
    frame = frame - frame.mean()
    rms = float(np.sqrt(np.mean(frame ** 2)))
    if rms < 1e-3:  # quá nhỏ -> coi như im lặng
        return 0.0
    corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
    lag_min = int(_SR / fmax)
    lag_max = int(_SR / fmin)
    if lag_max >= len(corr) or lag_min < 1:
        return 0.0
    window = corr[lag_min:lag_max]
    if window.size == 0 or corr[0] <= 0:
        return 0.0
    peak = int(np.argmax(window)) + lag_min
    # Độ "có giọng": đỉnh tự tương quan phải đủ mạnh so với năng lượng.
    if corr[peak] / corr[0] < 0.3:
        return 0.0
    return _SR / peak


def _segment_f0(audio: np.ndarray, start: float, end: float) -> float:
    """Trung vị F0 các khung có giọng trong đoạn [start, end] (giây); 0 nếu không có."""
    a = int(max(0.0, start) * _SR)
    b = int(min(len(audio) / _SR, end) * _SR)
    clip = audio[a:b]
    if clip.size < _SR // 20:  # ngắn hơn ~50ms
        return 0.0
    win = int(0.04 * _SR)   # khung 40ms
    hop = int(0.02 * _SR)   # bước 20ms
    f0s: list[float] = []
    for s in range(0, len(clip) - win, hop):
        f0 = _f0_of_frame(clip[s : s + win])
        if f0 > 0:
            f0s.append(f0)
    if not f0s:
        return 0.0
    return float(np.median(f0s))


def detect_genders(
    media_path: Path,
    segments: list[Segment],
    threshold: float = 165.0,
    default: str = "female",
) -> list[str]:
    """Trả về danh sách "male"/"female" cho từng segment.

    Đoạn không xác định được (nhạc/im lặng) sẽ kế thừa giới tính đoạn trước,
    hoặc dùng `default` nếu chưa có đoạn nào xác định.
    """
    log.info("Ước lượng giới tính người nói cho %d đoạn", len(segments))
    audio = _load_audio(media_path)
    genders: list[str] = []
    last = default
    n_male = n_female = n_unknown = 0
    for seg in segments:
        f0 = _segment_f0(audio, seg.start, seg.end)
        if f0 <= 0:
            g = last
            n_unknown += 1
        else:
            g = "male" if f0 < threshold else "female"
            last = g
            if g == "male":
                n_male += 1
            else:
                n_female += 1
        genders.append(g)
    log.info(
        "Giới tính: %d nam, %d nữ, %d không rõ (kế thừa)", n_male, n_female, n_unknown
    )
    return genders
