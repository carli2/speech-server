from __future__ import annotations

import logging
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("audiosocket-sink")


class AudioSocketSink(Stage):
    """Sink stage: writes PCM s16le mono into an AudioSocket connection.

    Expects upstream to deliver PCM at the session's sample rate (typically 8kHz).
    Converters are auto-inserted by pipe() if the upstream stage outputs
    a different format.

    Terminal sink — drives the pipeline like WebSocketWriter.run().
    """

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.input_format = AudioFormat(session.sample_rate, "s16le")

    def run(self) -> None:
        """Drive the pipeline and write all PCM to AudioSocket."""
        if not self.session.connected.is_set():
            _LOGGER.info("AudioSocketSink: waiting for connection...")
            self.session.connected.wait(timeout=60)

        if self.session.hungup.is_set():
            _LOGGER.warning("AudioSocketSink: already hung up")
            return

        _LOGGER.info("AudioSocketSink: streaming to AudioSocket")
        try:
            for pcm in self.upstream.stream_pcm24k():
                if self.cancelled or self.session.hungup.is_set():
                    break
                try:
                    self.session.tx_queue.put(pcm, timeout=1.0)
                except Exception:
                    break
        except Exception as e:
            _LOGGER.warning("AudioSocketSink error: %s", e)
        finally:
            _LOGGER.info("AudioSocketSink: stream ended")
            try:
                self.session.hangup()
            except Exception:
                pass
