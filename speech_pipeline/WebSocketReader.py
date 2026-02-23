from __future__ import annotations

import logging
from typing import Iterator

from .base import Stage

_LOGGER = logging.getLogger("piper-multi-server")

END_SENTINEL = "__END__"


class WebSocketReader(Stage):
    """Reusable WebSocket source stage for text AND binary data.

    Wraps a flask-sock ``ws`` object.  Two iterators:
    - ``text_lines()``   — yields text messages (for TTS input, chat, …)
    - ``stream_pcm24k()`` — yields binary messages as raw PCM bytes (for STT, SIP, …)

    Both stop on the ``__END__`` sentinel, WebSocket close, or pipeline cancel.
    """

    def __init__(self, ws) -> None:
        super().__init__()
        self.ws = ws

    # -- text iterator (TTS input) ------------------------------------------

    def text_lines(self) -> Iterator[str]:
        while not self.cancelled:
            try:
                msg = self.ws.receive(timeout=60)
            except Exception:
                break
            if msg is None:
                break
            if isinstance(msg, bytes):
                continue  # skip binary in text mode
            text = msg.strip()
            if text == END_SENTINEL:
                break
            if text:
                yield text

    # -- binary iterator (STT / SIP input) ----------------------------------

    def stream_pcm24k(self) -> Iterator[bytes]:
        while not self.cancelled:
            try:
                msg = self.ws.receive(timeout=60)
            except Exception:
                break
            if msg is None:
                break
            if isinstance(msg, str):
                if msg.strip() == END_SENTINEL:
                    break
                continue  # skip text in binary mode
            if msg:
                yield msg
