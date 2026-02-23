from __future__ import annotations

import audioop
import logging
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("sample-rate-converter")


class SampleRateConverter(Stage):
    """Resample upstream PCM (s16le mono) from src_rate to dst_rate via audioop."""

    def __init__(self, src_rate: int = 48000, dst_rate: int = 16000) -> None:
        super().__init__()
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self.input_format = AudioFormat(self.src_rate, "s16le")
        self.output_format = AudioFormat(self.dst_rate, "s16le")

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.upstream:
            return
        if self.src_rate == self.dst_rate:
            yield from self.upstream.stream_pcm24k()
            return

        _LOGGER.info("Resampling %d -> %d Hz", self.src_rate, self.dst_rate)
        state = None
        for chunk in self.upstream.stream_pcm24k():
            if self.cancelled:
                break
            resampled, state = audioop.ratecv(
                chunk, 2, 1, self.src_rate, self.dst_rate, state
            )
            if resampled:
                yield resampled
