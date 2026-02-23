from __future__ import annotations

import logging
import queue
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("queue-source")


class QueueSource(Stage):
    """Source stage: reads PCM from a ``queue.Queue[bytes | None]``.

    Push ``bytes`` chunks into the queue to feed the pipeline.
    Push ``None`` as EOF sentinel to signal end of stream.

    Used by AudioTee (side-chain sinks) and AudioMixer (input feeds).
    """

    def __init__(self, q: queue.Queue, sample_rate: int, encoding: str = "s16le") -> None:
        super().__init__()
        self.q = q
        self.output_format = AudioFormat(sample_rate, encoding)

    def stream_pcm24k(self) -> Iterator[bytes]:
        while not self.cancelled:
            try:
                chunk = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if chunk is None:
                break
            yield chunk
