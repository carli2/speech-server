"""Source stage: reads decoded PCM s16le 48 kHz from a CodecSocketSession."""
from __future__ import annotations

import logging
from queue import Empty
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("codec-source")


class CodecSocketSource(Stage):
    """Yields PCM s16le mono 48 kHz chunks received (and decoded) by the
    session's RX loop.

    Converters are auto-inserted by ``pipe()`` if the downstream stage
    needs a different format.
    """

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.output_format = AudioFormat(48000, "s16le")

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.session.connected.is_set():
            _LOGGER.info("CodecSocketSource: waiting for connection...")
            self.session.connected.wait(timeout=60)

        if self.session.closed.is_set():
            _LOGGER.warning("CodecSocketSource: already closed")
            return

        _LOGGER.info("CodecSocketSource: streaming audio")
        while not self.cancelled and not self.session.closed.is_set():
            try:
                frame = self.session.rx_queue.get(timeout=0.5)
            except Empty:
                continue
            if frame:
                yield frame

        _LOGGER.info("CodecSocketSource: stream ended")
