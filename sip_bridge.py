#!/usr/bin/env python3
"""SIP Conference Bridge CLI tool.

Registers as a normal SIP client and dials into an Asterisk conference.
No sudo, no AMI, no AudioSocket — just a standard SIP call.

- RX: conference audio -> STT -> console output (transcription)
- TX: console input -> TTS -> conference audio (speech)

Audio format conversion (u8 <-> s16le, sample rate changes) is handled
automatically by the Stage.pipe() auto-format system.

Usage:
  # Setup first (once):
  sudo bash asterisk/setup.sh

  # Then just run:
  python3 sip_bridge.py

  # Call extension 800 with your softphone to join the conference.
  # Speak -> transcription appears in console.
  # Type text + Enter -> spoken into conference.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_LOGGER = logging.getLogger("sip-bridge")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SIP Conference Bridge with STT/TTS via pyVoIP"
    )
    parser.add_argument("--sip-server", default="127.0.0.1",
                        help="SIP server address (default: 127.0.0.1)")
    parser.add_argument("--sip-port", type=int, default=5060,
                        help="SIP server port (default: 5060)")
    parser.add_argument("--sip-user", default="piper",
                        help="SIP username (default: piper)")
    parser.add_argument("--sip-password", default="piper123",
                        help="SIP password (default: piper123)")
    parser.add_argument("--extension", default="800",
                        help="Extension to dial (default: 800)")
    parser.add_argument("--voice", default="de_DE-thorsten-medium",
                        help="TTS voice model ID")
    parser.add_argument("--voices-path", default="voices-piper",
                        help="Directory containing .onnx voice models")
    parser.add_argument("--language", "--lang", default="de",
                        help="STT language (default: de)")
    parser.add_argument("--whisper-model", default="base",
                        help="Whisper model size (default: base)")
    parser.add_argument("--cuda", action="store_true",
                        help="Use GPU for TTS")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # Quiet noisy per-chunk loggers unless --debug
    if not args.debug:
        logging.getLogger("faster_whisper").setLevel(logging.WARNING)

    from lib.SIPSession import SIPSession
    from lib.SIPSource import SIPSource
    from lib.SIPSink import SIPSink
    from lib.WhisperSTT import WhisperTranscriber
    from lib.StreamingTTSProducer import StreamingTTSProducer
    from lib.CLIReader import CLIReader
    from lib.CLIWriter import CLIWriter
    from lib.registry import TTSRegistry

    # Load TTS voice
    registry = TTSRegistry(args.voices_path, use_cuda=args.cuda)
    if not registry.index:
        sys.stderr.write(f"ERROR: No voices found in {args.voices_path}\n")
        sys.exit(1)
    voice_id = args.voice
    if voice_id not in registry.index:
        available = ", ".join(sorted(registry.index.keys()))
        sys.stderr.write(f"ERROR: Voice '{voice_id}' not found. Available: {available}\n")
        sys.exit(1)
    voice = registry.ensure_loaded(voice_id)
    syn = registry.create_synthesis_config(voice, {})

    # Connect to SIP conference
    sys.stderr.write(
        f"Connecting {args.sip_user}@{args.sip_server}:{args.sip_port} -> ext {args.extension}...\n"
    )
    session = SIPSession(
        target=args.extension,
        server=args.sip_server,
        port=args.sip_port,
        username=args.sip_user,
        password=args.sip_password,
    )
    try:
        session.start()
    except RuntimeError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        sys.stderr.write(
            "  Ensure: sudo bash asterisk/setup.sh && systemctl start asterisk\n"
        )
        sys.exit(1)

    sys.stderr.write(
        "\nBot joined conference! Type text + Enter to speak. Ctrl+D or 'quit' to exit.\n"
        "Call extension 800 with your softphone to join.\n\n"
    )

    all_stages = []
    cancel_event = threading.Event()

    def cancel_all():
        cancel_event.set()
        for s in all_stages:
            try:
                s.cancel()
            except Exception:
                pass
        try:
            session.hangup()
        except Exception:
            pass

    def sigint_handler(sig, frame):
        sys.stderr.write("\n[Ctrl+C] Shutting down...\n")
        cancel_all()
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    # RX: SIPSource(u8@8k) -> [auto: EncodingConverter + SampleRateConverter] -> STT(s16le@16k) -> CLIWriter
    def rx_thread():
        try:
            source = SIPSource(session)
            transcriber = WhisperTranscriber(
                model_size=args.whisper_model,
                chunk_seconds=3.0,
                language=args.language,
            )
            writer = CLIWriter(mode="ndjson")

            # pipe() auto-inserts EncodingConverter(u8->s16le) + SampleRateConverter(8k->16k)
            source.pipe(transcriber).pipe(writer)
            all_stages.extend([source, transcriber, writer])
            writer.run()
        except Exception as e:
            if not cancel_event.is_set():
                _LOGGER.error("RX pipeline error: %s", e)

    # TX: CLIReader -> TTS(s16le@native) -> [auto: SampleRateConverter + EncodingConverter] -> SIPSink(u8@8k)
    def tx_thread():
        try:
            reader = CLIReader(prompt="")
            tts = StreamingTTSProducer(reader.text_lines(), voice, syn)
            sink = SIPSink(session)

            # pipe() auto-inserts SampleRateConverter(native->8k) + EncodingConverter(s16le->u8)
            tts.pipe(sink)
            all_stages.extend([reader, tts, sink])
            sink.run()
        except Exception as e:
            if not cancel_event.is_set():
                _LOGGER.error("TX pipeline error: %s", e)

    t_rx = threading.Thread(target=rx_thread, daemon=True, name="rx")
    t_rx.start()

    t_tx = threading.Thread(target=tx_thread, daemon=True, name="tx")
    t_tx.start()

    # Wait until call ends, stdin closes, or user quits
    try:
        t_tx.join()
    except KeyboardInterrupt:
        pass

    cancel_all()
    t_rx.join(timeout=3.0)

    sys.stderr.write("Disconnected.\n")


if __name__ == "__main__":
    main()
