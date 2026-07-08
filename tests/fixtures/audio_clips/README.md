# Audio Clips — Test Fixtures

Small WAV audio fixtures for VAD, preprocessing, and STT unit tests.

## Generating Clips

Use `tests/fixtures/audio.py::make_wav_bytes` to create WAV bytes in tests:

```python
from tests.fixtures.audio import make_wav_bytes

silence_wav = make_wav_bytes(sample_rate=8000, num_samples=160, silence=True)
speech_wav  = make_wav_bytes(sample_rate=8000, num_samples=160, silence=False)
```

## Clip Patterns

| Pattern    | Description                                | Use case               |
|------------|--------------------------------------------|------------------------|
| silence    | 8 kHz mono 8-bit PCM, 160 samples silence  | VAD silence detection  |
| speech     | 8 kHz mono 8-bit PCM, 440 Hz sine wave     | VAD speech detection   |
| barge-in   | Speech → silence → speech (composite)      | Barge-in endpointing   |

## Architecture Reference

V1 Ch3 (Media Gateway), V1 Ch5 (Preprocessing), V1 Ch6 (VAD & Endpointing).

Generated clips use 8 kHz / 20 ms / mono / 8-bit unsigned PCM (same as
telephony PCMU after decode). The FakeRTPStream in `audio.py` wraps these
in proper AudioFrame envelopes with RTP sequencing.
