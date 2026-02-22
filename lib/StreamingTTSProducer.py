from __future__ import annotations

import tempfile as _tempfile
import wave as _wave
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .base import Stage
from .util import ffmpeg_to_pcm16


class StreamingTTSProducer(Stage):
    """Source stage: reads text lines from an iterable, synthesizes each via Piper.

    Unlike TTSProducer (which takes a fixed text string), this stage
    accepts an iterable of text lines (e.g. from request.stream) and
    synthesizes each line as it arrives — ideal for streaming TTS.
    """

    def __init__(
        self,
        text_iter: Iterable[str],
        voice,
        syn_config,
        sentence_silence: float = 0.0,
    ) -> None:
        super().__init__()
        self.text_iter = text_iter
        self.voice = voice
        self.syn = syn_config
        self.sentence_silence = float(sentence_silence)

    def stream_pcm24k(self) -> Iterator[bytes]:
        native_sr = self.voice.config.sample_rate
        silence_bytes = int(native_sr * self.sentence_silence * 2) if self.sentence_silence > 0 else 0
        first = True
        for text in self.text_iter:
            if self.cancelled:
                break
            text = text.strip()
            if not text:
                continue
            if not first and silence_bytes > 0:
                yield bytes(silence_bytes)
            for chunk in self.voice.synthesize(text, self.syn):
                if self.cancelled:
                    break
                pcm = chunk.audio_int16_bytes
                if native_sr != 24000:
                    pcm = self._resample(pcm, native_sr)
                yield pcm
            first = False

    @staticmethod
    def _resample(pcm: bytes, src_rate: int) -> bytes:
        """Resample PCM s16le mono from src_rate to 24000 via ffmpeg."""
        tmp_in = _tempfile.NamedTemporaryFile(prefix="stts_in_", suffix=".wav", delete=False)
        p_in = Path(tmp_in.name)
        tmp_in.close()
        with _wave.open(str(p_in), "wb") as ww:
            ww.setnchannels(1)
            ww.setsampwidth(2)
            ww.setframerate(src_rate)
            ww.writeframes(pcm)
        tmp_out = _tempfile.NamedTemporaryFile(prefix="stts_out_", suffix=".wav", delete=False)
        p_out = Path(tmp_out.name)
        tmp_out.close()
        if not ffmpeg_to_pcm16(p_in, p_out, sample_rate=24000):
            p_out = p_in
        try:
            with _wave.open(str(p_out), "rb") as wf:
                return wf.readframes(wf.getnframes())
        finally:
            try:
                p_in.unlink()
            except Exception:
                pass
            try:
                p_out.unlink()
            except Exception:
                pass
