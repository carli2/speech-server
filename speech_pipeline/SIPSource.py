from __future__ import annotations

import logging
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("sip-source")


class SIPSource(Stage):
    """Source stage: reads audio from a pyVoIP SIP call.

    Output: unsigned 8-bit PCM (u8) mono @ 8000 Hz.
    This is pyVoIP's native decoded audio format.
    Converters are auto-inserted by pipe() if the downstream stage
    needs a different format (e.g. s16le @ 16kHz for WhisperSTT).
    """

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.output_format = AudioFormat(8000, "u8")

    def stream_pcm24k(self) -> Iterator[bytes]:
        import time as _time

        if not self.session.connected.is_set():
            _LOGGER.info("SIPSource: waiting for call to connect...")
            self.session.connected.wait(timeout=30)

        if self.session.hungup.is_set():
            _LOGGER.warning("SIPSource: call already hung up")
            return

        _LOGGER.info("SIPSource: streaming audio from SIP call")
        call = self.session.call
        while not self.cancelled and not self.session.hungup.is_set():
            try:
                # Non-blocking read: returns whatever is in the RTP buffer.
                # We pace at real-time (20ms per 160-byte frame) to avoid
                # spinning when the buffer only has silence.
                frame = call.read_audio(length=160, blocking=False)
                if frame:
                    yield frame
                _time.sleep(0.02)  # 20ms = one frame period @ 8kHz
            except Exception as e:
                if not self.cancelled:
                    _LOGGER.warning("SIPSource read error: %s", e)
                break

        _LOGGER.info("SIPSource: stream ended")
