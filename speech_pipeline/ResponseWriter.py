from __future__ import annotations

from typing import Iterator, Optional, Any
import logging

from .base import Stage


class ResponseWriter(Stage):
    def __init__(self, upstream: Stage, est_frames_24k: Optional[int], max_chunk_bytes: Optional[int] = None) -> None:
        super().__init__()
        self.upstream = upstream
        self.est_frames = est_frames_24k
        self.max_chunk_bytes = max_chunk_bytes
        # Derive sample rate from upstream if available, else 24000
        if upstream and upstream.output_format and upstream.output_format.sample_rate > 0:
            self.sample_rate = upstream.output_format.sample_rate
        else:
            self.sample_rate = 24000

    def estimate_frames_24k(self) -> Optional[int]:
        return self.est_frames if self.est_frames is not None else (
            self.upstream.estimate_frames_24k() if self.upstream else None
        )

    def stream(self) -> Iterator[bytes]:
        log = logging.getLogger("piper-multi-server")
        sr = self.sample_rate
        est_frames = self.estimate_frames_24k()
        if est_frames is None or est_frames <= 0:
            est_frames = int(30 * sr)
        else:
            est_frames = int(est_frames)

        est_bytes_nominal = max(0, int(est_frames * 2 * 1.05))
        if est_bytes_nominal % 2:
            est_bytes_nominal += 1  # keep 16-bit alignment
        data_size = min(est_bytes_nominal, 0xFFFFFFFF)
        riff_size = min(36 + data_size, 0xFFFFFFFF)
        wav_header = (
            b"RIFF"
            + riff_size.to_bytes(4, "little", signed=False)
            + b"WAVEfmt "
            + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little")
            + (1).to_bytes(2, "little")
            + sr.to_bytes(4, "little")
            + (sr * 2).to_bytes(4, "little")
            + (2).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data"
            + data_size.to_bytes(4, "little", signed=False)
        )
        log.debug(
            "writer: header sent est_frames=%d data_bytes=%d",
            est_frames,
            data_size,
        )
        yield wav_header
        total = 0
        chunk_idx = 0
        for pcm in self.upstream.stream_pcm24k():
            chunk_idx += 1
            total += len(pcm)
            log.debug("writer: chunk=%d bytes=%d total=%d", chunk_idx, len(pcm), total)
            try:
                if self.max_chunk_bytes and len(pcm) > self.max_chunk_bytes:
                    while len(pcm) > self.max_chunk_bytes:
                        yield pcm[:self.max_chunk_bytes]
                        pcm = pcm[self.max_chunk_bytes:]
                    if pcm:
                        yield pcm
                else:
                    yield pcm
            except (GeneratorExit, BrokenPipeError):
                log.info("writer: downstream closed at chunk=%d total=%d; cancelling pipeline", chunk_idx, total)
                self.cancel()
                break
        if (not self.cancelled) and total < data_size:
            pad = data_size - total
            if pad > 0:
                log.debug("writer: padding bytes=%d to reach declared size=%d", pad, data_size)
                yield bytes(pad)
            total = data_size
        if total > data_size:
            log.warning(
                "writer: streamed bytes=%d exceed declared size=%d; header may be too small",
                total,
                data_size,
            )
        log.debug("writer: complete cancelled=%s total_bytes=%d", self.cancelled, total)

    def apply_headers(self, resp: Any) -> None:
        """Set media headers on a Flask Response (or response-like) object.
        Override defaults so browsers don't assume text/html.
        """
        try:
            # Prefer Flask API if available
            try:
                resp.mimetype = "audio/wav"  # sets Content-Type
            except Exception:
                pass
            # Ensure header is explicitly audio/wav regardless of defaults
            try:
                resp.headers["Content-Type"] = "audio/wav"
            except Exception:
                pass
            # Present inline in browser players
            try:
                resp.headers["Content-Disposition"] = "inline"
            except Exception:
                pass
            # If we can estimate a length, set HTTP Content-Length accordingly
            try:
                est_frames = self.estimate_frames_24k()
                if est_frames is None or est_frames <= 0:
                    est_frames = int(30 * self.sample_rate)
                else:
                    est_frames = int(est_frames)
                est_bytes_nominal = max(0, int(est_frames * 2 * 1.05))
                if est_bytes_nominal % 2:
                    est_bytes_nominal += 1
                total_len = 44 + min(est_bytes_nominal, 0xFFFFFFFF)
                resp.headers["Content-Length"] = str(total_len)
            except Exception:
                pass
        except Exception:
            pass
