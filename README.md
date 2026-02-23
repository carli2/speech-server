# speech-pipeline

A composable, real-time speech processing toolkit for Python. Build text-to-speech, speech-to-text, voice conversion, and telephony pipelines from simple building blocks -- as a library, HTTP server, or CLI tool.

Stages snap together like UNIX pipes. Format conversion (sample rate, encoding) is automatic. Streaming is the default: audio plays as it is synthesized, transcriptions arrive as words are spoken.

```
echo "Hallo Welt" | speech-pipeline run "cli:text | tts:de_DE-thorsten-medium | cli:raw" > out.raw
```

## Key Features

**Text-to-Speech** -- Multi-voice TTS via [Piper](https://github.com/rhasspy/piper) ONNX models with automatic voice discovery, streaming synthesis, and configurable speed/pitch/noise parameters.

**Speech-to-Text** -- Real-time transcription via [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with pause-based chunking, multi-language support, and NDJSON output.

**Voice Conversion** -- Transform speaker identity in real time using FreeVC (Coqui TTS). Swap any voice to sound like a target recording.

**Pitch Adjustment** -- Formant-preserving pitch shifting via ffmpeg rubberband. Auto-estimated from source/target F0 or set explicitly in semitones.

**Pipeline DSL** -- Describe complex audio flows as simple text: `ws:pcm | resample:48000:16000 | stt:de | ws:ndjson`. The builder wires up all the stages, converters, and adapters automatically.

**SIP Bridge** -- Dial into Asterisk conferences as a bot with full-duplex STT/TTS. Transcribe what others say, speak generated text back.

**Fourier Codec** -- Custom FFT-based audio codec with four quality profiles (low/medium/high/full) for compressed real-time audio over WebSockets.

**HTTP & WebSocket Server** -- Ready-to-deploy server with REST and WebSocket endpoints for TTS, STT, streaming pipelines, and codec-compressed audio. CORS enabled, pm2-friendly.

**CLI Tool** -- Run pipelines from the command line, list voices, or start the server. Pipe stdin/stdout like any UNIX tool.

## Installation

```bash
pip install -e ".[tts,stt,server]"         # core + TTS + STT + HTTP server
pip install -e ".[all]"                     # everything including VC and SIP
pip install -e /path/to/piper              # Piper TTS from source
```

System dependencies:

```bash
sudo apt install espeak-ng ffmpeg
```

### Quick Start

```bash
# Start the server
speech-pipeline serve --voices-path voices-piper

# List available voices
speech-pipeline voices --voices-path voices-piper

# Synthesize from the command line
echo "Hallo Welt" | speech-pipeline run "cli:text | tts:de_DE-thorsten-medium | cli:raw" > out.raw

# Or use the server script (creates venv, installs deps, starts server)
bash run_server.sh
```

## Library Usage

Use `speech_pipeline` as a Python library to build custom audio pipelines.

### TTS: Text to Audio File

```python
from speech_pipeline import TTSProducer, FileRecorder, SampleRateConverter
from speech_pipeline.registry import TTSRegistry

registry = TTSRegistry("voices-piper")
voice = registry.ensure_loaded("de_DE-thorsten-medium")
syn = registry.create_synthesis_config(voice, {})

source = TTSProducer(voice, syn, text="Hallo Welt!", sentence_silence=0.2)
recorder = FileRecorder("output.mp3", sample_rate=voice.config.sample_rate)
source.pipe(recorder)
recorder.run()
```

### Streaming TTS: Lines to PCM

```python
from speech_pipeline import StreamingTTSProducer
from speech_pipeline.registry import TTSRegistry

registry = TTSRegistry("voices-piper")
voice = registry.ensure_loaded("de_DE-thorsten-medium")
syn = registry.create_synthesis_config(voice, {})

lines = ["First sentence.", "Second sentence.", "Third sentence."]
source = StreamingTTSProducer(lines, voice, syn)

for pcm_chunk in source.stream_pcm24k():
    # pcm_chunk is bytes (s16le mono at voice.config.sample_rate)
    process_audio(pcm_chunk)
```

### STT: Audio File to Text

```python
from speech_pipeline import AudioReader, SampleRateConverter
from speech_pipeline.WhisperSTT import WhisperTranscriber

source = AudioReader("interview.wav")
stt = WhisperTranscriber("small", chunk_seconds=3.0, language="de")
source.pipe(SampleRateConverter(24000, 16000)).pipe(stt)

for ndjson_chunk in stt.stream_pcm24k():
    print(ndjson_chunk.decode())
    # {"text": "hallo welt", "start": 0.0, "end": 1.5}
```

### Composing Stages with `.pipe()`

Stages chain with `.pipe()`. Format conversion (sample rate, encoding) is inserted automatically when needed:

```python
from speech_pipeline import (
    TTSProducer, VCConverter, PitchAdjuster, FileRecorder
)

source = TTSProducer(voice, syn, text="Hello!")
pipeline = (
    source
    .pipe(VCConverter("target_voice.wav"))
    .pipe(PitchAdjuster("target_voice.wav", pitch_override_st=2.0))
    .pipe(FileRecorder("output.wav", sample_rate=24000))
)
pipeline.run()
```

### Running a Pipeline from DSL

```python
from speech_pipeline.PipelineBuilder import PipelineBuilder
from speech_pipeline.registry import TTSRegistry
import argparse

registry = TTSRegistry("voices-piper")
args = argparse.Namespace(whisper_model="small", cuda=False)
builder = PipelineBuilder(ws=None, registry=registry, args=args)

run = builder.build("cli:text | tts:de_DE-thorsten-medium | cli:raw")
run.run()
```

## Architecture

The library uses a pipeline of composable stages that process audio as a stream of PCM chunks. Each stage extends `Stage` (`speech_pipeline/base.py`), implements `stream_pcm24k() -> Iterator[bytes]`, and connects via `.pipe()`:

```
Source --> Processor --> Processor --> Sink
```

Format conversion between stages is automatic: `.pipe()` inserts `SampleRateConverter` and `EncodingConverter` stages when the output format of one stage does not match the input format of the next.

### Example Pipelines

```
POST /              TTS:            TTSProducer --> VCConverter --> PitchAdjuster --> ResponseWriter
POST /tts/stream    Streaming TTS:  text_lines(request.stream) --> StreamingTTSProducer --> ResponseWriter
POST /inputstream   STT:            PCMInputReader --> [SampleRateConverter] --> WhisperTranscriber --> NDJSON
WS   /ws/pipe       Generic:        DSL-defined (e.g. ws:pcm | resample:48000:16000 | stt:de | ws:ndjson)
CLI                 TTS:            cli:text | tts:de_DE-thorsten-medium | cli:raw
```

### All Stages

#### Sources (produce PCM, no upstream)

| Stage | Module | Description |
|-------|--------|-------------|
| `TTSProducer` | `speech_pipeline.TTSProducer` | Fixed text to PCM via Piper ONNX. Streams sentence by sentence. |
| `StreamingTTSProducer` | `speech_pipeline.StreamingTTSProducer` | Text iterable to PCM. Synthesizes each line as it arrives. |
| `AudioReader` | `speech_pipeline.AudioReader` | Reads audio from file/URL via ffmpeg. Bearer auth for remote files. |
| `PCMInputReader` | `speech_pipeline.PCMInputReader` | Reads raw PCM bytes from a stream (HTTP body, microphone). |
| `WebSocketReader` | `speech_pipeline.WebSocketReader` | Binary/text from flask-sock WebSocket. |
| `SIPSource` | `speech_pipeline.SIPSource` | RTP audio from a SIP call via pyVoIP. |
| `CLIReader` | `speech_pipeline.CLIReader` | Text lines from stdin. |
| `QueueSource` | `speech_pipeline.QueueSource` | PCM from a `queue.Queue`. Bridge for AudioTee/AudioMixer. |
| `AudioMixer` | `speech_pipeline.AudioMixer` | Mixes N input queues. Hot-pluggable. |
| `CodecSocketSource` | `speech_pipeline.CodecSocketSource` | Decoded PCM from Fourier codec WebSocket. |

#### Processors (transform PCM, have upstream)

| Stage | Module | Description |
|-------|--------|-------------|
| `VCConverter` | `speech_pipeline.VCConverter` | Voice conversion via FreeVC. Passthrough if unavailable. |
| `PitchAdjuster` | `speech_pipeline.PitchAdjuster` | Pitch shifting via ffmpeg rubberband (formant-preserving). |
| `SampleRateConverter` | `speech_pipeline.SampleRateConverter` | Resampling via audioop (zero-latency). No-op when rates match. |
| `EncodingConverter` | `speech_pipeline.EncodingConverter` | s16le <-> u8. Auto-inserted by `pipe()`. |
| `AudioTee` | `speech_pipeline.AudioTee` | Pass-through with side-chain sinks via queues. Hot-pluggable. |
| `GainStage` | `speech_pipeline.GainStage` | Runtime-adjustable volume. |
| `DelayLine` | `speech_pipeline.DelayLine` | Runtime-adjustable audio delay. |

#### Sinks (consume PCM, produce output)

| Stage | Module | Description |
|-------|--------|-------------|
| `ResponseWriter` | `speech_pipeline.ResponseWriter` | Streams PCM as WAV HTTP response. |
| `RawResponseWriter` | `speech_pipeline.RawResponseWriter` | Raw file passthrough. |
| `WhisperTranscriber` | `speech_pipeline.WhisperSTT` | PCM to NDJSON transcription via faster-whisper. |
| `WebSocketWriter` | `speech_pipeline.WebSocketWriter` | PCM as binary WebSocket messages. |
| `SIPSink` | `speech_pipeline.SIPSink` | PCM as RTP packets into a SIP call. |
| `CLIWriter` | `speech_pipeline.CLIWriter` | NDJSON, text, or raw binary to stdout. |
| `FileRecorder` | `speech_pipeline.FileRecorder` | Records PCM to file (MP3/WAV/OGG) via ffmpeg. |
| `CodecSocketSink` | `speech_pipeline.CodecSocketSink` | Encodes PCM to Fourier codec frames. |

#### Utilities

| Component | Module | Description |
|-----------|--------|-------------|
| `NdjsonToText` | `speech_pipeline.NdjsonToText` | Iterator adapter: extracts `.text` from NDJSON bytes for STT->TTS transitions. |
| `PipelineBuilder` | `speech_pipeline.PipelineBuilder` | DSL parser and stage wiring. |
| `TTSRegistry` | `speech_pipeline.registry` | Voice discovery, caching and lazy loading. |
| `FileFetcher` | `speech_pipeline.FileFetcher` | Downloads HTTP(S) URLs or local files. Bearer auth. |
| `FreeVCService` | `speech_pipeline.vc_service` | Singleton FreeVC model manager. |
| `SIPSession` | `speech_pipeline.SIPSession` | pyVoIP lifecycle manager. |
| `CodecSocketSession` | `speech_pipeline.CodecSocketSession` | WebSocket session for Fourier codec. |
| `fourier_codec` | `speech_pipeline.fourier_codec` | FFT-based codec with multi-profile support. |

## CLI Reference

```bash
# Run a pipeline from a DSL string
speech-pipeline run "cli:text | tts:de_DE-thorsten-medium | cli:raw"

# Start the HTTP/WebSocket server
speech-pipeline serve --host 0.0.0.0 --port 5000 --voices-path voices-piper

# Start the SIP conference bridge
speech-pipeline sip-bridge -- --voice de_DE-thorsten-medium --lang de

# List available voices
speech-pipeline voices --voices-path voices-piper
```

## Pipeline DSL

Syntax: `element | element | ... | element`

Each element: `type:param1:param2`

| Type | Params | Stage |
|------|--------|-------|
| `cli:text` | -- | CLIReader (first) / CLIWriter text (last) |
| `cli:raw` | -- | CLIWriter binary (last) |
| `cli:ndjson` | -- | CLIWriter NDJSON (last) |
| `ws:pcm` | -- | WebSocketReader / WebSocketWriter |
| `ws:text` | -- | WebSocketReader.text_lines() / ws.send() |
| `ws:ndjson` | -- | ws.send(NDJSON line) |
| `resample` | FROM:TO | SampleRateConverter |
| `stt` | LANG or LANG:CHUNK:MODEL | WhisperTranscriber |
| `tts` | VOICE | StreamingTTSProducer |
| `sip` | TARGET | SIPSource / SIPSink |
| `vc` | VOICE2 | VCConverter |
| `pitch` | ST | PitchAdjuster |
| `record` | FILE or FILE:RATE | AudioTee + FileRecorder sidechain |
| `tee` | NAME | AudioTee feeding named mixer |
| `mix` | NAME or NAME:RATE | AudioMixer source |
| `gain` | FACTOR | GainStage (1.0 = unity) |
| `delay` | MS | DelayLine |
| `codec` | ID or ID:PROFILE | CodecSocketSource / CodecSocketSink |

### Example DSL Pipelines

```
STT:      ws:pcm | resample:48000:16000 | stt:de | ws:ndjson
TTS:      ws:text | tts:de_DE-thorsten-medium | ws:pcm
STS:      ws:pcm | resample:48000:16000 | stt:de | tts:de_DE-thorsten-medium | ws:pcm
CLI-TTS:  cli:text | tts:de_DE-thorsten-medium | cli:raw
SIP-TX:   ws:text | tts:de_DE-thorsten-medium | resample:22050:8000 | sip:100@pbx
SIP-RX:   sip:100@pbx | resample:8000:16000 | stt:de | ws:ndjson
```

## HTTP API

### `GET /healthz`
Liveness check. Returns `200 OK`.

### `GET /voices`
Returns JSON map of available voices with metadata.

### `POST /`
Synthesize speech. Parameters: `text`, `voice`, `voice2` (VC target), `sound` (audio source), `lang`, `speaker`/`speaker_id`, `length_scale`, `noise_scale`, `noise_w_scale`, `sentence_silence`, `pitch_st`, `pitch_factor`, `pitch_disable`.

### `POST /inputstream`
Streaming STT. Send raw PCM via request body, receive NDJSON transcription.

```bash
arecord -f S16_LE -r 16000 -c 1 -t raw -q - | \
  curl -sN -T - -H "Content-Type: application/octet-stream" \
  http://localhost:5000/inputstream
```

### `POST /tts/stream`
Streaming TTS. Send text lines via request body, receive streaming WAV audio.

```bash
echo "Hallo Welt." | curl -T - -H 'Content-Type: text/plain' \
  -o out.wav 'http://localhost:5000/tts/stream?voice=de_DE-thorsten-medium'
```

### `WS /ws/stt`
WebSocket STT. Binary PCM in, NDJSON text out.

### `WS /ws/tts`
WebSocket TTS. Text in, binary PCM out.

### `WS /ws/pipe`
Generic pipeline endpoint. Send JSON config (`{"pipe": "..."}` or `{"pipes": [...]}`), data flows according to the DSL.

### `WS /ws/socket/<id>`
Fourier codec bidirectional audio socket with profile handshake.

## Fourier Codec

Custom FFT-based audio codec for compressed real-time audio over WebSockets.

| Profile | Bins | Freq range | ~Bytes/frame | Use case |
|---------|------|------------|--------------|----------|
| `low` | 160 | 0-7.5 kHz | ~157 | Telephone, low bandwidth |
| `medium` | 256 | 0-12 kHz | ~410 | Good speech quality |
| `high` | 384 | 0-18 kHz | ~920 | Near-CD quality |
| `full` | 512 | 0-24 kHz | ~2060 | Lossless (within FFT) |

## SIP Bridge

Dial into Asterisk conferences as a bot with full-duplex STT/TTS.

```bash
speech-pipeline sip-bridge -- --voice de_DE-thorsten-medium --lang de
```

```
RX: SIPSource --> SampleRateConverter(8k->16k) --> WhisperTranscriber --> CLIWriter
TX: CLIReader --> StreamingTTSProducer --> SampleRateConverter(native->8k) --> SIPSink
```

SIP stages require `pyVoIP` (`pip install pyVoIP`).

## Voice Models

Voice models (`.onnx` files) are not included. Place them in `voices-piper/` or specify `--voices-path`.

```bash
mkdir -p voices-piper && cd voices-piper

# German - Thorsten (medium)
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json

# English - Amy (medium)
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
```

Browse all voices: https://github.com/rhasspy/piper/blob/master/VOICES.md

## Browser Demos

| Demo | File | Description |
|------|------|-------------|
| STT | `examples/stt.html` | Microphone -> WebSocket STT -> transcript display |
| STS | `examples/sts.html` | Microphone -> STT -> TTS -> speaker (robot voice) |
| Codec | `examples/codec-demo.html` | Mic -> Fourier codec -> WS -> server -> decode -> playback |

Open via `https://server/tts/examples/stt.html?api=https://server/tts`

## Apache Proxy

```apache
ProxyPass /tts/ws/pipe ws://localhost:5000/ws/pipe
ProxyPass /tts/ws/stt ws://localhost:5000/ws/stt
ProxyPass /tts/ws/tts ws://localhost:5000/ws/tts
ProxyPass /tts http://localhost:5000
```

## Requirements

- Python 3.10+
- espeak-ng (`sudo apt install espeak-ng`)
- ffmpeg with rubberband support (`sudo apt install ffmpeg`)
- [Piper](https://github.com/rhasspy/piper) Python bindings (install from source)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (optional, for STT)
- [pyVoIP](https://pypi.org/project/pyVoIP/) (optional, for SIP)

## License

MIT
