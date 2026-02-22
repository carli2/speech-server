#!/usr/bin/env python3
"""
Calibrate BASE_F0_HZ from a reference WAV (e.g., Thorsten).

Usage:
  # Single file: show F0 percentiles + talking speed
  python tts-piper/calibrate_baseline.py --file tts-piper/x.wav [--seconds 5.0]

  # Source/target: compute mapping suggestions (speed/pitch)
  python tts-piper/calibrate_baseline.py --file thorsten.wav --target pia.wav

Outputs the estimated median F0 (Hz) so you can set BASE_F0_HZ
in piper_multi_server.py or via the environment variable BASE_F0_HZ.
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path
from typing import Optional

import numpy as np
try:
    import librosa  # type: ignore
    _HAS_LIBROSA = True
except Exception:
    _HAS_LIBROSA = False


def read_wav_head_samples(path: Path, seconds: float = 5.0):
    with wave.open(str(path), 'rb') as wf:
        sr = wf.getframerate()
        nchan = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        nframes = int(min(wf.getnframes(), seconds * sr))
        raw = wf.readframes(nframes)
    if sampwidth == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nchan > 1:
        x = x.reshape(-1, nchan).mean(axis=1)
    return sr, x


def estimate_f0_avg(sr: int, x: np.ndarray, fmin: float = 75.0, fmax: float = 400.0) -> Optional[float]:
    """Estimate median F0; try librosa.pyin if available, else ACF fallback."""
    if x.size == 0:
        return None
    if _HAS_LIBROSA:
        try:
            f0, _, _ = librosa.pyin(x, fmin=fmin, fmax=fmax, sr=sr)
            vals = f0[~np.isnan(f0)]
            vals = vals[vals > 0]
            if vals.size > 0:
                return float(np.median(vals))
        except Exception:
            pass
    # ACF fallback
    frame = int(sr * 0.05)
    hop = int(sr * 0.025)
    maxlag = int(sr / fmin)
    minlag = int(sr / fmax)
    vals = []
    for start in range(0, max(1, x.size - frame), hop):
        seg = x[start:start + frame]
        if seg.size < frame:
            break
        if np.sqrt(np.mean(seg * seg)) < 0.01:
            continue
        seg = seg - np.mean(seg)
        if np.allclose(seg, 0.0):
            continue
        acf = np.correlate(seg, seg, mode='full')[frame - 1:frame - 1 + maxlag + 1]
        acf[0] = 0.0
        lag = np.argmax(acf[minlag:maxlag + 1]) + minlag
        if lag > 0 and acf[lag] > 0:
            f0 = sr / lag
            if fmin <= f0 <= fmax:
                vals.append(f0)
    if not vals:
        return None
    return float(np.median(np.array(vals, dtype=np.float32)))


def spectral_centroid_track(sr: int, x: np.ndarray, fmin: float = 80.0, fmax: float = 2000.0) -> np.ndarray:
    n_fft = 1024
    hop = 512
    if x.size < n_fft:
        return np.array([], dtype=np.float32)
    win = np.hanning(n_fft).astype(np.float32)
    frames = 1 + (x.size - n_fft) // hop
    cents: list[float] = []
    fstep = sr / n_fft
    i0 = int(fmin / fstep)
    i1 = int(fmax / fstep)
    for i in range(frames):
        seg = x[i*hop:i*hop + n_fft]
        if seg.size < n_fft:
            break
        mag = np.abs(np.fft.rfft(seg * win))
        band = mag[i0:i1]
        if band.size == 0:
            continue
        freqs = np.linspace(i0*fstep, (i1-1)*fstep, num=band.size, dtype=np.float32)
        s = np.sum(band)
        if s <= 1e-9:
            continue
        cent = float(np.sum(freqs * band) / s)
        cents.append(cent)
    return np.array(cents, dtype=np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('path', nargs='?', help='Path to reference WAV (positional)')
    ap.add_argument('--file', '-f', type=str, default=None, help='Path to reference WAV (source if --target given)')
    ap.add_argument('--target', '-t', type=str, default=None, help='Target reference WAV to compare against')
    ap.add_argument('--seconds', '-s', type=float, default=5.0, help='Seconds to analyze from start')
    ap.add_argument('--blend', type=float, default=0.3, help='Blend weight a for F0 vs centroid (r_pitch = r_f0^a * r_cent^(1-a))')
    args = ap.parse_args()

    sel = args.file or args.path
    if sel:
        path = Path(sel)
    else:
        # Try common defaults
        candidates = [
            Path('tts-piper') / 'x.wav',
            Path('x.wav'),
            Path('voices') / 'x.wav',
            Path('voices') / 'default-voice.wav',
        ]
        path = None
        for c in candidates:
            if c.exists():
                path = c
                break
        if path is None:
            raise SystemExit('Reference file not found. Pass a path (positional or --file), or place x.wav in tts-piper/ or voices/.')
    path = path.resolve()

    sr, x = read_wav_head_samples(path, seconds=args.seconds)
    f0 = estimate_f0_avg(sr, x)
    if not f0:
        raise SystemExit('Could not estimate F0 (try a longer --seconds or a different file)')

    # Talking speed estimate
    try:
        if _HAS_LIBROSA:
            onset_env = librosa.onset.onset_strength(y=x, sr=sr)
            onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units='time')
            speed = float(len(onsets)) / max(1e-6, x.size / sr)
        else:
            n_fft = 1024; hop = 512
            frames = 1 + (x.size - n_fft) // hop
            mags = []
            win = np.hanning(n_fft).astype(np.float32)
            for i in range(max(0, frames)):
                seg = x[i*hop:i*hop+n_fft]
                if seg.size < n_fft: break
                mags.append(np.abs(np.fft.rfft(seg * win)))
            if mags:
                mags = np.stack(mags, axis=0)
                flux = np.maximum(0.0, mags[1:] - mags[:-1]).sum(axis=1)
                med = np.median(flux); mad = np.median(np.abs(flux - med)) + 1e-6
                thr = med + 1.0 * mad
                peaks = (flux[1:-1] > thr) & (flux[1:-1] > flux[:-2]) & (flux[1:-1] > flux[2:])
                events = int(np.count_nonzero(peaks))
                speed = events / max(1e-6, x.size / sr)
            else:
                speed = None
    except Exception:
        speed = None

    # Spectral centroid profile
    cents = spectral_centroid_track(sr, x)
    c50 = float(np.median(cents)) if cents.size > 0 else None

    print(f'Estimated F0 (median): {f0:.2f} Hz')
    if speed is not None:
        print(f'Estimated talking speed (events/sec): {speed:.3f}')
    if c50 is not None:
        print(f'Estimated spectral centroid (median): {c50:.2f} Hz')

    # If target is provided, compute mapping suggestions
    if args.target:
        tpath = Path(args.target).resolve()
        if not tpath.exists():
            raise SystemExit(f'Target file not found: {args.target}')
        sr_t, xt = read_wav_head_samples(tpath, seconds=args.seconds)
        f0_t = estimate_f0_avg(sr_t, xt) or 0.0
        cents_t = spectral_centroid_track(sr_t, xt)
        c50_t = float(np.median(cents_t)) if cents_t.size > 0 else 0.0
        if f0 <= 0.0 or f0_t <= 0.0 or c50 is None or c50_t <= 0.0:
            print('Insufficient data to compute mapping suggestions')
        else:
            r_f0 = f0_t / f0
            r_cent = c50_t / c50
            a = float(args.blend)
            r_pitch = (r_f0 ** a) * (r_cent ** (1.0 - a))
            st = 12.0 * np.log2(r_pitch)
            speed_f0 = r_f0
            speed_cent = r_cent
            speed_blend = (r_f0 ** 0.5) * (r_cent ** 0.5)
            print('--- Mapping suggestions ---')
            print(f'Pitch ratio F0: {r_f0:.3f}  Centroid: {r_cent:.3f}  Blended(a={a:.2f}): {r_pitch:.3f}  => shift {st:+.2f} st')
            print(f'Speed ratio F0: {speed_f0:.3f}  Centroid: {speed_cent:.3f}  Geom.mean: {speed_blend:.3f}')

    else:
        print('Set in server code (piper_multi_server.py):')
        print(f'  BASE_F0_HZ = {f0:.2f}')


if __name__ == '__main__':
    main()
