from __future__ import annotations

import audioop
import logging
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("gain-stage")


class GainStage(Stage):
    """Processor: adjusts PCM volume with a runtime-mutable gain factor.

    Uses ``audioop.mul()`` to scale samples. The gain factor can be
    changed at any time via ``set_gain()`` — the new value takes effect
    on the next chunk (GIL-safe float assignment, no lock needed).

    gain=1.0 is unity (passthrough), 0.0 is silence, >1.0 amplifies.
    """

    def __init__(self, sample_rate: int, gain: float = 1.0, encoding: str = "s16le") -> None:
        super().__init__()
        self.sample_rate = sample_rate
        self._gain = gain
        fmt = AudioFormat(sample_rate, encoding)
        self.input_format = fmt
        self.output_format = fmt
        # sample width in bytes: s16le=2, u8=1
        self._sample_width = 2 if encoding == "s16le" else 1

    @property
    def gain(self) -> float:
        return self._gain

    def set_gain(self, gain: float) -> None:
        """Set the gain factor. Takes effect on the next chunk."""
        self._gain = gain

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.upstream:
            return

        for chunk in self.upstream.stream_pcm24k():
            if self.cancelled:
                break
            g = self._gain
            if g == 1.0:
                yield chunk
            elif g == 0.0:
                yield b"\x00" * len(chunk)
            else:
                yield audioop.mul(chunk, self._sample_width, g)
