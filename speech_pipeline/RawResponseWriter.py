from __future__ import annotations

from typing import Iterator


class RawResponseWriter:
    def __init__(self, upstream, chunk_bytes: int = 64 * 1024) -> None:
        self.upstream = upstream
        self.chunk_bytes = int(max(4096, chunk_bytes))

    def cancel(self) -> None:
        try:
            if hasattr(self.upstream, 'cancel'):
                self.upstream.cancel()
        except Exception:
            pass

    def stream(self) -> Iterator[bytes]:
        read = getattr(self.upstream, 'read', None)
        if callable(read):
            while True:
                buf = read(self.chunk_bytes)
                if not buf:
                    break
                yield buf
        else:
            # Fallback to .stream() generator contract if provided
            stream_gen = getattr(self.upstream, 'stream', None)
            if callable(stream_gen):
                for b in stream_gen():
                    yield b
