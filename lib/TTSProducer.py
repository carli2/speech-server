from __future__ import annotations

import tempfile as _tempfile
import wave as _wave
from pathlib import Path
from typing import Iterator, Optional

from .base import Stage
from .util import ffmpeg_to_pcm16


class TTSProducer(Stage):
    def __init__(
        self,
        voice,
        syn_config,
        text: str,
        sentence_silence: float,
        chunk_seconds: float = 10.0,
    ) -> None:
        super().__init__()
        self.voice = voice
        self.syn = syn_config
        self.text = text
        self.sentence_silence = float(sentence_silence)
        self.chunk_seconds = float(chunk_seconds)

    def estimate_frames_24k(self) -> Optional[int]:
        char_rate = 15.0
        try:
            sentence_count = len(self.voice.phonemize(self.text)) or 1
        except Exception:
            sentence_count = 1
        base_seconds = max(1.0, len(self.text) / char_rate)
        silence_seconds = max(0.0, (sentence_count - 1) * self.sentence_silence)
        safety_factor = 1.5
        margin_seconds = 0.5
        est_seconds = (base_seconds + silence_seconds + margin_seconds) * safety_factor
        return int(est_seconds * 24000)

    def stream_pcm24k(self) -> Iterator[bytes]:
        native_sr = self.voice.config.sample_rate
        chunk_bytes_native = int(native_sr * 2 * max(0.1, self.chunk_seconds))
        buf = bytearray()
        for i, chunk in enumerate(self.voice.synthesize(self.text, self.syn)):
            if self.cancelled:
                break
            if i > 0 and self.sentence_silence > 0.0:
                buf.extend(bytes(int(native_sr * self.sentence_silence * 2)))
            buf.extend(chunk.audio_int16_bytes)
            while len(buf) >= chunk_bytes_native and not self.cancelled:
                piece = bytes(buf[:chunk_bytes_native])
                del buf[:chunk_bytes_native]
                # Resample piece to 24k PCM
                tmp_in = _tempfile.NamedTemporaryFile(prefix="tts_in_", suffix=".wav", delete=False)
                p_in = Path(tmp_in.name)
                tmp_in.close()
                with _wave.open(str(p_in), "wb") as ww:
                    ww.setnchannels(1)
                    ww.setsampwidth(2)
                    ww.setframerate(native_sr)
                    ww.writeframes(piece)
                tmp_out = _tempfile.NamedTemporaryFile(prefix="tts_out_", suffix=".wav", delete=False)
                p_out = Path(tmp_out.name)
                tmp_out.close()
                if not ffmpeg_to_pcm16(p_in, p_out, sample_rate=24000):
                    p_out = p_in
                try:
                    with _wave.open(str(p_out), "rb") as wf:
                        yield wf.readframes(wf.getnframes())
                finally:
                    try:
                        p_in.unlink()
                    except Exception:
                        pass
                    try:
                        p_out.unlink()
                    except Exception:
                        pass
        if buf and not self.cancelled:
            # Flush tail
            tmp_in = _tempfile.NamedTemporaryFile(prefix="tts_in_", suffix=".wav", delete=False)
            p_in = Path(tmp_in.name)
            tmp_in.close()
            with _wave.open(str(p_in), "wb") as ww:
                ww.setnchannels(1)
                ww.setsampwidth(2)
                ww.setframerate(native_sr)
                ww.writeframes(bytes(buf))
            tmp_out = _tempfile.NamedTemporaryFile(prefix="tts_out_", suffix=".wav", delete=False)
            p_out = Path(tmp_out.name)
            tmp_out.close()
            if not ffmpeg_to_pcm16(p_in, p_out, sample_rate=24000):
                p_out = p_in
            try:
                with _wave.open(str(p_out), "rb") as wf:
                    yield wf.readframes(wf.getnframes())
            finally:
                try:
                    p_in.unlink()
                except Exception:
                    pass
                try:
                    p_out.unlink()
                except Exception:
                    pass

