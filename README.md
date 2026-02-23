# Speech Server

Multi-voice TTS/STT HTTP server with realtime streaming, voice conversion, and pitch adjustment.

## Features

- **Multi-voice TTS** via [Piper](https://github.com/rhasspy/piper) ONNX voices with automatic discovery
- **Realtime streaming** pipeline architecture (audio streams as it is synthesized)
- **Voice conversion** via FreeVC (Coqui TTS) to transform timbre to a target voice
- **Pitch adjustment** via ffmpeg rubberband (formant-preserving)
- **CORS enabled** for browser usage
- **Speech-to-text** via [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with streaming NDJSON output
- **Streaming TTS** (`POST /tts/stream`) — text in via request body, audio out as streaming WAV, single HTTP connection
- **Streaming STT** (`POST /inputstream`) — PCM audio in via request body, NDJSON transcription out, single HTTP connection
- **WebSocket STT** (`/ws/stt`) — PCM audio in via WebSocket binary frames, NDJSON text out. Works through Apache `mod_proxy_wstunnel` and in all browsers (Chrome, Firefox, Safari)
- **WebSocket TTS** (`/ws/tts`) — text in via WebSocket text frames, PCM audio out as binary frames. Works through Apache `mod_proxy_wstunnel` and in all browsers
- **SIP bridge** — dial into Asterisk conferences as a bot with full-duplex STT/TTS

## Architecture

The server uses a pipeline of composable stages that process audio as a stream of PCM chunks. Each stage extends `Stage` (`lib/base.py`), implements `stream_pcm24k() -> Iterator[bytes]`, and connects via `.pipe()`:

```
Source ──► Processor ──► Processor ──► Sink
```

Format conversion between stages is automatic: `.pipe()` inserts `SampleRateConverter` and `EncodingConverter` stages when the output format of one stage doesn't match the input format of the next.

### Example pipelines

```
POST /              TTS:            TTSProducer ──► VCConverter ──► PitchAdjuster ──► ResponseWriter
POST /              Sound:          AudioReader ──► VCConverter ──► PitchAdjuster ──► ResponseWriter
POST /tts/stream    Streaming TTS:  text_lines(request.stream) ──► StreamingTTSProducer ──► ResponseWriter
POST /inputstream   STT (16kHz):    PCMInputReader ──► WhisperTranscriber ──► NDJSON
POST /inputstream   STT (48kHz):    PCMInputReader ──► SampleRateConverter(48k→16k) ──► WhisperTranscriber ──► NDJSON
WS   /ws/stt        STT (WebSocket): WebSocketReader ──► SampleRateConverter ──► WhisperTranscriber ──► ws.send(NDJSON)
WS   /ws/tts        TTS (WebSocket): WebSocketReader.text_lines() ──► StreamingTTSProducer ──► WebSocketWriter
WS   /ws/pipe       Generic:        DSL-defined pipeline (e.g. ws:pcm | resample:48000:16000 | stt:de | ws:ndjson)
                    SIP-Bridge:     sip:100@pbx | resample:8000:16000 | stt:de | ws:ndjson
```

### All Stages

#### Sources (produce PCM, no upstream)

| Stage | File | Status | Description |
|-------|------|--------|-------------|
| `TTSProducer` | `lib/TTSProducer.py` | done | Fixed text string to PCM via Piper ONNX. Streams sentence by sentence at native sample rate. |
| `StreamingTTSProducer` | `lib/StreamingTTSProducer.py` | done | Text iterable (lines) to PCM via Piper ONNX. Synthesizes each line as it arrives — for `/tts/stream` and SIP. Outputs at native sample rate. |
| `AudioReader` | `lib/AudioReader.py` | done | Reads audio from file/URL via ffmpeg, outputs PCM 24kHz s16le mono. Bearer auth for remote files. |
| `PCMInputReader` | `lib/PCMInputReader.py` | done | Reads raw PCM bytes from a stream (e.g. HTTP request body, microphone input). |
| `WebSocketReader` | `lib/WebSocketReader.py` | done | Reads binary or text messages from a flask-sock WebSocket. `stream_pcm24k()` for PCM bytes (STT), `text_lines()` for text (TTS). |
| `SIPSource` | `lib/SIPSource.py` | done | Receives RTP audio from a SIP call as PCM stream via pyVoIP. |
| `CLIReader` | `lib/CLIReader.py` | done | Reads text lines from stdin. For CLI tools (e.g. `sip_bridge.py`). |
| `QueueSource` | `lib/QueueSource.py` | done | Reads PCM from a `queue.Queue`. Bridge between AudioTee/AudioMixer and their consumers. `None` = EOF sentinel. |
| `AudioMixer` | `lib/AudioMixer.py` | done | Mixes N input queues into one output using `audioop.add()` on fixed-size frames. Used as source for mixed recordings. |

#### Processors (transform PCM, have upstream)

| Stage | File | Status | Description |
|-------|------|--------|-------------|
| `VCConverter` | `lib/VCConverter.py` | done | Voice conversion via FreeVC (Coqui TTS). Changes timbre to target voice. Passthrough if FreeVC not installed. |
| `PitchAdjuster` | `lib/PitchAdjuster.py` | done | Pitch shifting via ffmpeg rubberband (formant-preserving). Auto-estimation from source/target F0 or explicit semitone override. |
| `SampleRateConverter` | `lib/SampleRateConverter.py` | done | Resampling between arbitrary sample rates via audioop (zero-latency, no subprocess). No-op when rates match. |
| `EncodingConverter` | `lib/EncodingConverter.py` | done | Converts between audio encodings (s16le ↔ u8). Auto-inserted by `pipe()`. |
| `AudioTee` | `lib/AudioTee.py` | done | Pass-through that copies data to side-chain sinks via queues in background threads. Used for recording while streaming. |
| `NoiseGate` | -- | **planned** | Removes silence/noise from the stream, reduces Whisper compute. |

#### Sinks (consume PCM, produce output)

| Stage | File | Status | Description |
|-------|------|--------|-------------|
| `ResponseWriter` | `lib/ResponseWriter.py` | done | Streams PCM as WAV HTTP response with correct headers and Content-Length estimation. Derives sample rate from upstream. |
| `RawResponseWriter` | `lib/RawResponseWriter.py` | done | Raw file passthrough (no resampling, no WAV header). |
| `WhisperTranscriber` | `lib/WhisperSTT.py` | done | Consumes PCM stream, transcribes in 3-second chunks via faster-whisper, outputs NDJSON segments (`{text, start, end}`). Lazy model loading, CPU/CUDA auto-detect with fallback. |
| `WebSocketWriter` | `lib/WebSocketWriter.py` | done | Reads PCM from upstream, sends as binary WebSocket messages (chunked). Sends `__END__` on completion. |
| `SIPSink` | `lib/SIPSink.py` | done | Writes PCM audio as RTP packets into a SIP call via pyVoIP. Counterpart to SIPSource. |
| `CLIWriter` | `lib/CLIWriter.py` | done | Writes NDJSON lines or text to stdout. For CLI tools (e.g. `sip_bridge.py`). |
| `FileRecorder` | `lib/FileRecorder.py` | done | Records PCM to file (MP3/WAV/OGG/etc.) by streaming to ffmpeg stdin. No temp files. Terminal sink. |

#### Adapters (not stages, but used by the pipeline)

| Component | File | Status | Description |
|-----------|------|--------|-------------|
| `NdjsonToText` | `lib/NdjsonToText.py` | done | Iterator adapter: extracts `.text` from NDJSON bytes (e.g. WhisperTranscriber output) for STT→TTS transitions. |
| `PipelineBuilder` | `lib/PipelineBuilder.py` | done | DSL parser and stage wiring for `/ws/pipe`. Builds pipelines from text descriptions. |

#### Services & Utilities (not stages, but used by stages)

| Component | File | Status | Description |
|-----------|------|--------|-------------|
| `WhisperSTT` | `lib/WhisperSTT.py` | done | Singleton wrapper around faster-whisper. Lazy loading, CUDA/CPU auto-detect, resampling to 16kHz. Used by WhisperTranscriber. |
| `FileFetcher` | `lib/FileFetcher.py` | done | Downloads HTTP(S) URLs or local files. Bearer auth. Used by AudioReader and VCConverter. |
| `FreeVCService` | `lib/vc_service.py` | done | Singleton FreeVC model manager. CPU/CUDA auto-detect. Used internally by VCConverter. |
| `TTSRegistry` | `lib/registry.py` | done | Voice discovery, caching and lazy loading for Piper ONNX models. |
| `SIPSession` | `lib/SIPSession.py` | done | pyVoIP lifecycle manager: registers SIP account, dials call, provides RX/TX audio queues. Optional (requires pyVoIP). |

### SIP Integration

The server can dial into SIP calls and act as a conversation partner (full-duplex). SIP stages require `pyVoIP` (`pip install pyVoIP`).

```
Incoming (listen):
  SIPSource ──► SampleRateConverter(8k→16k) ──► WhisperTranscriber ──► Text

Outgoing (speak):
  Text ──► StreamingTTSProducer ──► SampleRateConverter(native→8k) ──► EncodingConverter(s16le→u8) ──► SIPSink

Full-duplex call (via /ws/pipe with two pipes):
  ┌─ SIPSource ──► SampleRateConverter ──► WhisperTranscriber ──► ws:ndjson ─┐
  │                                                                           │
  └─ SIPSink   ◄── SampleRateConverter ◄── StreamingTTSProducer ◄── ws:text ─┘
```

SIP stages:
- **`SIPSession`** (`lib/SIPSession.py`) -- pyVoIP lifecycle: SIP registration, call setup, RX/TX audio queues. Managed by PipelineBuilder.
- **`SIPSource`** (`lib/SIPSource.py`) -- Source stage: reads PCM s16le mono @ 8kHz from SIP call via rx_queue.
- **`SIPSink`** (`lib/SIPSink.py`) -- Sink stage: writes PCM into SIP call via tx_queue. Hangs up when the pipeline ends.

### Planned Stages

- **`NoiseGate`** -- Filters silence and background noise before transcription, reduces Whisper load.
- **`VADSplitter`** -- Voice Activity Detection as a stage: splits the stream into speech segments, forwards only active parts.
- **`LLMBridge`** -- Takes transcribed text, sends it to an LLM (local or API), feeds the response text to TTSProducer. Enables autonomous conversations.

## Requirements

- Python 3.10+
- espeak-ng (`sudo apt install espeak-ng`)
- ffmpeg with rubberband support (`sudo apt install ffmpeg`)
- [Piper](https://github.com/rhasspy/piper) Python bindings (install from source)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (optional, for STT)
- [pyVoIP](https://pypi.org/project/pyVoIP/) (optional, for SIP)

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e /path/to/piper  # Piper from source
```

## Voice Models

Voice models (`.onnx` files) are not included in this repository. Place them in the `voices-piper/` directory (or specify a custom path with `--voices-path`).

### Download from Piper releases

Browse available voices at https://github.com/rhasspy/piper/blob/master/VOICES.md

```bash
# Example: German voice (Thorsten, medium quality)
mkdir -p voices-piper
cd voices-piper
wget https://github.com/rhasspy/piper/releases/download/2023.11.14-2/voice-de_DE-thorsten-medium.tar.gz
tar xzf voice-de_DE-thorsten-medium.tar.gz
# This extracts de_DE-thorsten-medium.onnx and de_DE-thorsten-medium.onnx.json

# Example: English voice (Amy, medium quality)
wget https://github.com/rhasspy/piper/releases/download/2023.11.14-2/voice-en_US-amy-medium.tar.gz
tar xzf voice-en_US-amy-medium.tar.gz
```

### Direct download via URL

Each voice consists of two files: the ONNX model and its JSON config.

```bash
cd voices-piper

# German - Thorsten (medium)
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json

# English - Amy (medium)
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
```

Voice files follow the naming convention `{lang}_{REGION}-{name}-{quality}.onnx` where quality is one of `x_low`, `low`, `medium`, or `high`. Higher quality models are larger and slower but sound better.

The server auto-discovers all `.onnx` files in the voices directory on startup. Use `GET /voices` to list loaded voices.

## Usage

```bash
# Start with default voice directory
python3 piper_multi_server.py --host 0.0.0.0 --port 5000

# Start with explicit voice path and GPU
python3 piper_multi_server.py --voices-path ./voices-piper --cuda

# With remote voice file support
python3 piper_multi_server.py --soundpath "https://example.com/voices/%s.wav" --bearer TOKEN
```

## API Endpoints

### `GET /healthz`
Liveness check. Returns `200 OK`.

### `GET /voices`
Returns JSON map of available voices with metadata (espeak voice, sample rate, speakers).

### `POST /`
Synthesize speech. Accepts JSON or form data.

Parameters:
- `text` - Text to synthesize
- `voice` - Voice model ID (default: `de_DE-thorsten-medium`)
- `voice2` - Target voice ID for voice conversion
- `sound` - Source audio ID (plays a sound file, optionally through VC)
- `lang` - Language code for automatic voice selection
- `speaker` / `speaker_id` - Speaker selection for multi-speaker models
- `length_scale` - Speech speed (1.0 = normal)
- `noise_scale` - Phoneme noise
- `noise_w_scale` - Phoneme width noise
- `sentence_silence` - Silence between sentences (seconds)
- `pitch_st` - Pitch shift in semitones
- `pitch_factor` - Pitch multiplier (alternative to `pitch_st`)
- `pitch_disable` - Disable pitch processing (`1`/`true`)

### `POST /inputstream`
Streaming speech-to-text on a single HTTP connection. Raw PCM audio goes in via the request body, recognized text comes back as NDJSON.

Headers:
- `X-Sample-Rate` - Input sample rate in Hz (default: `16000`). If not 16000, a `SampleRateConverter` stage automatically resamples.

Pipeline: `PCMInputReader(request.stream) -> [SampleRateConverter] -> WhisperTranscriber -> NDJSON`

Each output line is a JSON object:
```json
{"text": "hallo welt", "start": 0.0, "end": 1.5}
```

#### Examples

```bash
# Live microphone (16kHz, default)
arecord -f S16_LE -r 16000 -c 1 -t raw -q - | \
  curl -sN -T - -H "Content-Type: application/octet-stream" \
  http://localhost:5000/inputstream

# Live microphone (48kHz, browser-compatible rate)
arecord -f S16_LE -r 48000 -c 1 -t raw -q - | \
  curl -sN -T - -H "Content-Type: application/octet-stream" \
  -H "X-Sample-Rate: 48000" \
  http://localhost:5000/inputstream

# Audio file via ffmpeg
ffmpeg -i test.wav -f s16le -ac 1 -ar 16000 - | \
  curl -sN -T - -H "Content-Type: application/octet-stream" \
  http://localhost:5000/inputstream
```

Note: Use `curl -T -` (not `--data-binary @-`) to stream stdin in real time. `--data-binary` buffers the entire input before sending.

Use `--whisper-model` to select the model size (default: `base`). Available: `tiny`, `base`, `small`, `medium`, `large-v3`.

### `POST /tts/stream`
Streaming TTS on a single HTTP connection. Text goes in via the request body, audio comes back as a streaming WAV response. The connection stays open — each line of text is synthesized and streamed as audio as soon as it arrives.

Query parameters (all optional):
- `voice` - Voice model ID (default: server default)
- `length_scale` - Speech speed (1.0 = normal)
- `noise_scale` - Phoneme noise
- `noise_w_scale` - Phoneme width noise

The request body is plain text (`Content-Type: text/plain`), newline-delimited. Each line is treated as a sentence and synthesized independently. The response is a streaming `audio/wav` (PCM16, mono) with a provisional WAV header.

Pipeline: `text_lines(request.stream) -> voice.synthesize() -> streaming WAV response`

#### Examples

```bash
# Simple: synthesize a single sentence
echo "Hallo Welt." | curl -X POST -H 'Content-Type: text/plain' \
  --data-binary @- -o out.wav 'http://localhost:5000/tts/stream?voice=de_DE-thorsten-medium'

# Streaming from a process (e.g. LLM output) — audio starts before input is complete
my_llm_command | curl -T - -H 'Content-Type: text/plain' \
  -o out.wav 'http://localhost:5000/tts/stream?voice=de_DE-thorsten-medium'

# Multiple sentences, streamed line by line
printf "First sentence.\nSecond sentence.\nThird sentence.\n" | \
  curl -T - -H 'Content-Type: text/plain' \
  -o out.wav 'http://localhost:5000/tts/stream'

# Pipe from an LLM and play in real time (requires sox)
my_llm_command | curl -sN -T - -H 'Content-Type: text/plain' \
  'http://localhost:5000/tts/stream?voice=de_DE-thorsten-medium' | \
  play -t wav -
```

Note: Use `curl -T -` (not `--data-binary @-`) to stream stdin in real time. `--data-binary` buffers the entire input before sending.

### `WS /ws/stt`
WebSocket speech-to-text. Replaces `POST /inputstream` for browser use — works through Apache `mod_proxy_wstunnel` and in all browsers (Chrome, Firefox, Safari).

Protocol:
1. Client connects to `ws://host:port/ws/stt`
2. Client sends a JSON text message: `{"sampleRate": 48000, "language": "de", "chunkSeconds": 2.0}` (only `sampleRate` required; `language` avoids per-chunk auto-detection, `chunkSeconds` controls transcription latency, default 2.0)
3. Client sends binary frames (PCM s16le mono at the declared sample rate)
4. Server sends text frames (one NDJSON line per segment: `{"text": "...", "start": 0.0, "end": 1.5}`)
5. Client sends `__END__` text message when done
6. Server sends `__END__` text message after final results

Pipeline: `WebSocketReader -> SampleRateConverter -> WhisperTranscriber -> ws.send(NDJSON)`

### `WS /ws/tts`
WebSocket text-to-speech. Replaces `POST /tts/stream` for browser use.

Query parameters: `?voice=de_DE-thorsten-medium` (optional)

Protocol:
1. Client connects to `ws://host:port/ws/tts?voice=...`
2. Server sends a JSON text message: `{"sample_rate": 22050}` (actual voice sample rate)
3. Client sends text frames (one sentence per message)
4. Server sends binary frames (PCM s16le mono at the declared sample rate)
5. Client sends `__END__` text message when done
6. Server sends `__END__` text message after all audio is sent

Pipeline: `WebSocketReader.text_lines() -> StreamingTTSProducer -> WebSocketWriter`

### `WS /ws/pipe`
Generic pipeline endpoint. Instead of hardcoded STT/TTS WebSocket endpoints, `/ws/pipe` accepts a pipeline description as DSL and wires up the stages dynamically.

Protocol:
1. Client connects to `ws://host:port/ws/pipe`
2. Client sends a JSON text message with the pipeline config
3. Data flows according to the pipeline definition
4. Server sends `__END__` when done (for ws sinks)

#### Pipeline DSL

Syntax: `element | element | ... | element`

Each element: `type:param1:param2`

| Type | Params | I/O | Stage |
|------|--------|-----|-------|
| `ws:pcm` | -- | PCM via WS binary | WebSocketReader / WebSocketWriter |
| `ws:text` | -- | text via WS text | WebSocketReader.text_lines() / ws.send() |
| `ws:ndjson` | -- | NDJSON via WS text | ws.send(line) |
| `resample` | FROM:TO | PCM -> PCM | SampleRateConverter |
| `stt` | LANG | PCM -> NDJSON | WhisperTranscriber |
| `tts` | VOICE | text -> PCM | StreamingTTSProducer |
| `sip` | TARGET | PCM via RTP | SIPSource / SIPSink |
| `vc` | VOICE2 | PCM -> PCM | VCConverter |
| `pitch` | ST | PCM -> PCM | PitchAdjuster |
| `record` | FILE or FILE:RATE | PCM -> PCM (pass-through) | AudioTee + FileRecorder sidechain |
| `tee` | NAME | PCM -> PCM (pass-through) | AudioTee feeding named mixer |
| `mix` | NAME or NAME:RATE | -- -> PCM (source only) | AudioMixer |

Type transitions (automatically inserted):
- `stt -> tts`: NdjsonToText adapter extracts `.text` from NDJSON
- `stt -> ws:ndjson`: NDJSON bytes sent as WS text frames
- `stt -> ws:text`: NdjsonToText -> ws.send(text)
- `ws:text -> tts`: text lines directly to StreamingTTSProducer

#### Single pipe config

```json
{"pipe": "ws:pcm | resample:48000:16000 | stt:de | ws:ndjson"}
```

#### Multi pipe config (duplex)

```json
{"pipes": [
  "sip:100@pbx | resample:8000:16000 | stt:de | ws:ndjson",
  "ws:text | tts:de_DE-thorsten-medium | resample:22050:8000 | sip:100@pbx"
]}
```

SIP sessions with the same target are shared across pipes (same call, bidirectional).

#### Example pipes

```
STT:      ws:pcm | resample:48000:16000 | stt:de | ws:ndjson
TTS:      ws:text | tts:de_DE-thorsten-medium | ws:pcm
STS:      ws:pcm | resample:48000:16000 | stt:de | tts:de_DE-thorsten-medium | ws:pcm
SIP-TX:   ws:text | tts:de_DE-thorsten-medium | resample:22050:8000 | sip:100@pbx
SIP-RX:   sip:100@pbx | resample:8000:16000 | stt:de | ws:ndjson
TTS+REC:  ws:text | tts:de_DE-thorsten-medium | record:output.mp3:22050 | ws:pcm
```

#### Recording and mixing

`record` inserts an AudioTee that copies all audio to a FileRecorder sidechain. The main pipeline continues unchanged.

```
# Record SIP inbound to MP3 while transcribing:
sip:100@pbx | record:incoming.mp3:8000 | resample:8000:16000 | stt:de | ws:ndjson
```

`tee` + `mix` enable mixed recordings of multiple streams (e.g. both sides of a SIP call):

```json
{"pipes": [
  "sip:100@pbx | tee:call | resample:8000:16000 | stt:de | ws:ndjson",
  "ws:text | tts:de_DE-thorsten-medium | resample:22050:8000 | tee:call | sip:100@pbx",
  "mix:call:8000 | record:call.mp3"
]}
```

Each `tee:call` feeds a queue into the `mix:call` mixer. The mixer combines all inputs using `audioop.add()` and the result is recorded to file. If tee and mixer sample rates differ, a SampleRateConverter is auto-inserted.

#### SIP Stages

| Stage | File | Description |
|-------|------|-------------|
| `SIPSession` | `lib/SIPSession.py` | pyVoIP lifecycle: SIP registration, call setup, MediaPort with RX/TX queues |
| `SIPSource` | `lib/SIPSource.py` | Source stage: reads PCM from SIP call via rx_queue |
| `SIPSink` | `lib/SIPSink.py` | Sink stage: writes PCM into SIP call via tx_queue |

SIP stages require `pyVoIP` (`pip install pyVoIP`). All other stages work without it.

## SIP Bridge CLI

`sip_bridge.py` is a standalone CLI tool that registers as a SIP client and dials into an Asterisk conference. It runs two pipelines in full-duplex:

- **RX** (listen): SIPSource → SampleRateConverter(8k→16k) → WhisperTranscriber → CLIWriter (NDJSON on stdout)
- **TX** (speak): CLIReader (stdin) → StreamingTTSProducer → SampleRateConverter(native→8k) → EncodingConverter(s16le→u8) → SIPSink

Format conversion (encoding, sample rate) is handled automatically by `pipe()`.

```bash
# Setup Asterisk (once):
sudo bash asterisk/setup.sh

# Run the bridge:
python3 sip_bridge.py --voice de_DE-thorsten-medium --lang de

# Options:
python3 sip_bridge.py \
  --sip-server 127.0.0.1 --sip-port 5060 \
  --sip-user piper --sip-password piper123 \
  --extension 800 \
  --voice de_DE-thorsten-medium \
  --language de \
  --whisper-model base \
  --cuda --debug
```

Call extension 800 with a softphone to join the conference. Type text + Enter to speak into the conference. Transcriptions appear as NDJSON on stdout.

### Apache Proxy

WebSocket endpoints require `mod_proxy_wstunnel`. Add WS rules **before** the HTTP rule:

```apache
ProxyPass /tts/ws/pipe ws://localhost:5000/ws/pipe
ProxyPass /tts/ws/stt ws://localhost:5000/ws/stt
ProxyPass /tts/ws/tts ws://localhost:5000/ws/tts
ProxyPass /tts http://localhost:5000
```

#### Browser usage

Use `fetch()` with `duplex: 'half'` and a `ReadableStream` body to send text incrementally while receiving audio on the same connection:

```js
var controller;
var bodyStream = new ReadableStream({
  start: function(c) { controller = c; }
});

fetch('/tts/stream?voice=de_DE-thorsten-medium', {
  method: 'POST',
  headers: {'Content-Type': 'text/plain'},
  duplex: 'half',
  body: bodyStream
}).then(function(response) {
  // response.body is a ReadableStream of WAV audio (44-byte header + PCM16)
  var reader = response.body.getReader();
  // ... parse WAV header, decode PCM, schedule via Web Audio API
});

// Push text as it becomes available:
controller.enqueue(new TextEncoder().encode("First sentence.\n"));
controller.enqueue(new TextEncoder().encode("Second sentence.\n"));

// Signal end of input:
controller.close();
```

## Browser Demos

| Demo | File | Description |
|------|------|-------------|
| STT | `examples/stt.html` | Microphone → WebSocket STT → transcript display |
| STS | `examples/sts.html` | Microphone → STT → TTS → speaker (robot voice) |

Open via `https://server/tts/examples/stt.html?api=https://server/tts`

## License

MIT
