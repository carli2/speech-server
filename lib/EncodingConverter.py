from __future__ import annotations

import audioop
import logging
from typing import Callable, Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("encoding-converter")


class EncodingConverter(Stage):
    """Converts between audio encodings (u8 <-> s16le).

    u8:    unsigned 8-bit PCM (1 byte/sample, silence = 128)
    s16le: signed 16-bit little-endian PCM (2 bytes/sample, silence = 0)

    Sample rate and channel count are preserved — only the encoding changes.
    This stage is typically auto-inserted by Stage.pipe() when formats differ.
    """

    _CONVERTERS = {
        ("u8", "s16le"): "_u8_to_s16le",
        ("s16le", "u8"): "_s16le_to_u8",
    }

    def __init__(self, src_encoding: str, dst_encoding: str) -> None:
        super().__init__()
        self.src_encoding = src_encoding
        self.dst_encoding = dst_encoding
        key = (src_encoding, dst_encoding)
        if key not in self._CONVERTERS:
            raise ValueError(f"No converter for {src_encoding} -> {dst_encoding}")
        self._convert: Callable[[bytes], bytes] = getattr(self, self._CONVERTERS[key])

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.upstream:
            return
        _LOGGER.info("EncodingConverter: %s -> %s", self.src_encoding, self.dst_encoding)
        for chunk in self.upstream.stream_pcm24k():
            if self.cancelled:
                break
            yield self._convert(chunk)

    @staticmethod
    def _u8_to_s16le(data: bytes) -> bytes:
        signed = audioop.bias(data, 1, -128)
        return audioop.lin2lin(signed, 1, 2)

    @staticmethod
    def _s16le_to_u8(data: bytes) -> bytes:
        signed = audioop.lin2lin(data, 2, 1)
        return audioop.bias(signed, 1, 128)
