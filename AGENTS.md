# Richtlinien fuer Implementierer

## Pflicht: Stage-Interface zuerst lesen

Bevor du irgendetwas implementierst, lies `speech_pipeline/base.py`. Das ist die Basisklasse `Stage`, auf der die gesamte Architektur aufbaut. Verstehe die Methoden `stream_pcm24k()`, `pipe()`, `cancel()` und `set_upstream()` vollstaendig, bevor du Code schreibst.

## Package-Struktur

Der gesamte Bibliothekscode liegt in `speech_pipeline/` (pip-Paketname: `speech-pipeline`, import: `speech_pipeline`). Das Verzeichnis `lib/` existiert nur als Kompatibilitaets-Shim, damit `piper_multi_server.py` und `sip_bridge.py` weiterhin `from lib.X import Y` verwenden koennen.

**Neuen Code immer in `speech_pipeline/` anlegen, niemals in `lib/`.**

## Alle Audio- und Text-Operationen sind Stages

Jede Operation, die Audio erzeugt, transformiert, konsumiert oder Text aus Audio gewinnt, MUSS als `Stage`-Subklasse implementiert werden. Keine Ausnahmen. Kein "schnell mal inline in den Endpoint schreiben".

### Rollen

| Rolle | upstream | stream_pcm24k() liefert | Beispiele |
|-------|----------|-------------------------|-----------|
| **Source** | keinen | PCM-Bytes | `TTSProducer`, `AudioReader`, `PCMInputReader` |
| **Processor** | liest von upstream | transformierte PCM-Bytes | `VCConverter`, `PitchAdjuster`, `SampleRateConverter` |
| **Sink** | liest von upstream | Ausgabe-Bytes (WAV, NDJSON, ...) | `ResponseWriter`, `WhisperTranscriber` |

### Regeln

1. **Erbe von `Stage`** (`from .base import Stage`)
2. **Implementiere `stream_pcm24k(self) -> Iterator[bytes]`** als Generator
3. **Sources** haben kein `self.upstream` — sie erzeugen PCM aus einer externen Quelle
4. **Processors und Sinks** lesen via `self.upstream.stream_pcm24k()` und yielden transformierte Daten
5. **Pruefe `self.cancelled`** in jeder Schleife — brich ab, wenn True
6. **Verkettung** erfolgt ausschliesslich ueber `.pipe()`:
   ```python
   source.pipe(processor).pipe(sink)
   ```
7. **PCM-Format**: s16le mono. Sample-Rate je nach Kontext (24kHz fuer TTS, 16kHz fuer Whisper). Bei Raten-Unterschieden `SampleRateConverter` einsetzen.
8. **Byte-Alignment**: s16le = 2 Bytes pro Sample. Yielde niemals eine ungerade Byte-Anzahl.
9. **Singleton-Modelle** (Whisper, FreeVC, Piper) werden auf Modul-Ebene lazy geladen, nicht in der Stage selbst.
10. **Exportiere** jede neue Stage in `speech_pipeline/__init__.py`.

### Keine Sonderwege

- Kein direktes `subprocess.Popen` im Endpoint — mach eine Stage draus
- Kein Resampling im Browser-JS — nutze `SampleRateConverter`
- Kein manuelles Byte-Shuffling im Endpoint — die Pipeline macht das
- Kein `request.stream.read()` im Endpoint — nutze `PCMInputReader`

Der Endpoint baut nur die Pipeline zusammen und iteriert ueber die letzte Stage:

```python
source = PCMInputReader(request.stream)
pipeline = source.pipe(SampleRateConverter(48000, 16000)).pipe(WhisperTranscriber())
for chunk in pipeline.stream_pcm24k():
    yield chunk
```
