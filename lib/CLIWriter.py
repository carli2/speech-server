from __future__ import annotations

import json
import logging
import sys
from typing import Iterator

from .base import Stage

_LOGGER = logging.getLogger("cli-writer")


class CLIWriter(Stage):
    """Sink stage: writes text to stdout (pipe-friendly).

    All status/error messages go to stderr via logging.
    stdout gets only clean output lines, suitable for piping.

    Modes:
    - 'ndjson': upstream yields NDJSON bytes (WhisperTranscriber output),
      extracts .text and prints each line.
    - 'text': upstream yields text strings, prints directly.
    - 'raw': upstream yields NDJSON bytes, prints raw JSON lines.
    """

    def __init__(self, mode: str = "ndjson") -> None:
        super().__init__()
        self.mode = mode

    def run(self) -> None:
        """Drive the pipeline and write output to stdout."""
        if not self.upstream:
            return

        try:
            if self.mode == "ndjson":
                self._run_ndjson()
            elif self.mode == "raw":
                self._run_raw()
            else:
                self._run_text()
        except Exception as e:
            _LOGGER.warning("CLIWriter error: %s", e)

    def _run_ndjson(self) -> None:
        """Parse NDJSON bytes from upstream, print extracted text."""
        for chunk in self.upstream.stream_pcm24k():
            if self.cancelled:
                break
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    text = obj.get("text", "").strip()
                    if text:
                        sys.stdout.write(f"{text}\n")
                        sys.stdout.flush()
                except (json.JSONDecodeError, AttributeError):
                    sys.stdout.write(f"{line}\n")
                    sys.stdout.flush()

    def _run_raw(self) -> None:
        """Print raw NDJSON bytes from upstream."""
        for chunk in self.upstream.stream_pcm24k():
            if self.cancelled:
                break
            sys.stdout.write(chunk.decode("utf-8", errors="replace"))
            sys.stdout.flush()

    def _run_text(self) -> None:
        """Print text from a text iterator."""
        for chunk in self.upstream.stream_pcm24k():
            if self.cancelled:
                break
            sys.stdout.write(chunk.decode("utf-8", errors="replace"))
            sys.stdout.flush()
