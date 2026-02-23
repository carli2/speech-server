from __future__ import annotations

import logging
import queue
import threading
from typing import Iterator, List

from .base import AudioFormat, Stage
from .QueueSource import QueueSource

_LOGGER = logging.getLogger("audio-tee")

# Bounded queue size — ~4 seconds at 16kHz/20ms frames.
# put_nowait() drops on full so the main pipeline never blocks.
_QUEUE_MAXSIZE = 200


class AudioTee(Stage):
    """Processor: pass-through that copies data to side-chain sinks.

    Every chunk from upstream is yielded unchanged to downstream AND
    pushed into registered side-chain queues. Side-chain sinks run in
    daemon threads so they don't block the main pipeline.

    Backpressure: bounded queues (maxsize=200), ``put_nowait()`` drops
    on full with a warning. Main pipeline never blocks.
    """

    def __init__(self, sample_rate: int, encoding: str = "s16le") -> None:
        super().__init__()
        fmt = AudioFormat(sample_rate, encoding)
        self.input_format = fmt
        self.output_format = fmt
        self._sidechain_queues: List[queue.Queue] = []
        self._sidechain_sinks: List[Stage] = []
        self._mixer_queues: List[queue.Queue] = []
        self._threads: List[threading.Thread] = []

    def add_sidechain(self, sink: Stage) -> QueueSource:
        """Register a sink as a side-chain consumer.

        Returns the QueueSource that feeds the sink (already piped).
        The sink's ``run()`` method will be called in a daemon thread.
        """
        q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        src = QueueSource(q, self.output_format.sample_rate, self.output_format.encoding)
        src.pipe(sink)
        self._sidechain_queues.append(q)
        self._sidechain_sinks.append(sink)
        return src

    def add_mixer_feed(self, mixer_queue: queue.Queue) -> None:
        """Register a raw queue to feed (for named mixers).

        The queue receives the same chunks as the main pipeline.
        """
        self._mixer_queues.append(mixer_queue)

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.upstream:
            return

        # Start side-chain sink threads
        for sink in self._sidechain_sinks:
            t = threading.Thread(target=self._run_sink, args=(sink,), daemon=True)
            t.start()
            self._threads.append(t)

        all_queues = self._sidechain_queues + self._mixer_queues

        try:
            for chunk in self.upstream.stream_pcm24k():
                if self.cancelled:
                    break
                # Copy to all side-chain and mixer queues
                for q in all_queues:
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:
                        _LOGGER.warning("AudioTee: queue full, dropping chunk")
                # Pass through to downstream
                yield chunk
        finally:
            # Send EOF sentinel to all queues
            for q in all_queues:
                try:
                    q.put(None, timeout=1.0)
                except Exception:
                    pass
            # Wait for side-chain threads to finish
            for t in self._threads:
                t.join(timeout=5.0)

    @staticmethod
    def _run_sink(sink: Stage) -> None:
        """Run a side-chain sink in a background thread."""
        try:
            sink.run()
        except Exception as e:
            _LOGGER.warning("AudioTee side-chain error: %s", e)

    def cancel(self) -> None:
        super().cancel()
        for sink in self._sidechain_sinks:
            try:
                sink.cancel()
            except Exception:
                pass
