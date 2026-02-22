from __future__ import annotations

from typing import Iterator, Optional

from .base import Stage


class PCMInputReader(Stage):
    """Source stage that reads raw PCM s16le mono from a file-like stream."""

    def __init__(self, stream, read_size: int = 4096) -> None:
        super().__init__()
        self.stream = stream
        self.read_size = read_size

    def stream_pcm24k(self) -> Iterator[bytes]:
        remainder = b""
        while not self.cancelled:
            chunk = self.stream.read(self.read_size)
            if not chunk:
                break
            chunk = remainder + chunk
            # s16le = 2 bytes per sample; never yield an odd-byte chunk
            if len(chunk) % 2 == 1:
                remainder = chunk[-1:]
                chunk = chunk[:-1]
            else:
                remainder = b""
            if chunk:
                yield chunk
