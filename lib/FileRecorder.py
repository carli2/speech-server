from __future__ import annotations

import logging
from subprocess import PIPE, Popen
from typing import Iterator

from .base import AudioFormat, Stage

_LOGGER = logging.getLogger("file-recorder")


class FileRecorder(Stage):
    """Terminal sink: records PCM to a file via ffmpeg.

    Streams PCM directly to ffmpeg's stdin — ffmpeg infers the output codec
    from the file extension (.mp3, .wav, .ogg, .flac, etc.).

    No temporary files are created; encoding happens in a single pass.

    This is a terminal sink (no ``output_format``). Drive it with ``run()``.
    """

    def __init__(self, filename: str, sample_rate: int = 16000) -> None:
        super().__init__()
        self.filename = filename
        self.sample_rate = sample_rate
        self.input_format = AudioFormat(sample_rate, "s16le")
        self._proc = None

    def run(self) -> None:
        """Drive the pipeline: read all upstream PCM and write to file."""
        if not self.upstream:
            _LOGGER.warning("FileRecorder: no upstream")
            return

        cmd = [
            "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
            "-f", "s16le", "-ac", "1", "-ar", str(self.sample_rate),
            "-i", "pipe:0",
            self.filename,
        ]
        _LOGGER.info("FileRecorder: %s (%d Hz) -> %s", self.filename, self.sample_rate, " ".join(cmd))

        self._proc = Popen(cmd, stdin=PIPE)
        try:
            for pcm in self.upstream.stream_pcm24k():
                if self.cancelled:
                    break
                self._proc.stdin.write(pcm)
        except BrokenPipeError:
            _LOGGER.warning("FileRecorder: ffmpeg pipe broken")
        except Exception as e:
            if not self.cancelled:
                _LOGGER.warning("FileRecorder error: %s", e)
        finally:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=10)
            except Exception:
                self._proc.kill()
            _LOGGER.info("FileRecorder: done -> %s", self.filename)

    def cancel(self) -> None:
        super().cancel()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
