from __future__ import annotations

import json
import logging
from typing import Iterator

from .base import Stage

_LOGGER = logging.getLogger("piper-multi-server")


class NdjsonToText:
    """Adapter: extracts .text from NDJSON bytes produced by WhisperTranscriber.

    Not a Stage subclass (outputs text strings, not PCM).
    Used by PipelineBuilder to bridge stt -> tts transitions.
    """

    def __init__(self, upstream: Stage) -> None:
        self.upstream = upstream

    def __iter__(self) -> Iterator[str]:
        for chunk in self.upstream.stream_pcm24k():
            # WhisperTranscriber yields NDJSON bytes, possibly multiple lines
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    text = obj.get("text", "").strip()
                    if text:
                        yield text
                except (json.JSONDecodeError, AttributeError):
                    _LOGGER.warning("NdjsonToText: skipping invalid line: %s", line[:80])
