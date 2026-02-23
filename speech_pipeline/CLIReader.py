from __future__ import annotations

import logging
import sys
import threading
from typing import Iterator

from .base import Stage

_LOGGER = logging.getLogger("cli-reader")


class CLIReader(Stage):
    """Source stage: reads text lines from stdin.

    CLI equivalent of WebSocketReader.text_lines().
    Yields text strings (not PCM) — use with StreamingTTSProducer.

    Reads in a background thread so the main thread can handle other work.
    Type a line and press Enter to send it to TTS. Ctrl+D or 'quit' to stop.
    """

    def __init__(self, prompt: str = "> ") -> None:
        super().__init__()
        self.prompt = prompt

    def text_lines(self) -> Iterator[str]:
        while not self.cancelled:
            try:
                if self.prompt and sys.stdin.isatty():
                    sys.stderr.write(self.prompt)
                    sys.stderr.flush()
                line = sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:  # EOF
                break
            text = line.strip()
            if text.lower() in ("quit", "exit", "__END__"):
                break
            if text:
                yield text

    def stream_pcm24k(self) -> Iterator[bytes]:
        # CLIReader is text-only; this should not be called directly.
        # PipelineBuilder uses text_lines() instead.
        raise NotImplementedError("CLIReader produces text, not PCM. Use text_lines().")
