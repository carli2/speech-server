from __future__ import annotations

from typing import Iterator, Optional


class Stage:
    def __init__(self) -> None:
        self.upstream: Optional[Stage] = None
        self.downstream: Optional[Stage] = None
        self.cancelled: bool = False

    def set_upstream(self, up: Stage) -> Stage:
        self.upstream = up
        up.downstream = self
        return self

    def pipe(self, next_stage: "Stage") -> "Stage":
        return next_stage.set_upstream(self)

    def cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        try:
            if self.upstream:
                self.upstream.cancel()
        except Exception:
            pass
        try:
            if self.downstream:
                self.downstream.cancel()
        except Exception:
            pass

    def estimate_frames_24k(self) -> Optional[int]:
        return None

    def stream_pcm24k(self) -> Iterator[bytes]:
        if False:
            yield b""

