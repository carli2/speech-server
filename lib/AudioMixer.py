from __future__ import annotations

import audioop
import logging
import queue
import time
from typing import Iterator, List

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("audio-mixer")


class AudioMixer(Stage):
    """Source stage: mixes N input queues into a single PCM output.

    Each input is a ``queue.Queue[bytes | None]`` fed by an AudioTee
    (via ``add_mixer_feed()``) or directly by application code.

    Mixing uses ``audioop.add()`` on fixed-size frames (default 20ms).
    Sources finishing at different times contribute silence.
    The mixer continues until ALL inputs have sent the ``None`` sentinel.
    """

    def __init__(self, name: str, sample_rate: int = 16000, frame_ms: int = 20) -> None:
        super().__init__()
        self.name = name
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * 2  # s16le = 2 bytes/sample
        self.output_format = AudioFormat(sample_rate, "s16le")
        self._inputs: List[queue.Queue] = []
        self._buffers: List[bytearray] = []
        self._finished: List[bool] = []

    def add_input(self) -> queue.Queue:
        """Register an input source. Returns queue to push PCM into.

        Push ``bytes`` chunks to feed audio. Push ``None`` to signal EOF.
        """
        q: queue.Queue = queue.Queue(maxsize=200)
        self._inputs.append(q)
        self._buffers.append(bytearray())
        self._finished.append(False)
        return q

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self._inputs:
            _LOGGER.warning("AudioMixer '%s': no inputs registered", self.name)
            return

        _LOGGER.info("AudioMixer '%s': mixing %d inputs @ %d Hz, %d ms frames",
                      self.name, len(self._inputs), self.sample_rate, self.frame_ms)

        silence = b"\x00" * self.frame_bytes

        while not self.cancelled:
            # Drain all input queues into per-source buffers
            for i, q in enumerate(self._inputs):
                if self._finished[i]:
                    continue
                while True:
                    try:
                        chunk = q.get_nowait()
                    except queue.Empty:
                        break
                    if chunk is None:
                        self._finished[i] = True
                        _LOGGER.debug("AudioMixer '%s': input %d finished", self.name, i)
                        break
                    self._buffers[i].extend(chunk)

            # All inputs finished and all buffers drained?
            if all(self._finished):
                remaining = sum(len(b) for b in self._buffers)
                if remaining < self.frame_bytes:
                    break

            # Check if we have at least one frame from any source
            has_data = any(len(b) >= self.frame_bytes for b in self._buffers)
            if not has_data and not all(self._finished):
                # No data yet — sleep briefly and retry
                time.sleep(self.frame_ms / 1000.0)
                continue

            # Extract one frame from each buffer, mix together
            mixed = silence
            for i, buf in enumerate(self._buffers):
                if len(buf) >= self.frame_bytes:
                    frame = bytes(buf[:self.frame_bytes])
                    del buf[:self.frame_bytes]
                else:
                    frame = silence
                mixed = audioop.add(mixed, frame, 2)

            yield mixed

        _LOGGER.info("AudioMixer '%s': done", self.name)
