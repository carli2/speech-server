from __future__ import annotations

from typing import Iterator, Optional

from .base import AudioFormat, Stage


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
        self.output_format = AudioFormat(voice.config.sample_rate, "s16le")

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
        return int(est_seconds * self.voice.config.sample_rate)

    def stream_pcm24k(self) -> Iterator[bytes]:
        native_sr = self.voice.config.sample_rate
        chunk_bytes = int(native_sr * 2 * max(0.1, self.chunk_seconds))
        buf = bytearray()
        for i, chunk in enumerate(self.voice.synthesize(self.text, self.syn)):
            if self.cancelled:
                break
            if i > 0 and self.sentence_silence > 0.0:
                buf.extend(bytes(int(native_sr * self.sentence_silence * 2)))
            buf.extend(chunk.audio_int16_bytes)
            while len(buf) >= chunk_bytes and not self.cancelled:
                yield bytes(buf[:chunk_bytes])
                del buf[:chunk_bytes]
        if buf and not self.cancelled:
            yield bytes(buf)
