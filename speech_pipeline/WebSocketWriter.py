from __future__ import annotations

import logging
from typing import Iterator

from .base import Stage

_LOGGER = logging.getLogger("piper-multi-server")

END_SENTINEL = "__END__"


class WebSocketWriter(Stage):
    """Reusable WebSocket sink stage for PCM audio.

    Reads ``upstream.stream_pcm24k()``, sends binary WS messages
    (chunked to *max_chunk_bytes*), then sends ``__END__`` text message.
    """

    def __init__(self, ws, upstream: Stage, max_chunk_bytes: int = 4800) -> None:
        super().__init__()
        self.ws = ws
        self.set_upstream(upstream)
        self.max_chunk_bytes = max_chunk_bytes

    def run(self) -> None:
        """Drive the pipeline and write all PCM to the WebSocket."""
        try:
            for pcm in self.upstream.stream_pcm24k():
                if self.cancelled:
                    break
                off = 0
                while off < len(pcm):
                    end = min(off + self.max_chunk_bytes, len(pcm))
                    self.ws.send(pcm[off:end])
                    off = end
        except Exception as e:
            _LOGGER.warning("WebSocketWriter error: %s", e)
        finally:
            try:
                self.ws.send(END_SENTINEL)
            except Exception:
                pass
