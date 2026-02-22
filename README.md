# Speech Server

Multi-voice TTS/STT HTTP server with realtime streaming, voice conversion, and pitch adjustment.

## Features

- **Multi-voice TTS** via [Piper](https://github.com/rhasspy/piper) ONNX voices with automatic discovery
- **Realtime streaming** pipeline architecture (audio streams as it is synthesized)
- **Voice conversion** via FreeVC (Coqui TTS) to transform timbre to a target voice
- **Pitch adjustment** via ffmpeg rubberband (formant-preserving)
- **CORS enabled** for browser usage
- **Streaming endpoint** (`/tts/stream`) for text-in, audio-out on a single connection

## Architecture

The server uses a pipeline of composable stages that process audio as a stream of PCM chunks:

```
Source              Processors                    Sink
──────              ──────────                    ────
TTSProducer    ──►  VCConverter  ──►  PitchAdjuster  ──►  ResponseWriter
AudioReader                                               RawResponseWriter
```

Each stage implements `stream_pcm24k() -> Iterator[bytes]` and connects via `.pipe()`.

### Existing Stages

| Stage | Type | Description |
|-------|------|-------------|
| `TTSProducer` | Source | Text to PCM via Piper |
| `AudioReader` | Source | File/URL to PCM via ffmpeg |
| `VCConverter` | Processor | Voice conversion via FreeVC |
| `PitchAdjuster` | Processor | Pitch shifting via ffmpeg rubberband |
| `ResponseWriter` | Sink | PCM to WAV HTTP streaming response |
| `RawResponseWriter` | Sink | Raw file passthrough |
| `FileFetcher` | Utility | HTTP/file download abstraction |

## Requirements

- Python 3.10+
- espeak-ng (`sudo apt install espeak-ng`)
- ffmpeg with rubberband support (`sudo apt install ffmpeg`)
- [Piper](https://github.com/rhasspy/piper) Python bindings (install from source)

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e /path/to/piper  # Piper from source
```

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

### `POST /tts/stream`
Streaming TTS. Send text line-by-line in the request body; receive WAV audio as a streaming response.

## License

MIT
