from __future__ import annotations

import logging
import queue
import threading

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("sip-sink")

# 160 bytes u8 @ 8kHz = 20ms per frame
_FRAME_BYTES = 160


class _AudioQueue:
    """Drop-in replacement for pyVoIP's RTPPacketManager (pmout).

    Uses a thread-safe queue instead of a BytesIO buffer.
    pyVoIP's original buffer loses data when the trans() thread's read
    cursor outruns the write cursor (which happens whenever there's a
    gap between writes, e.g. between TTS sentences).

    This queue never loses data: trans() blocks briefly when empty and
    sends silence, writes are buffered until trans() is ready.
    """

    def __init__(self):
        self._q: queue.Queue[bytes] = queue.Queue()
        self._partial = b""
        self.rebuilding = False  # compat with RTPPacketManager

    def read(self, length: int = 160) -> bytes:
        """Called by pyVoIP's trans() thread every ~20ms."""
        # Collect enough bytes for one frame
        while len(self._partial) < length:
            try:
                chunk = self._q.get(timeout=0.005)
                self._partial += chunk
            except queue.Empty:
                break

        if len(self._partial) >= length:
            result = self._partial[:length]
            self._partial = self._partial[length:]
            return result

        # Not enough data — return what we have + silence padding
        result = self._partial + b"\x80" * (length - len(self._partial))
        self._partial = b""
        return result

    def write(self, offset: int, data: bytes) -> None:
        """Called by VoIPCall.write_audio() → RTPClient.write()."""
        self._q.put(data)


class SIPSink(Stage):
    """Sink stage: writes audio into a pyVoIP SIP call.

    Input: unsigned 8-bit PCM (u8) mono @ 8000 Hz.
    Converters are auto-inserted by pipe() if the upstream stage
    outputs a different format (e.g. s16le @ 22050 from TTS).

    Replaces pyVoIP's broken output buffer with a thread-safe queue,
    then feeds audio through the normal write_audio() path. pyVoIP's
    trans() thread handles RTP framing, encoding, and pacing.

    Terminal sink — drives the pipeline like WebSocketWriter.run().
    """

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.input_format = AudioFormat(8000, "u8")

    def run(self) -> None:
        """Drive the pipeline and write all audio to the SIP call."""
        if not self.upstream:
            return

        if not self.session.connected.is_set():
            _LOGGER.info("SIPSink: waiting for call to connect...")
            self.session.connected.wait(timeout=30)

        if self.session.hungup.is_set():
            _LOGGER.warning("SIPSink: call already hung up")
            return

        call = self.session.call
        if not call.RTPClients:
            _LOGGER.warning("SIPSink: no RTP clients")
            return

        rtp = call.RTPClients[0]
        _LOGGER.info("SIPSink: streaming to %s:%d", rtp.outIP, rtp.outPort)

        # Replace pyVoIP's broken BytesIO buffer with our queue-based buffer.
        # The trans() thread (already running) will now read from the queue.
        rtp.pmout = _AudioQueue()

        try:
            for pcm in self.upstream.stream_pcm24k():
                if self.cancelled or self.session.hungup.is_set():
                    break
                # Write in 160-byte frames (20ms @ 8kHz u8)
                for i in range(0, len(pcm), _FRAME_BYTES):
                    if self.cancelled or self.session.hungup.is_set():
                        break
                    chunk = pcm[i:i + _FRAME_BYTES]
                    if not chunk:
                        continue
                    # Pad incomplete final frame with silence
                    if len(chunk) < _FRAME_BYTES:
                        chunk = chunk + b"\x80" * (_FRAME_BYTES - len(chunk))
                    call.write_audio(chunk)
        except Exception as e:
            if not self.cancelled:
                _LOGGER.warning("SIPSink write error: %s", e)
        finally:
            _LOGGER.info("SIPSink: stream ended")
