#!/usr/bin/env python3
"""
Multi-voice Piper HTTP server with realtime streaming and CORS.

Install (Python packages)
- Flask (web server)
    pip install Flask
- Piper (Python bindings for ONNX voices)
    Option A: install your local sources
      cd /home/carli/sources/piper
      pip install -e .
    Option B: if publishing is available, install from PyPI (name may differ)

Runtime dependencies (typical)
- onnxruntime or onnxruntime-gpu (choose one; GPU recommended)
    pip install onnxruntime
    # or
    pip install onnxruntime-gpu
- Coqui TTS (for optional FreeVC voice conversion)
    pip install TTS
- PyTorch (required by Coqui TTS; choose the right build for your CUDA/CPU)
    # CPU example
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    # CUDA example (adjust version)
    pip install torch --index-url https://download.pytorch.org/whl/cu121
- espeak-ng (system package) for phonemization used by many voices
    Debian/Ubuntu: sudo apt-get install espeak-ng

Quick start
- Install Python deps:
    pip install -r tts-piper/requirements.txt
    pip install -e /home/carli/sources/piper

Features
- Serves multiple voices/languages discovered from one or more scan directories.
- Similar endpoints to Piper's built-in http_server, plus CORS and language selection.
- Always streams realtime audio using a pipeline of stages.

Endpoints
- GET  /healthz                 -> 200 OK
- GET  /voices                  -> JSON map of available voices and metadata
- POST /                        -> JSON {text, voice?, lang?, speaker?, speaker_id?, length_scale?, noise_scale?, noise_w_scale?, sentence_silence?, voice2?, sound?, pitch_st?, pitch_factor?, pitch_disable?}

Run examples
  # auto-scan common folders for *.onnx voices
  python3 tts-piper/piper_multi_server.py --host 0.0.0.0 --port 5000

  # explicit scan dir
  python3 tts-piper/piper_multi_server.py --scan-dir . --scan-dir ../voices --scan-dir ../voices-piper

Maintainer notes
- Keep the request/response shape aligned with /home/carli/sources/piper/src/piper/http_server.py
- If Piper adds fields to SynthesisConfig or the API, mirror them here.
- CORS is enabled globally (after_request) to simplify browser usage.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import mimetypes, os

from flask import Flask, Response, jsonify, request, stream_with_context

# Ensure Piper sources are discoverable if installed from local sources
import sys as _sys
from pathlib import Path as _Path
_candidates = [
    _Path('/home/carli/sources/piper/src'),
    (_Path(__file__).resolve().parents[2] / 'sources' / 'piper' / 'src'),
]
for _p in _candidates:
    try:
        if _p.exists():
            _sys.path.insert(0, str(_p))
    except Exception:
        pass

# Optional: FreeVC via Coqui TTS for voice conversion
# FreeVC availability and initialization handled inside VCConverter stage


_LOGGER = logging.getLogger("piper-multi-server")

# Baseline constant for Thorsten (low, slow)
# Calibrated via calibrate_baseline.py on your x.wav
BASE_F0_HZ = 134.45

# Post-VC pitch damping factor (0..1). VC already nudges pitch; apply only a portion.
PITCH_CORRECTION = 0.5

# Default: do not disable pitch; can be overridden per-request via disable_pitch=1
PITCH_DISABLE_DEFAULT = False

# Chunk size used for VC/ffmpeg processing steps (seconds)
CHUNKSIZE_SECONDS = 10.0

def create_app(args: argparse.Namespace) -> Flask:
    # Voices live in a single folder (default: ./voices-piper). Allow override via --voices-path.
    # Back-compat: --scan-dir (single) behaves like --voices-path
    voices_arg = getattr(args, 'scan_dir', None) or getattr(args, 'voices_path', 'voices-piper')
    voices_dir = Path(voices_arg).resolve()
    _LOGGER.info("Voices dir: %s", voices_dir)
    # Use TTSRegistry for voice discovery, caching, and loading
    from lib.registry import TTSRegistry, load_voice_info, VoiceInfo  # type: ignore
    registry = TTSRegistry(voices_dir, use_cuda=args.cuda,
                           voice_ttl_seconds=int(getattr(args, 'voice_ttl_seconds', 7200)),
                           voice_cache_max=int(getattr(args, 'voice_cache_max', 64)))
    _LOGGER.info("Discovered %d voices", len(registry.index))
    # VC handled inside VCConverter; no global service here

    def ensure_loaded(model_id: str):
        return registry.ensure_loaded(model_id)

    # Preload default voice if provided
    default_model_id: Optional[str] = None
    if args.model:
        default_model_path = Path(args.model)
        if not default_model_path.exists():
            raise SystemExit(f"Model not found: {default_model_path}")
        default_model_id = default_model_path.name.rstrip(".onnx")
        _ = registry.ensure_loaded(default_model_id)
        registry.index.setdefault(default_model_id, default_model_path)

    # If no explicit default, pick the first discovered voice id (stable order)
    if (default_model_id is None) and registry.index:
        prefer = 'de_DE-thorsten-medium'
        default_model_id = prefer if prefer in registry.index else sorted(registry.index.keys())[0]

    app = Flask(__name__)
    # Ensure our module logger emits at desired level and propagates to root handler
    try:
        _LOGGER.setLevel(logging.DEBUG if args.debug else logging.INFO)
        _LOGGER.propagate = True
    except Exception:
        pass

    # (processing helpers removed; handled by stages/util.py)

    # (ffmpeg/ffprobe helpers removed)

    # Import pipeline stages
    try:
        # Allow importing from local lib/ directory
        import sys as _sys
        here = Path(__file__).resolve().parent
        _sys.path.insert(0, str(here / 'lib'))
        _sys.path.insert(0, str(here))
        from lib import AudioReader, VCConverter, PitchAdjuster, ResponseWriter, FileFetcher, RawResponseWriter  # type: ignore
    except Exception as _e:
        _LOGGER.warning('lib import failed: %s', _e)

    # (ffmpeg filter/run helpers removed)

    def _ffmpeg_pitch_shift(in_path: Path, out_path: Path, semitones: float, stop_check: Optional[Callable[[], bool]] = None) -> bool:
        if not _ffmpeg_exists():
            return False
        # factor >1 raises pitch; try asetrate + aresample + atempo to maintain duration
        factor = 2.0 ** (semitones / 12.0)
        # Prefer high-quality formant-preserving rubberband if available
        if _ffmpeg_has_filter('rubberband'):
            rb = f"rubberband=tempo=1.0:pitch={factor}:formant=1"
            cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(in_path), '-filter:a', rb, str(out_path)]
            _LOGGER.info("ffmpeg rubberband pitch: st=%.3f factor=%.5f cmd=%s", semitones, factor, ' '.join(cmd))
            if _run_ffmpeg_cmd(cmd, stop_check=stop_check):
                return True
            _LOGGER.warning("ffmpeg rubberband failed; falling back to asetrate/atempo")
        # Build chain of atempo filters to realize tempo=1/factor within 0.5..2.0 segments
        tempo = float(1.0 / factor)
        atempo_filters: List[str] = []
        if tempo <= 0:
            _LOGGER.warning("invalid tempo computed for pitch shift: %s", tempo)
            return False
        if tempo < 1.0:
            # Compose from 0.5 steps up to residual in [0.5, 1.0]
            remaining = tempo
            while remaining < 0.5:
                atempo_filters.append('atempo=0.5')
                remaining /= 0.5
            atempo_filters.append(f'atempo={remaining}')
        else:
            # Compose from 2.0 steps down to residual in [1.0, 2.0]
            remaining = tempo
            while remaining > 2.0:
                atempo_filters.append('atempo=2.0')
                remaining /= 2.0
            atempo_filters.append(f'atempo={remaining}')
        atempo_chain = ','.join(atempo_filters) if atempo_filters else ''
        # Use numeric input sample rate for stability across ffmpeg versions
        try:
            with _wave.open(str(in_path), 'rb') as _wf:
                in_sr = int(_wf.getframerate())
        except Exception:
            in_sr = None  # let ffmpeg infer; fall back to symbolic 'sample_rate' if available
        if in_sr and in_sr > 0:
            filt = f"asetrate={int(in_sr * factor)},aresample={in_sr}"
        else:
            # Fallback; most ffmpeg builds support 'sample_rate' variable in expressions
            filt = f"asetrate=sample_rate*{factor},aresample=sample_rate"
        if atempo_chain:
            filt = f"{filt},{atempo_chain}"
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(in_path), '-filter:a', filt, str(out_path)]
        _LOGGER.info("ffmpeg pitch: st=%.3f factor=%.5f tempo=%.5f cmd=%s", semitones, factor, tempo, ' '.join(cmd))
        return _run_ffmpeg_cmd(cmd, stop_check=stop_check)

    def _ffmpeg_change_speed(in_path: Path, out_path: Path, speed: float) -> bool:
        if not _ffmpeg_exists():
            return False
        spd = float(speed)
        if spd <= 0:
            _LOGGER.warning("invalid speed factor: %s", speed)
            return False
        # Build chain of atempo filters to realize any factor using 0.5..2.0 segments
        filters: List[str] = []
        remaining = spd
        if spd < 1.0:
            while remaining < 0.5:
                filters.append('atempo=0.5')
                remaining /= 0.5
            filters.append(f'atempo={remaining}')
        else:
            while remaining > 2.0:
                filters.append('atempo=2.0')
                remaining /= 2.0
            filters.append(f'atempo={remaining}')
        filt = ','.join(filters)
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(in_path), '-filter:a', filt, str(out_path)]
        try:
            _sp.run(cmd, check=True)
            return True
        except Exception as e:
            _LOGGER.warning("ffmpeg speed change failed: %s", e)
            return False

    def _ffmpeg_resample_mono_pad(in_path: Path, out_path: Path, sample_rate: int = 24000, pad_seconds: float = 0.2, stop_check: Optional[Callable[[], bool]] = None) -> bool:
        """Resample to mono at sample_rate and append short silence to avoid VC kernel-size errors."""
        if not _ffmpeg_exists():
            return False
        if sample_rate <= 0:
            sample_rate = 24000
        # apad pad_dur adds specified seconds of silence; keeps original audio
        filt = f"apad=pad_dur={max(0.0, float(pad_seconds))}"
        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-i', str(in_path),
            '-ac', '1', '-ar', str(int(sample_rate)),
            '-filter:a', filt,
            '-c:a', 'pcm_s16le',
            str(out_path)
        ]
        _LOGGER.info("ffmpeg resample/mono: %s -> %s @ %d Hz", in_path, out_path, sample_rate)
        return _run_ffmpeg_cmd(cmd, stop_check=stop_check)

    def _ffmpeg_to_pcm16(in_path: Path, out_path: Path, sample_rate: Optional[int] = None, stop_check: Optional[Callable[[], bool]] = None) -> bool:
        """Force WAV PCM16 output (and optionally set sample rate)."""
        if not _ffmpeg_exists():
            return False
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(in_path), '-c:a', 'pcm_s16le']
        if sample_rate and sample_rate > 0:
            cmd += ['-ar', str(int(sample_rate))]
        cmd.append(str(out_path))
        _LOGGER.info("Converting to PCM16: %s -> %s%s", in_path, out_path, f" @{sample_rate}Hz" if sample_rate else "")
        return _run_ffmpeg_cmd(cmd, stop_check=stop_check)

    def _split_wav_pcm16(in_path: Path, chunk_seconds: float) -> List[Path]:
        """Split a PCM16 WAV into ~chunk_seconds chunks. Returns list of temp file paths."""
        import tempfile
        out_paths: List[Path] = []
        with _wave.open(str(in_path), 'rb') as wf:
            nchan = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            if nchan < 1 or sw != 2 or sr <= 0:
                raise RuntimeError('split_wav_pcm16: input must be PCM16 WAV')
            frames_per_chunk = int(max(1, sr * max(0.1, float(chunk_seconds))))
            while True:
                frames = wf.readframes(frames_per_chunk)
                if not frames:
                    break
                tmp = tempfile.NamedTemporaryFile(prefix='chunk_', suffix='.wav', delete=False)
                tmp_path = Path(tmp.name)
                tmp.close()
                with _wave.open(str(tmp_path), 'wb') as ww:
                    ww.setnchannels(nchan)
                    ww.setsampwidth(sw)
                    ww.setframerate(sr)
                    ww.writeframes(frames)
                out_paths.append(tmp_path)
        _LOGGER.info('split wav: %s -> %d chunks (~%.1fs each)', in_path, len(out_paths), chunk_seconds)
        return out_paths

    def _concat_wavs_pcm16(paths: List[Path], out_path: Path) -> bool:
        """Concatenate PCM16 WAVs into out_path."""
        if not paths:
            return False
        try:
            with _wave.open(str(paths[0]), 'rb') as wf0:
                nchan = wf0.getnchannels(); sw = wf0.getsampwidth(); sr = wf0.getframerate()
            with _wave.open(str(out_path), 'wb') as ww:
                ww.setnchannels(nchan); ww.setsampwidth(sw); ww.setframerate(sr)
                for p in paths:
                    with _wave.open(str(p), 'rb') as wf:
                        ww.writeframes(wf.readframes(wf.getnframes()))
            _LOGGER.info('concat wav: %d chunks -> %s', len(paths), out_path)
            return True
        except Exception as e:
            _LOGGER.warning('concat wav failed: %s', e)
            return False

    def _vc_convert_chunkwise(src_wav: Path, tgt_wav: Path, out_path: Path, chunk_seconds: float = CHUNKSIZE_SECONDS) -> bool:
        """Run FreeVC on src_wav in chunks and concatenate results to out_path (PCM16)."""
        import tempfile, os
        # Ensure inputs are PCM16 24k mono
        src_pcm = tempfile.NamedTemporaryFile(prefix='vc_src_pcm_', suffix='.wav', delete=False); src_pcm_path = Path(src_pcm.name); src_pcm.close()
        if not _ffmpeg_to_pcm16(src_wav, src_pcm_path, sample_rate=24000):
            _LOGGER.warning('vc chunkwise: src pcm16 conversion failed; using original')
            src_pcm_path = src_wav
        try:
            chunks = _split_wav_pcm16(src_pcm_path, chunk_seconds)
            out_chunks: List[Path] = []
            for i, c in enumerate(chunks):
                tmp = tempfile.NamedTemporaryFile(prefix=f'vc_chunk_{i:04d}_', suffix='.wav', delete=False)
                c_out = Path(tmp.name); tmp.close()
                try:
                    get_vc_model().voice_conversion_to_file(source_wav=str(c), target_wav=str(tgt_wav), file_path=str(c_out))  # type: ignore
                except Exception as e:
                    _LOGGER.warning('vc chunk %d failed: %s', i, e)
                    # Best-effort: pass through source chunk
                    c_out = c
                # Normalize each chunk to PCM16 24k for concatenation
                c_out_pcm = tempfile.NamedTemporaryFile(prefix=f'vc_chunk_pcm_{i:04d}_', suffix='.wav', delete=False)
                c_out_pcm_path = Path(c_out_pcm.name); c_out_pcm.close()
                if not _ffmpeg_to_pcm16(c_out, c_out_pcm_path, sample_rate=24000):
                    c_out_pcm_path = c_out
                out_chunks.append(c_out_pcm_path)
            ok = _concat_wavs_pcm16(out_chunks, out_path)
            return ok
        finally:
            try:
                if src_pcm_path != src_wav and src_pcm_path.exists():
                    os.unlink(src_pcm_path)
            except Exception:
                pass

    def _vc_pitch_concat_chunkwise(src_wav: Path, tgt_wav: Path, out_path: Path, semitones_override: Optional[float], chunk_seconds: float = CHUNKSIZE_SECONDS) -> bool:
        """VC + (optional) pitch per chunk, concatenate directly to out_path (PCM16@24k)."""
        import tempfile, os
        # Prepare source as PCM16 24k mono
        src_pcm = tempfile.NamedTemporaryFile(prefix='vc_src_pcm_', suffix='.wav', delete=False); src_pcm_path = Path(src_pcm.name); src_pcm.close()
        if not _ffmpeg_resample_mono_pad(src_wav, src_pcm_path, sample_rate=24000, pad_seconds=0.0):
            if not _ffmpeg_to_pcm16(src_wav, src_pcm_path, sample_rate=24000):
                src_pcm_path = src_wav
        # Estimate target baseline once (use full first chunk duration)
        try:
            sr_t, x_t = _read_wav_head_samples(tgt_wav, seconds=float(chunk_seconds))
            f0_t = _estimate_f0_avg(sr_t, x_t)
        except Exception:
            f0_t = None
        try:
            chunks = _split_wav_pcm16(src_pcm_path, chunk_seconds)
            # Open writer
            with _wave.open(str(out_path), 'wb') as ww:
                ww.setnchannels(1); ww.setsampwidth(2); ww.setframerate(24000)
                for i, c in enumerate(chunks):
                    # VC chunk
                    tmp_vc = tempfile.NamedTemporaryFile(prefix=f'vc_chunk_{i:04d}_', suffix='.wav', delete=False)
                    c_vc = Path(tmp_vc.name); tmp_vc.close()
                    try:
                        get_vc_model().voice_conversion_to_file(source_wav=str(c), target_wav=str(tgt_wav), file_path=str(c_vc))  # type: ignore
                    except Exception as e:
                        _LOGGER.warning('vc chunk %d failed: %s; passing through', i, e)
                        c_vc = c
                    # Ensure PCM16 24k for pitch/write
                    tmp_pcm = tempfile.NamedTemporaryFile(prefix=f'vc_chunk_pcm_{i:04d}_', suffix='.wav', delete=False)
                    c_pcm = Path(tmp_pcm.name); tmp_pcm.close()
                    if not _ffmpeg_to_pcm16(c_vc, c_pcm, sample_rate=24000):
                        c_pcm = c_vc
                    # Decide pitch for this chunk
                    applied_st: Optional[float] = None
                    if semitones_override is not None and abs(semitones_override) > 0.05:
                        applied_st = float(semitones_override) * float(PITCH_CORRECTION)
                    elif f0_t:
                        try:
                            sr_v, x_v = _read_wav_head_samples(c_pcm, seconds=float(chunk_seconds))
                            f0_v = _estimate_f0_avg(sr_v, x_v)
                            if f0_v and f0_v > 0.0:
                                st_raw = 12.0 * math.log2(float(f0_t) / float(f0_v))
                                applied_st = float(st_raw) * float(PITCH_CORRECTION)
                        except Exception:
                            applied_st = None
                    # Apply pitch if needed
                    c_out = c_pcm
                    if applied_st is not None and abs(applied_st) > 0.1:
                        tmp_ps = tempfile.NamedTemporaryFile(prefix=f'vc_chunk_ps_{i:04d}_', suffix='.wav', delete=False)
                        c_ps = Path(tmp_ps.name); tmp_ps.close()
                        if _ffmpeg_pitch_shift(c_pcm, c_ps, applied_st):
                            c_out = c_ps
                    # Append frames
                    try:
                        with _wave.open(str(c_out), 'rb') as wf:
                            ww.writeframes(wf.readframes(wf.getnframes()))
                    except Exception as e:
                        _LOGGER.warning('write chunk %d failed: %s (skipping)', i, e)
                    # cleanup temps (best-effort)
                    for p in (c_vc, c_pcm, c_out):
                        try:
                            if p not in (c,) and p.exists():
                                os.unlink(p)
                        except Exception:
                            pass
            return True
        finally:
            try:
                if src_pcm_path != src_wav and src_pcm_path.exists():
                    os.unlink(src_pcm_path)
            except Exception:
                pass


    def _apply_pitch_chunkwise(in_path: Path, out_path: Path, semitones: float, chunk_seconds: float = CHUNKSIZE_SECONDS) -> bool:
        """Split PCM16 WAV, pitch each chunk, and concatenate."""
        import tempfile, os
        # Ensure PCM16 for splitting
        in_pcm = tempfile.NamedTemporaryFile(prefix='pitch_in_pcm_', suffix='.wav', delete=False); in_pcm_path = Path(in_pcm.name); in_pcm.close()
        if not _ffmpeg_to_pcm16(in_path, in_pcm_path, None):
            in_pcm_path = in_path
        try:
            chunks = _split_wav_pcm16(in_pcm_path, chunk_seconds)
            out_chunks: List[Path] = []
            for i, c in enumerate(chunks):
                tmp = tempfile.NamedTemporaryFile(prefix=f'pitch_chunk_{i:04d}_', suffix='.wav', delete=False)
                c_out = Path(tmp.name); tmp.close()
                if not _ffmpeg_pitch_shift(c, c_out, semitones):
                    c_out = c
                out_chunks.append(c_out)
            ok = _concat_wavs_pcm16(out_chunks, out_path)
            return ok
        finally:
            try:
                if in_pcm_path != in_path and in_pcm_path.exists():
                    os.unlink(in_pcm_path)
            except Exception:
                pass

    def _python_resample_mono_pad(in_path: Path, out_path: Path, sample_rate: int = 24000, pad_seconds: float = 0.5) -> bool:
        """Pure-Python fallback: convert to mono, linear resample to sample_rate, pad trailing silence, write PCM16 WAV."""
        try:
            import wave as _w
            import numpy as _np
            with _w.open(str(in_path), 'rb') as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                n = wf.getnframes()
                raw = wf.readframes(n)
            if n == 0:
                # Create small silence
                sr = sample_rate
                x = _np.zeros(int(sr * max(0.5, pad_seconds)), dtype=_np.float32)
            else:
                if sw == 2:
                    x = _np.frombuffer(raw, dtype=_np.int16).astype(_np.float32) / 32768.0
                elif sw == 1:
                    x = (_np.frombuffer(raw, dtype=_np.uint8).astype(_np.float32) - 128.0) / 128.0
                else:
                    x = _np.frombuffer(raw, dtype=_np.int16).astype(_np.float32) / 32768.0
                if ch > 1:
                    x = x.reshape(-1, ch).mean(axis=1)
                # Resample if needed (linear)
                if sr != sample_rate and x.size > 1:
                    ratio = float(sample_rate) / float(sr)
                    out_len = max(1, int(round(x.size * ratio)))
                    t = _np.linspace(0.0, x.size - 1, num=out_len, dtype=_np.float32)
                    i0 = _np.floor(t).astype(_np.int32)
                    i1 = _np.minimum(i0 + 1, x.size - 1)
                    frac = t - i0
                    x = (x[i0] * (1.0 - frac)) + (x[i1] * frac)
                sr = sample_rate
                # Pad trailing silence
                pad = _np.zeros(int(max(0.0, pad_seconds) * sr), dtype=_np.float32)
                x = _np.concatenate([x, pad]) if pad.size > 0 else x
            # Write PCM16
            x16 = _np.clip(_np.round(x * 32767.0), -32768, 32767).astype(_np.int16)
            with _w.open(str(out_path), 'wb') as ww:
                ww.setnchannels(1)
                ww.setsampwidth(2)
                ww.setframerate(sr)
                ww.writeframes(x16.tobytes())
            _LOGGER.info("Python resample/mono/pad wrote: %s @ %d Hz (%d frames)", out_path, sr, x16.size)
            return True
        except Exception as e:
            _LOGGER.warning("python resample/mono failed: %s", e)
            return False

    # Basic CORS for all responses
    @app.after_request
    def add_cors_headers(resp):  # type: ignore
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        # Allow Authorization so server-side bearer fetches are configurable if ever proxied
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept, Authorization"
        # Media streaming hint for proxies
        resp.headers["X-Accel-Buffering"] = "no"
        # Be explicit about referrer policy if the browser surfaces strict-origin-when-cross-origin
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Note: Content headers for audio are applied by ResponseWriter
        return resp

    @app.route("/healthz", methods=["GET"])  # liveness
    def healthz() -> Tuple[str, int, Dict[str, str]]:
        return ("ok", 200, {"Content-Type": "text/plain"})

    @app.route("/voices", methods=["GET"])  # list models
    def voices() -> Any:
        # Ensure index is current
        registry.refresh_index()
        result: Dict[str, Any] = {}
        for mid, path in registry.index.items():
            info = registry.infos.get(mid)
            if not info:
                try:
                    info = load_voice_info(path)
                    registry.infos[mid] = info
                except Exception:
                    info = None
            if info:
                result[mid] = {
                    "path": str(path),
                    "espeak_voice": info.espeak_voice,
                    "sample_rate": info.sample_rate,
                    "num_speakers": info.num_speakers,
                    "speaker_id_map": info.speaker_id_map,
                }
            else:
                result[mid] = {"path": str(path)}
        return jsonify(result)

    @app.route("/", methods=["OPTIONS"])  # CORS preflight
    def options_root() -> Tuple[str, int, Dict[str, str]]:
        return ("", 204, {})

    @app.route("/", methods=["GET", "POST"])  # synthesize
    def synthesize() -> Any:
        # Accept JSON or form/query parameters for flexibility
        payload: Dict[str, Any] = {}
        if request.is_json:
            try:
                payload = request.get_json(force=True, silent=True) or {}
            except Exception:
                payload = {}
        # Merge form/query onto payload without overwriting explicit JSON values
        for k in ("text", "voice", "lang", "speaker", "speaker_id", "length_scale", "noise_scale", "noise_w_scale", "sentence_silence", "voice2", "sound", "pitch_st", "pitch_factor", "pitch_disable", "disable_pitch", "nopitch"):
            if k not in payload or payload.get(k) in (None, ""):
                v = request.form.get(k, request.args.get(k))
                if v is not None:
                    payload[k] = v
        text = (str(payload.get("text") or "")).strip()
        voice2 = (str(payload.get("voice2") or "")).strip()  # target timbre id for VC
        sound = (str(payload.get("sound") or "")).strip()    # source audio id from voices folder
        # Require either text or sound; otherwise return generic 400 without hints
        if (not text) and (not sound):
            return ("bad request", 400, {"Content-Type": "text/plain"})
        # Optional pitch override controls (applied pre-VC)
        pitch_st_raw = (str(payload.get("pitch_st") or "").strip())
        pitch_factor_raw = (str(payload.get("pitch_factor") or "").strip())
        pitch_override_semitones: Optional[float] = None
        try:
            if pitch_st_raw != "":
                pitch_override_semitones = float(pitch_st_raw)
            elif pitch_factor_raw != "":
                pf = float(pitch_factor_raw)
                if pf > 0:
                    import math as _math
                    pitch_override_semitones = 12.0 * _math.log2(pf)
        except Exception:
            pitch_override_semitones = None
        # Quick switch to disable any pitch processing
        pitch_disable = bool(PITCH_DISABLE_DEFAULT)
        _pd = payload.get("pitch_disable", payload.get("disable_pitch", payload.get("nopitch")))
        try:
            if isinstance(_pd, bool):
                pitch_disable = _pd
            elif isinstance(_pd, (int, float)):
                pitch_disable = (float(_pd) != 0.0)
            elif isinstance(_pd, str):
                pitch_disable = _pd.strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            pitch_disable = bool(PITCH_DISABLE_DEFAULT)

        model_id = payload.get("voice") or default_model_id
        lang = payload.get("lang")
        # Resolve model by language if needed
        if not model_id and lang:
            model_id = registry.best_for_lang(lang)
        if not model_id and default_model_id:
            model_id = default_model_id
        if not model_id:
            return ("No voice available", 404, {"Content-Type": "text/plain"})

        # Validate model id by loading when needed through the factory
        try:
            _ = registry.ensure_loaded(model_id)
        except KeyError:
            return (f"Voice not found: {model_id}", 404, {"Content-Type": "text/plain"})

        try:
            sentence_silence = float(payload.get("sentence_silence", args.sentence_silence))
        except Exception:
            sentence_silence = args.sentence_silence
        _LOGGER.info("request: len(text)=%d model=%s voice2=%s sound=%s pitch_st=%s pitch_factor=%s pitch_disable=%s",
                     len(text), model_id, (voice2 or '-'), (sound or '-'),
                     payload.get('pitch_st'), payload.get('pitch_factor'), pitch_disable)

        # ID validator to prevent path/URL hijacking
        def _valid_id(s: str) -> bool:
            if not s:
                return False
            if any(ch in s for ch in ('/', '&')):
                return False
            # Strict allowlist: alnum, underscore, dash, dot
            import re as _re
            return bool(_re.fullmatch(r'[A-Za-z0-9_.\-]{1,128}', s))

        # no resolver here: validate IDs; build absolute/URL refs via FileFetcher.build_ref

        # Download helper is now provided by stages.FileFetcher.fetch_to_temp

        # If 'sound' is provided: stream a WAV from voices/ optionally through VC to target 'voice2'
        if sound:
            if not _valid_id(sound):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            # Validate and resolve to ref
            here = Path(__file__).resolve().parent
            if not _valid_id(sound):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            tmpl = args.soundpath if hasattr(args, 'soundpath') else "../voices/%s.wav"
            value_s = FileFetcher.build_ref(sound, tmpl, here)
            # If no voice2, shortcut via FileFetcher -> RawResponseWriter (no resample, raw passthrough)
            if not voice2:
                src_ref = value_s
                _LOGGER.info("Streaming sound (no VC) via FileFetcher: source=%s", src_ref)
                fetcher = FileFetcher(src_ref, bearer=getattr(args, 'bearer', ''))
                writer = RawResponseWriter(fetcher)
                # Guess a suitable mimetype from file extension
                guessed, _ = mimetypes.guess_type(src_ref)
                if guessed == 'audio/x-wav':
                    guessed = 'audio/wav'
                mtype = guessed or 'application/octet-stream'
                def gen_sound_only_raw():
                    for b in writer.stream():
                        yield b
                resp = Response(stream_with_context(gen_sound_only_raw()), mimetype=mtype)
                # Best-effort: set Content-Length if known to improve playback stability
                try:
                    if src_ref.startswith('http://') or src_ref.startswith('https://'):
                        try:
                            h = fetcher._open()  # type: ignore[attr-defined]
                            clen = getattr(h, 'getheader', None)
                            if callable(clen):
                                v = clen('Content-Length')
                                if v:
                                    resp.headers['Content-Length'] = str(v)
                            else:
                                v2 = getattr(getattr(h, 'headers', None), 'get', None)
                                if callable(v2):
                                    vv = v2('Content-Length')
                                    if vv:
                                        resp.headers['Content-Length'] = str(vv)
                        except Exception:
                            pass
                    else:
                        try:
                            resp.headers['Content-Length'] = str(os.path.getsize(src_ref))
                        except Exception:
                            pass
                except Exception:
                    pass
                # Ensure inline disposition for browser playback
                try:
                    resp.headers.setdefault('Content-Disposition', 'inline')
                except Exception:
                    pass
                def _cleanup():
                    try:
                        writer.cancel()
                    except Exception:
                        pass
                    try:
                        fetcher.close()
                    except Exception:
                        pass
                resp.call_on_close(_cleanup)
                return resp

            # With voice2: convert whole file using VC stage (passes through if unavailable)
            if not _valid_id(voice2):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            # Resolve target reference (URL or file path string)
            here = Path(__file__).resolve().parent
            if not _valid_id(voice2):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            tmpl = args.soundpath if hasattr(args, 'soundpath') else "../voices/%s.wav"
            value_t = FileFetcher.build_ref(voice2, tmpl, here)
            # Resolve source based on soundpath
            if not _valid_id(sound):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            here = Path(__file__).resolve().parent
            if not _valid_id(sound):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            tmpl = args.soundpath if hasattr(args, 'soundpath') else "../voices/%s.wav"
            value_s = FileFetcher.build_ref(sound, tmpl, here)
            src_ref = value_s
            _LOGGER.info("SOUND+VC: source ref=%s target ref=%s (downloading if http)", src_ref, value_t)
            source = AudioReader(src_ref, bearer=getattr(args, 'bearer', ''))
            # Let stages resolve and fetch target as needed (with bearer), avoiding temp logic here
            pipeline = source.pipe(VCConverter(value_t, bearer=getattr(args, 'bearer', ''))).pipe(PitchAdjuster(value_t, pitch_disable=False, pitch_override_st=None, correction=PITCH_CORRECTION, bearer=getattr(args, 'bearer', '')))
            writer = ResponseWriter(pipeline, est_frames_24k=source.estimate_frames_24k())
            def gen_stream_sound():
                for b in writer.stream():
                    yield b
            resp = Response(stream_with_context(gen_stream_sound()), mimetype="audio/wav")
            try:
                writer.apply_headers(resp)
            except Exception:
                pass
            def _cleanup2():
                try:
                    writer.cancel()
                except Exception:
                    pass
            resp.call_on_close(_cleanup2)
            return resp

        # TTS path: always stream via pipeline (realtime)
        # If voice2 is provided, run VC + pitch; else just TTS

        # TTS+VC pipeline (still realtime streaming)
        _LOGGER.info("path: TTS pipeline (VC=%s)", bool(voice2))

        # 2) If voice2 requested, run VC; stage handles passthrough if VC unavailable
        if voice2:
            if not _valid_id(voice2):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            here = Path(__file__).resolve().parent
            if not _valid_id(voice2):
                return ("bad request", 400, {"Content-Type": "text/plain"})
            tmpl = args.soundpath if hasattr(args, 'soundpath') else "../voices/%s.wav"
            value_t = FileFetcher.build_ref(voice2, tmpl, here)
            # Build pipeline: TTSProducer -> VC -> Pitch -> Writer
            _LOGGER.info("TTS+VC: target ref=%s (downloading if http)", value_t)
            source = registry.create_tts_stream(model_id, text, {"sentence_silence": sentence_silence, "chunk_seconds": CHUNKSIZE_SECONDS, "speaker": payload.get("speaker"), "speaker_id": payload.get("speaker_id"), "length_scale": payload.get("length_scale"), "noise_scale": payload.get("noise_scale"), "noise_w_scale": payload.get("noise_w_scale")} )
            # Let stages resolve and fetch target as needed (with bearer)
            pipeline = source.pipe(VCConverter(value_t, bearer=getattr(args, 'bearer', ''))).pipe(PitchAdjuster(value_t, pitch_disable, pitch_override_semitones, correction=PITCH_CORRECTION, bearer=getattr(args, 'bearer', '')))
            writer = ResponseWriter(pipeline, est_frames_24k=source.estimate_frames_24k())
            def gen_stream():
                for b in writer.stream():
                    yield b
            resp = Response(stream_with_context(gen_stream()), mimetype="audio/wav")
            try:
                writer.apply_headers(resp)
            except Exception:
                pass
            def _cleanup3():
                try:
                    writer.cancel()
                except Exception:
                    pass
            resp.call_on_close(_cleanup3)
            return resp

        # 3) No VC: stream TTS via pipeline (single header + PCM chunks)
        _LOGGER.info("path: buffered TTS (no VC) via pipeline")
        source = registry.create_tts_stream(model_id, text, {"sentence_silence": sentence_silence, "chunk_seconds": CHUNKSIZE_SECONDS, "speaker": payload.get("speaker"), "speaker_id": payload.get("speaker_id"), "length_scale": payload.get("length_scale"), "noise_scale": payload.get("noise_scale"), "noise_w_scale": payload.get("noise_w_scale")} )
        writer = ResponseWriter(source, est_frames_24k=source.estimate_frames_24k())
        def gen_tts_only():
            for b in writer.stream():
                yield b
        resp = Response(stream_with_context(gen_tts_only()), mimetype="audio/wav")
        try:
            writer.apply_headers(resp)
        except Exception:
            pass
        resp.call_on_close(lambda: writer.cancel())
        return resp

    # ---- Streaming TTS: text streams in, audio streams out on one connection ----
    @app.route("/tts/stream", methods=["POST", "OPTIONS"])
    def tts_stream_endpoint():
        if request.method == "OPTIONS":
            return ("", 204)
        model_id = default_model_id
        if not model_id:
            return ("No voice available", 404, {"Content-Type": "text/plain"})
        voice = registry.ensure_loaded(model_id)
        syn = registry.create_synthesis_config(voice, {})
        sr = voice.config.sample_rate
        input_stream = request.stream

        def gen():
            # WAV header with max size (streaming, unknown total length)
            data_size = 0x7FFFFFFF
            riff_size = 36 + data_size
            yield (b"RIFF" + riff_size.to_bytes(4, "little") + b"WAVEfmt "
                   + (16).to_bytes(4, "little") + (1).to_bytes(2, "little")
                   + (1).to_bytes(2, "little") + sr.to_bytes(4, "little")
                   + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little")
                   + (16).to_bytes(2, "little") + b"data"
                   + data_size.to_bytes(4, "little"))
            buf = b''
            while True:
                chunk = input_stream.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    text = line.decode('utf-8', errors='replace').strip()
                    if text:
                        _LOGGER.info("tts/stream: synthesizing %d chars", len(text))
                        for audio_chunk in voice.synthesize(text, syn):
                            yield audio_chunk.audio_int16_bytes
            # Flush remaining text
            text = buf.decode('utf-8', errors='replace').strip()
            if text:
                _LOGGER.info("tts/stream: flushing %d chars", len(text))
                for audio_chunk in voice.synthesize(text, syn):
                    yield audio_chunk.audio_int16_bytes

        resp = Response(stream_with_context(gen()), mimetype="audio/wav")
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Content-Disposition"] = "inline"
        return resp

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--model", help="Optional: preload this voice (.onnx path)")
    parser.add_argument("--voices-path", default="voices-piper", help="Directory that contains *.onnx voices (default: voices-piper)")
    parser.add_argument("--scan-dir", help="(legacy) Single directory to scan for *.onnx voices; same as --voices-path")
    parser.add_argument("--cuda", action="store_true", help="Use GPU")
    parser.add_argument("--sentence-silence", type=float, default=0.0, help="Seconds of silence between sentences")
    parser.add_argument("--soundpath", default="../voices/%s.wav", help="Template for sound/voice2 source. Use %s placeholder for id. Supports file paths or http(s) URLs.")
    parser.add_argument("--bearer", default="", help="Bearer token for authorizing remote (http/https) downloads/streams")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    app = create_app(args)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
