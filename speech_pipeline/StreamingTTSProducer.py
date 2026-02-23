from __future__ import annotations

from typing import Iterable, Iterator

from .base import AudioFormat, Stage


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
        self.input_format = AudioFormat(0, "text")
        self.output_format = AudioFormat(voice.config.sample_rate, "s16le")

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
                yield chunk.audio_int16_bytes
            first = False
