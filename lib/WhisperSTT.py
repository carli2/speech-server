from __future__ import annotations

import json
import logging
import os
import threading
from typing import Iterator, List, Optional

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("whisper-stt")

try:
    from faster_whisper import WhisperModel as _WhisperModel  # type: ignore
except Exception:
    _WhisperModel = None  # type: ignore

_singleton_model = None
_singleton_lock = threading.Lock()
_model_init_lock = threading.Lock()


def _detect_device() -> str:
    device = os.environ.get("WHISPER_DEVICE", "").lower()
    if device in ("cuda", "cpu"):
        return device
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _device_candidates(preferred):
    if preferred == "cuda":
        yield "cuda", "float16"
        yield "cuda", "int8"
    yield "cpu", "int8"
    yield "cpu", "float32"


def _get_model(model_size: str = "base"):
    """Return a process-wide singleton WhisperModel, lazily loaded."""
    global _singleton_model
    if _singleton_model is not None:
        return _singleton_model
    with _model_init_lock:
        if _singleton_model is not None:
            return _singleton_model
        if _WhisperModel is None:
            raise RuntimeError("faster-whisper is not installed")
        device = _detect_device()
        for dev, ct in _device_candidates(device):
            try:
                _LOGGER.info("Loading Whisper model %s on %s (compute_type=%s)",
                             model_size, dev, ct)
                _singleton_model = _WhisperModel(model_size, device=dev,
                                                  compute_type=ct)
                return _singleton_model
            except (ValueError, RuntimeError) as e:
                _LOGGER.warning("Whisper init failed (%s/%s): %s", dev, ct, e)
        raise RuntimeError("Could not load Whisper model on any device")


class WhisperTranscriber(Stage):
    """Sink stage: consumes PCM s16le from upstream, yields NDJSON lines.

    Buffers ~chunk_seconds of audio, transcribes via faster-whisper,
    and yields one JSON line per recognized segment.
    """

    def __init__(self, model_size: str = "base", chunk_seconds: float = 3.0,
                 sample_rate: int = 16000, language: Optional[str] = None) -> None:
        super().__init__()
        self.model_size = model_size
        self.chunk_seconds = chunk_seconds
        self.sample_rate = sample_rate
        self.language = language
        self.input_format = AudioFormat(sample_rate, "s16le")
        self.output_format = AudioFormat(0, "ndjson")

    def stream_pcm24k(self) -> Iterator[bytes]:
        """Yields NDJSON lines (as bytes) instead of PCM.

        Each line is a UTF-8 encoded JSON object: {"text": ..., "start": ..., "end": ...}
        """
        import numpy as np
        model = _get_model(self.model_size)
        chunk_bytes = int(self.sample_rate * 2 * self.chunk_seconds)  # s16le = 2 bytes/sample

        if not self.upstream:
            return

        buf = b""
        time_offset = 0.0

        _LOGGER.info("WhisperTranscriber: waiting for upstream data (chunk_bytes=%d)", chunk_bytes)
        for pcm in self.upstream.stream_pcm24k():
            if self.cancelled:
                break
            buf += pcm
            while len(buf) >= chunk_bytes:
                segment_audio = buf[:chunk_bytes]
                buf = buf[chunk_bytes:]
                _LOGGER.debug("transcribing chunk at offset=%.1fs (%d bytes)", time_offset, len(segment_audio))
                for line in self._transcribe_chunk(model, segment_audio, time_offset):
                    _LOGGER.info("result: %s", line.decode().strip())
                    yield line
                time_offset += self.chunk_seconds

        # Flush remaining
        if buf and not self.cancelled:
            _LOGGER.info("flushing remaining %d bytes at offset=%.1fs", len(buf), time_offset)
            for line in self._transcribe_chunk(model, buf, time_offset):
                _LOGGER.info("result: %s", line.decode().strip())
                yield line

    def _transcribe_chunk(self, model, pcm_bytes: bytes, time_offset: float) -> Iterator[bytes]:
        import numpy as np
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(samples, language=self.language, beam_size=5,
                                       vad_filter=True)
        for seg in segments:
            text = seg.text.strip()
            if text:
                obj = {"text": text,
                       "start": round(seg.start + time_offset, 3),
                       "end": round(seg.end + time_offset, 3)}
                yield (json.dumps(obj, ensure_ascii=False) + "\n").encode()


