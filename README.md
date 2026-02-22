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

### `POST /tts/stream`
Streaming TTS. Send text line-by-line in the request body; receive WAV audio as a streaming response.

## License

MIT
