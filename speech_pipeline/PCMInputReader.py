from __future__ import annotations

import logging
from typing import Iterator, Optional

from .base import Stage

_LOGGER = logging.getLogger("pcm-input-reader")


class PCMInputReader(Stage):
    """Source stage that reads raw PCM s16le mono from a file-like stream."""

    def __init__(self, stream, read_size: int = 4096) -> None:
        super().__init__()
        self.stream = stream
        self.read_size = read_size

    def stream_pcm24k(self) -> Iterator[bytes]:
        _LOGGER.info("starting read loop, stream type=%s", type(self.stream).__name__)
        remainder = b""
        total = 0
        reads = 0
        while not self.cancelled:
            chunk = self.stream.read(self.read_size)
            if not chunk:
                _LOGGER.info("stream EOF after %d reads, %d bytes total", reads, total)
                break
            reads += 1
            chunk = remainder + chunk
            # s16le = 2 bytes per sample; never yield an odd-byte chunk
            if len(chunk) % 2 == 1:
                remainder = chunk[-1:]
                chunk = chunk[:-1]
            else:
                remainder = b""
            if chunk:
                total += len(chunk)
                if reads <= 3 or reads % 100 == 0:
                    _LOGGER.info("read #%d: %d bytes (total %d)", reads, len(chunk), total)
                yield chunk
