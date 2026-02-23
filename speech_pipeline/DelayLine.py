from __future__ import annotations

import logging
from collections import deque
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("delay-line")


class DelayLine(Stage):
    """Processor: adds a variable delay to the audio stream.

    Internally uses a ring buffer of PCM chunks. The delay can be
    changed at runtime via ``set_delay_ms()`` — the buffer grows or
    shrinks to match. GIL-safe assignment, no lock needed.

    At delay_ms=0 this is a passthrough (no buffering overhead).
    """

    def __init__(self, sample_rate: int, delay_ms: float = 0.0, encoding: str = "s16le") -> None:
        super().__init__()
        self.sample_rate = sample_rate
        self._delay_ms = delay_ms
        fmt = AudioFormat(sample_rate, encoding)
        self.input_format = fmt
        self.output_format = fmt
        # bytes per ms: sample_rate/1000 * bytes_per_sample
        bps = 2 if encoding == "s16le" else 1
        self._bytes_per_ms = sample_rate / 1000.0 * bps

    @property
    def delay_ms(self) -> float:
        return self._delay_ms

    def set_delay_ms(self, delay_ms: float) -> None:
        """Set the delay in milliseconds. Takes effect gradually."""
        self._delay_ms = max(0.0, delay_ms)

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.upstream:
            return

        # Ring buffer of PCM chunks
        buf = deque()
        buf_bytes = 0

        for chunk in self.upstream.stream_pcm24k():
            if self.cancelled:
                break

            target_bytes = int(self._delay_ms * self._bytes_per_ms)
            # Ensure even byte count for s16le alignment
            target_bytes &= ~1

            if target_bytes == 0:
                # Zero delay: flush buffer then passthrough
                while buf:
                    yield buf.popleft()
                buf_bytes = 0
                yield chunk
                continue

            # Add incoming chunk to buffer
            buf.append(chunk)
            buf_bytes += len(chunk)

            # Emit chunks when buffer exceeds target delay
            while buf_bytes > target_bytes and buf:
                out = buf.popleft()
                buf_bytes -= len(out)
                yield out

        # Flush remaining buffer on stream end
        while buf:
            yield buf.popleft()
