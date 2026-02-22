from __future__ import annotations

import subprocess as _sp
import wave as _wave
from pathlib import Path
from typing import Optional, Tuple

import numpy as _np


def ffprobe_duration_sec(src: str) -> Optional[float]:
    try:
        out = _sp.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                src,
            ],
            stderr=_sp.DEVNULL,
        )
        s = out.decode("utf-8", "ignore").strip()
        if s:
            return float(s)
    except Exception:
        return None
    return None


def ffmpeg_to_pcm16(in_path: Path, out_path: Path, sample_rate: Optional[int] = None) -> bool:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(in_path), "-c:a", "pcm_s16le"]
    if sample_rate and sample_rate > 0:
        cmd += ["-ar", str(int(sample_rate))]
    cmd.append(str(out_path))
    try:
        _sp.check_call(cmd)
        return True
    except Exception:
        return False


def read_wav_all_samples(path: Path) -> Tuple[int, _np.ndarray]:
    with _wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        nchan = wf.getnchannels()
        sw = wf.getsampwidth()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if sw == 2:
        x = _np.frombuffer(raw, dtype=_np.int16).astype(_np.float32) / 32768.0
    elif sw == 1:
        x = (_np.frombuffer(raw, dtype=_np.uint8).astype(_np.float32) - 128.0) / 128.0
    else:
        x = _np.frombuffer(raw, dtype=_np.int16).astype(_np.float32) / 32768.0
    if nchan > 1:
        x = x.reshape(-1, nchan).mean(axis=1)
    return sr, x


def estimate_f0_avg(sr: int, x: _np.ndarray, fmin: float = 75.0, fmax: float = 400.0) -> Optional[float]:
    if x.size == 0:
        return None
    frame = int(sr * 0.05)
    hop = int(sr * 0.025)
    maxlag = int(sr / fmin)
    minlag = int(sr / fmax)
    vals: list[float] = []
    for start in range(0, max(1, x.size - frame), hop):
        seg = x[start : start + frame]
        if seg.size < frame:
            break
        if _np.sqrt(_np.mean(seg * seg)) < 0.01:
            continue
        seg = seg - _np.mean(seg)
        if _np.allclose(seg, 0):
            continue
        acf = _np.correlate(seg, seg, mode="full")[frame - 1 : frame - 1 + maxlag + 1]
        acf[0] = 0.0
        lag = _np.argmax(acf[minlag : maxlag + 1]) + minlag
        if lag > 0 and acf[lag] > 0:
            f0 = sr / lag
            if fmin <= f0 <= fmax:
                vals.append(f0)
    if not vals:
        return None
    return float(_np.median(_np.array(vals, dtype=_np.float32)))

