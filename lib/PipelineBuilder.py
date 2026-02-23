from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from .base import Stage

_LOGGER = logging.getLogger("pipeline-builder")


class PipelineRun:
    """Encapsulates a runnable pipeline with cancel support."""

    def __init__(self) -> None:
        self.stages: List[Stage] = []
        self.sip_sessions: List[Any] = []
        self._run_fn = None
        self._cancel_extras: List[Any] = []

    def run(self) -> None:
        if self._run_fn:
            self._run_fn()

    def cancel(self) -> None:
        for s in self.stages:
            try:
                s.cancel()
            except Exception:
                pass
        for sess in self.sip_sessions:
            try:
                sess.hangup()
            except Exception:
                pass
        for extra in self._cancel_extras:
            try:
                extra()
            except Exception:
                pass


class PipelineBuilder:
    """Parses pipeline DSL strings and builds runnable stage pipelines.

    DSL syntax: ``element | element | ... | element``
    Element:    ``type:param1:param2``

    Supported element types:
        ws:pcm      WebSocket binary PCM source/sink
        ws:text     WebSocket text source/sink
        ws:ndjson   WebSocket NDJSON sink (text frames)
        cli:text    CLI stdin text source / stdout text sink
        cli:ndjson  CLI stdout NDJSON sink
        resample    SampleRateConverter  (resample:FROM:TO)
        stt         WhisperTranscriber   (stt:LANG)
        tts         StreamingTTSProducer (tts:VOICE)
        sip         SIPSource/SIPSink    (sip:TARGET)
        vc          VCConverter          (vc:VOICE2)
        pitch       PitchAdjuster        (pitch:ST)
    """

    def __init__(self, ws, registry, args) -> None:
        self.ws = ws
        self.registry = registry
        self.args = args
        self._sip_sessions: Dict[str, Any] = {}

    def parse(self, pipe_str: str) -> List[Tuple[str, List[str]]]:
        """Parse ``'a:x:y | b:z | c'`` into ``[('a', ['x','y']), ('b', ['z']), ('c', [])]``."""
        elements = []
        for part in pipe_str.split("|"):
            part = part.strip()
            if not part:
                continue
            tokens = part.split(":")
            typ = tokens[0].strip()
            params = [t.strip() for t in tokens[1:]]
            elements.append((typ, params))
        return elements

    def build(self, pipe_str: str) -> PipelineRun:
        elements = self.parse(pipe_str)
        if not elements:
            raise ValueError("Empty pipeline")

        run = PipelineRun()
        # Track output type through the pipeline
        # 'pcm' | 'text' | 'ndjson_bytes'
        current_output_type: Optional[str] = None
        current_stage: Optional[Stage] = None
        # For text iterators (not Stage subclasses)
        current_text_iter = None

        for i, (typ, params) in enumerate(elements):
            is_first = (i == 0)
            is_last = (i == len(elements) - 1)

            if typ == "ws":
                subtype = params[0] if params else "pcm"

                if is_first:
                    # Source
                    from .WebSocketReader import WebSocketReader
                    reader = WebSocketReader(self.ws)
                    run.stages.append(reader)

                    if subtype == "pcm":
                        current_stage = reader
                        current_output_type = "pcm"
                    elif subtype == "text":
                        current_text_iter = reader.text_lines()
                        current_output_type = "text"
                        current_stage = None
                    elif subtype == "ndjson":
                        raise ValueError("ws:ndjson cannot be a source")
                    else:
                        raise ValueError(f"Unknown ws subtype: {subtype}")

                elif is_last:
                    # Sink
                    if subtype == "pcm":
                        from .WebSocketWriter import WebSocketWriter
                        writer = WebSocketWriter(self.ws, current_stage, max_chunk_bytes=4800)
                        run.stages.append(writer)
                        run._run_fn = writer.run
                    elif subtype == "ndjson":
                        # NDJSON bytes from STT -> send as text frames
                        stage = current_stage
                        def make_ndjson_sender(st):
                            def sender():
                                try:
                                    for chunk in st.stream_pcm24k():
                                        for line in chunk.decode("utf-8", errors="replace").splitlines():
                                            line = line.strip()
                                            if line:
                                                self.ws.send(line)
                                except Exception as e:
                                    _LOGGER.warning("ws:ndjson sender error: %s", e)
                                finally:
                                    try:
                                        self.ws.send("__END__")
                                    except Exception:
                                        pass
                            return sender
                        run._run_fn = make_ndjson_sender(stage)
                    elif subtype == "text":
                        # Text output -> send as text frames
                        if current_output_type == "ndjson_bytes":
                            from .NdjsonToText import NdjsonToText
                            adapter = NdjsonToText(current_stage)
                            def make_text_sender(it):
                                def sender():
                                    try:
                                        for text in it:
                                            self.ws.send(text)
                                    except Exception as e:
                                        _LOGGER.warning("ws:text sender error: %s", e)
                                    finally:
                                        try:
                                            self.ws.send("__END__")
                                        except Exception:
                                            pass
                                return sender
                            run._run_fn = make_text_sender(adapter)
                        elif current_text_iter is not None:
                            def make_text_sender2(it):
                                def sender():
                                    try:
                                        for text in it:
                                            self.ws.send(text)
                                    except Exception as e:
                                        _LOGGER.warning("ws:text sender error: %s", e)
                                    finally:
                                        try:
                                            self.ws.send("__END__")
                                        except Exception:
                                            pass
                                return sender
                            run._run_fn = make_text_sender2(current_text_iter)
                        else:
                            raise ValueError("ws:text sink requires text or ndjson upstream")
                    else:
                        raise ValueError(f"Unknown ws subtype: {subtype}")

                else:
                    raise ValueError("ws element can only appear at start or end of pipeline")

            elif typ == "cli":
                subtype = params[0] if params else "text"

                if is_first:
                    # Source: read text from stdin
                    if subtype != "text":
                        raise ValueError("cli source only supports cli:text")
                    from .CLIReader import CLIReader
                    reader = CLIReader()
                    run.stages.append(reader)
                    current_text_iter = reader.text_lines()
                    current_output_type = "text"
                    current_stage = None

                elif is_last:
                    # Sink: write to stdout
                    from .CLIWriter import CLIWriter
                    if subtype == "ndjson":
                        writer = CLIWriter(mode="ndjson", prefix="[STT] ")
                    elif subtype == "text":
                        if current_output_type == "ndjson_bytes":
                            writer = CLIWriter(mode="ndjson", prefix="[STT] ")
                        else:
                            writer = CLIWriter(mode="text")
                    elif subtype == "raw":
                        writer = CLIWriter(mode="raw")
                    else:
                        raise ValueError(f"Unknown cli subtype: {subtype}")
                    if current_stage:
                        current_stage.pipe(writer)
                    run.stages.append(writer)
                    run._run_fn = writer.run

                else:
                    raise ValueError("cli element can only appear at start or end of pipeline")

            elif typ == "resample":
                from .SampleRateConverter import SampleRateConverter
                src = int(params[0]) if len(params) > 0 else 48000
                dst = int(params[1]) if len(params) > 1 else 16000
                stage = SampleRateConverter(src, dst)
                if current_stage:
                    current_stage.pipe(stage)
                current_stage = stage
                run.stages.append(stage)
                current_output_type = "pcm"

            elif typ == "stt":
                from .WhisperSTT import WhisperTranscriber
                lang = params[0] if params else None
                chunk_seconds = float(params[1]) if len(params) > 1 else 3.0
                model_size = getattr(self.args, "whisper_model", "base")
                stage = WhisperTranscriber(model_size, chunk_seconds=chunk_seconds, language=lang)
                if current_stage:
                    current_stage.pipe(stage)
                current_stage = stage
                run.stages.append(stage)
                current_output_type = "ndjson_bytes"

            elif typ == "tts":
                from .StreamingTTSProducer import StreamingTTSProducer
                voice_id = params[0] if params else None
                if not voice_id:
                    # Use server default
                    voice_id = sorted(self.registry.index.keys())[0] if self.registry.index else None
                if not voice_id:
                    raise ValueError("tts: no voice specified and no default available")
                voice = self.registry.ensure_loaded(voice_id)
                syn = self.registry.create_synthesis_config(voice, {})

                # Determine text input based on upstream type
                if current_output_type == "ndjson_bytes" and current_stage:
                    from .NdjsonToText import NdjsonToText
                    text_iter = NdjsonToText(current_stage)
                elif current_output_type == "text" and current_text_iter is not None:
                    text_iter = current_text_iter
                else:
                    raise ValueError(f"tts requires text or ndjson upstream, got {current_output_type}")

                stage = StreamingTTSProducer(text_iter, voice, syn)
                # TTS is a source (no .pipe from upstream PCM stage)
                current_stage = stage
                run.stages.append(stage)
                current_output_type = "pcm"
                current_text_iter = None

            elif typ == "sip":
                target = params[0] if params else None
                if not target:
                    raise ValueError("sip requires a target (e.g. sip:100@pbx)")

                session = self._get_or_create_sip_session(target)
                run.sip_sessions.append(session)

                if is_first:
                    from .SIPSource import SIPSource
                    stage = SIPSource(session)
                    current_stage = stage
                    run.stages.append(stage)
                    current_output_type = "pcm"
                elif is_last:
                    from .SIPSink import SIPSink
                    sink = SIPSink(session)
                    if current_stage:
                        current_stage.pipe(sink)
                    run.stages.append(sink)
                    run._run_fn = sink.run
                else:
                    raise ValueError("sip element can only appear at start or end of pipeline")

            elif typ == "vc":
                from .VCConverter import VCConverter
                voice2 = params[0] if params else None
                if not voice2:
                    raise ValueError("vc requires a target voice ID")
                from .FileFetcher import FileFetcher
                here = __import__("pathlib").Path(__file__).resolve().parent.parent
                tmpl = getattr(self.args, "soundpath", "../voices/%s.wav")
                ref = FileFetcher.build_ref(voice2, tmpl, here)
                bearer = getattr(self.args, "bearer", "")
                stage = VCConverter(ref, bearer=bearer)
                if current_stage:
                    current_stage.pipe(stage)
                current_stage = stage
                run.stages.append(stage)
                current_output_type = "pcm"

            elif typ == "pitch":
                from .PitchAdjuster import PitchAdjuster
                st = float(params[0]) if params else 0.0
                # Pitch adjuster needs a target ref; for standalone pitch just use override
                stage = PitchAdjuster(
                    target_ref="",
                    pitch_disable=(abs(st) < 0.05),
                    pitch_override_st=st,
                    correction=1.0,
                )
                if current_stage:
                    current_stage.pipe(stage)
                current_stage = stage
                run.stages.append(stage)
                current_output_type = "pcm"

            else:
                raise ValueError(f"Unknown pipeline element: {typ}")

        # If no explicit run_fn was set (e.g. pipeline ends with a processor),
        # default to draining the last stage
        if run._run_fn is None and current_stage is not None:
            stage = current_stage
            def make_drain(st):
                def drain():
                    for _ in st.stream_pcm24k():
                        pass
                return drain
            run._run_fn = make_drain(stage)

        return run

    def build_multi(self, pipes: List[str]) -> List[PipelineRun]:
        """Build multiple pipelines. SIP sessions with the same target are shared."""
        return [self.build(p) for p in pipes]

    def _get_or_create_sip_session(self, target: str):
        if target in self._sip_sessions:
            return self._sip_sessions[target]
        from .SIPSession import SIPSession
        session = SIPSession(
            target=target,
            server=getattr(self.args, "sip_server", "127.0.0.1"),
            port=getattr(self.args, "sip_port", 5060),
            username=getattr(self.args, "sip_user", "piper"),
            password=getattr(self.args, "sip_password", "piper123"),
        )
        session.start()
        self._sip_sessions[target] = session
        return session
