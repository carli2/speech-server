from __future__ import annotations

import logging
from queue import Empty
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("audiosocket-source")


class AudioSocketSource(Stage):
    """Source stage: reads PCM s16le mono from an AudioSocket connection.

    Output is PCM at the session's sample rate (typically 8kHz from Asterisk).
    Converters are auto-inserted by pipe() if the downstream stage needs
    a different format.
    """

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.output_format = AudioFormat(session.sample_rate, "s16le")

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.session.connected.is_set():
            _LOGGER.info("AudioSocketSource: waiting for connection...")
            self.session.connected.wait(timeout=60)

        if self.session.hungup.is_set():
            _LOGGER.warning("AudioSocketSource: already hung up")
            return

        _LOGGER.info("AudioSocketSource: streaming audio")
        while not self.cancelled and not self.session.hungup.is_set():
            try:
                frame = self.session.rx_queue.get(timeout=0.5)
            except Empty:
                continue
            if frame:
                yield frame

        _LOGGER.info("AudioSocketSource: stream ended")
