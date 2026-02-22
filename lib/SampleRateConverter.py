from __future__ import annotations

import logging
import subprocess as _sp
import threading
from typing import Iterator, Optional

from .base import Stage

_LOGGER = logging.getLogger("sample-rate-converter")


class SampleRateConverter(Stage):
    """Resample upstream PCM (s16le mono) from src_rate to dst_rate via ffmpeg."""

    def __init__(self, src_rate: int = 48000, dst_rate: int = 16000) -> None:
        super().__init__()
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)

    def stream_pcm24k(self) -> Iterator[bytes]:
        if not self.upstream:
            return
        if self.src_rate == self.dst_rate:
            yield from self.upstream.stream_pcm24k()
            return

        _LOGGER.info("Resampling %d -> %d Hz", self.src_rate, self.dst_rate)
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-f", "s16le", "-ac", "1", "-ar", str(self.src_rate), "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(self.dst_rate), "pipe:1",
        ]
        proc = _sp.Popen(cmd, stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE)
        feeder = None
        try:
            def feed():
                try:
                    for chunk in self.upstream.stream_pcm24k():
                        if self.cancelled:
                            break
                        proc.stdin.write(chunk)
                        proc.stdin.flush()
                except BrokenPipeError:
                    pass
                except Exception as e:
                    _LOGGER.warning("SampleRateConverter feeder error: %s", e)
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

            feeder = threading.Thread(target=feed, daemon=True)
            feeder.start()

            while True:
                if self.cancelled:
                    break
                buf = proc.stdout.read(4096)
                if not buf:
                    break
                yield buf

            feeder.join(timeout=2.0)

            stderr = proc.stderr.read()
            if stderr:
                _LOGGER.warning("ffmpeg stderr: %s", stderr.decode(errors="replace").strip())
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.stderr.close()
            except Exception:
                pass
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=2.0)
            except Exception:
                pass
