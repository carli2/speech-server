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

## Architecture

The server uses a pipeline of composable stages that process audio as a stream of PCM chunks. Each stage extends `Stage` (`lib/base.py`), implements `stream_pcm24k() -> Iterator[bytes]`, and connects via `.pipe()`:

```
Source ──► Processor ──► Processor ──► Sink
```

### Example pipelines

```
POST /              TTS:            TTSProducer ──► VCConverter ──► PitchAdjuster ──► ResponseWriter
POST /              Sound:          AudioReader ──► VCConverter ──► PitchAdjuster ──► ResponseWriter
POST /tts/stream    Streaming TTS:  text_lines(request.stream) ──► StreamingTTSProducer ──► ResponseWriter
POST /inputstream   STT (16kHz):    PCMInputReader ──► WhisperTranscriber ──► NDJSON
POST /inputstream   STT (48kHz):    PCMInputReader ──► SampleRateConverter(48k→16k) ──► WhisperTranscriber ──► NDJSON
WS   /ws/stt        STT (WebSocket): WebSocketReader ──► SampleRateConverter ──► WhisperTranscriber ──► ws.send(NDJSON)
WS   /ws/tts        TTS (WebSocket): WebSocketReader.text_lines() ──► StreamingTTSProducer ──► WebSocketWriter
                    SIP (planned):  SIPSource ──► SampleRateConverter ──► WhisperTranscriber ──► [LLM] ──► TTSProducer ──► SIPSink
```

### All Stages

#### Sources (produce PCM, no upstream)

| Stage | File | Status | Description |
|-------|------|--------|-------------|
| `TTSProducer` | `lib/TTSProducer.py` | vorhanden | Text (fester String) zu PCM via Piper ONNX. Streamt satzweise. |
| `StreamingTTSProducer` | `lib/StreamingTTSProducer.py` | vorhanden | Text (Iterable von Zeilen) zu PCM via Piper ONNX. Synthetisiert jede Zeile sobald sie ankommt — fuer `/tts/stream` und SIP. |
| `AudioReader` | `lib/AudioReader.py` | vorhanden | Liest Audio aus Datei/URL via ffmpeg, gibt PCM 24kHz s16le mono aus. Bearer-Auth fuer Remote-Dateien. |
| `PCMInputReader` | `lib/PCMInputReader.py` | vorhanden | Liest rohe PCM-Bytes aus einem Stream (z.B. HTTP Request Body, Mikrofon-Input). |
| `WebSocketReader` | `lib/WebSocketReader.py` | vorhanden | Liest binary oder text Messages aus einem flask-sock WebSocket. `stream_pcm24k()` fuer PCM-Bytes (STT), `text_lines()` fuer Text (TTS). |
| `SIPSource` | -- | **geplant** | Empfaengt RTP-Audio aus einem SIP-Call als PCM-Stream. Registriert sich als SIP-User-Agent via PJSIP oder baresip. |

#### Processors (transform PCM, have upstream)

| Stage | File | Status | Description |
|-------|------|--------|-------------|
| `VCConverter` | `lib/VCConverter.py` | vorhanden | Voice Conversion via FreeVC (Coqui TTS). Aendert Klangfarbe auf Zielstimme. Passthrough wenn FreeVC nicht installiert. |
| `PitchAdjuster` | `lib/PitchAdjuster.py` | vorhanden | Pitch-Shifting via ffmpeg rubberband (formant-erhaltend). Auto-Schaetzung aus Source/Target-F0 oder expliziter Semitone-Override. |
| `SampleRateConverter` | `lib/SampleRateConverter.py` | vorhanden | Resampling zwischen beliebigen Sample-Raten via ffmpeg (z.B. 48kHz Browser → 16kHz fuer Whisper). No-op wenn Raten gleich. |
| `NoiseGate` | -- | **geplant** | Entfernt Stille/Rauschen aus dem Stream, spart Whisper-Rechenzeit. |
| `AudioMixer` | -- | **geplant** | Mischt mehrere PCM-Streams (z.B. fuer SIP-Konferenzen). |

#### Sinks (consume PCM, produce output)

| Stage | File | Status | Description |
|-------|------|--------|-------------|
| `ResponseWriter` | `lib/ResponseWriter.py` | vorhanden | Streamt PCM als WAV-HTTP-Response mit korrekten Headern und Content-Length-Schaetzung. |
| `RawResponseWriter` | `lib/RawResponseWriter.py` | vorhanden | Roher Datei-Passthrough (kein Resampling, kein WAV-Header). |
| `WhisperTranscriber` | `lib/WhisperSTT.py` | vorhanden | Nimmt PCM-Stream entgegen, transkribiert in 3-Sekunden-Chunks via faster-whisper, gibt NDJSON-Segmente aus (`{text, start, end}`). Lazy Model-Loading, CPU/CUDA Auto-Detect mit Fallback. |
| `WebSocketWriter` | `lib/WebSocketWriter.py` | vorhanden | Liest PCM von upstream, sendet als binary WebSocket-Messages (chunked). Sendet `__END__` zum Abschluss. |
| `SIPSink` | -- | **geplant** | Sendet PCM-Audio als RTP-Pakete in einen SIP-Call. Gegenstueck zu SIPSource. |

#### Services & Utilities (keine Stages, aber von Stages genutzt)

| Component | File | Status | Description |
|-----------|------|--------|-------------|
| `WhisperSTT` | `lib/WhisperSTT.py` | vorhanden | Singleton-Wrapper um faster-whisper. Lazy Loading, CUDA/CPU Auto-Detect, Resampling auf 16kHz. Wird von WhisperTranscriber genutzt. |
| `FileFetcher` | `lib/FileFetcher.py` | vorhanden | Download von HTTP(S)-URLs oder lokalen Dateien. Bearer-Auth. Genutzt von AudioReader und VCConverter. |
| `FreeVCService` | `lib/vc_service.py` | vorhanden | Singleton FreeVC-Modell-Manager. CPU/CUDA Auto-Detect. Intern von VCConverter genutzt. |
| `TTSRegistry` | `lib/registry.py` | vorhanden | Voice-Discovery, Caching und Lazy Loading fuer Piper ONNX-Modelle. |

### Geplant: SIP-Integration

Ziel: Der Server kann sich in SIP-Calls einwaehlen und dort als Gespraechspartner agieren (Vollduplex).

```
Eingehend (hoeren):
  SIPSource ──► SampleRateConverter(8k→16k) ──► WhisperTranscriber ──► Text

Ausgehend (sprechen):
  Text ──► TTSProducer ──► SampleRateConverter(24k→8k) ──► SIPSink

Vollduplex-Call:
  ┌─ SIPSource ──► SampleRateConverter ──► WhisperTranscriber ──► [LLM/Logic] ─┐
  │                                                                             │
  └─ SIPSink   ◄── SampleRateConverter ◄── TTSProducer ◄───────────────────────┘
```

Geplante SIP-Stages:
- **`SIPSource`** -- RTP-Empfang aus SIP-Call, liefert PCM-Stream (typisch 8kHz G.711 oder 16kHz). Registrierung als SIP-User-Agent via PJSIP oder baresip.
- **`SIPSink`** -- PCM-Stream als RTP in den SIP-Call senden. Encoding in G.711/Opus.
- **`SIPSession`** -- Orchestriert Source+Sink fuer einen Call: Dial, Hangup, DTMF, Hold/Resume. Verwaltet SIP-Registrierung und Call-State.
- **`AudioMixer`** -- Mischt mehrere PCM-Streams fuer Konferenz-Szenarien.

### Geplant: Weitere Stages

- **`NoiseGate`** -- Filtert Stille und Hintergrundrauschen vor der Transkription, reduziert Whisper-Last.
- **`VADSplitter`** -- Voice Activity Detection als Stage: splittet den Stream in Sprach-Segmente, leitet nur aktive Teile weiter.
- **`LLMBridge`** -- Nimmt transkribierten Text, sendet ihn an ein LLM (lokal oder API), gibt Antwort-Text an TTSProducer weiter. Ermoeglicht autonome Gespraeche.

## Requirements

- Python 3.10+
- espeak-ng (`sudo apt install espeak-ng`)
- ffmpeg with rubberband support (`sudo apt install ffmpeg`)
- [Piper](https://github.com/rhasspy/piper) Python bindings (install from source)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (optional, for STT)

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
- `X-Sample-Rate` - Input sample rate in Hz (default: `16000`). If not 16000, a `SampleRateConverter` stage automatically resamples via ffmpeg.

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
printf "Erster Satz.\nZweiter Satz.\nDritter Satz.\n" | \
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
2. Server sends a JSON text message: `{"sample_rate": 24000}`
3. Client sends text frames (one sentence per message)
4. Server sends binary frames (PCM s16le mono at the declared sample rate)
5. Client sends `__END__` text message when done
6. Server sends `__END__` text message after all audio is sent

Pipeline: `WebSocketReader.text_lines() -> StreamingTTSProducer -> WebSocketWriter`

### Apache Proxy

WebSocket endpoints require `mod_proxy_wstunnel`. Add WS rules **before** the HTTP rule:

```apache
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
controller.enqueue(new TextEncoder().encode("Erster Satz.\n"));
controller.enqueue(new TextEncoder().encode("Zweiter Satz.\n"));

// Signal end of input:
controller.close();
```

## Browser Demos

| Demo | Datei | Beschreibung |
|------|-------|-------------|
| STT | `examples/stt.html` | Mikrofon → WebSocket STT → Transkript-Anzeige |
| STS | `examples/sts.html` | Mikrofon → STT → TTS → Lautsprecher (Roboterstimme) |

Oeffnen via `https://server/tts/examples/stt.html?api=https://server/tts`

## License

MIT
