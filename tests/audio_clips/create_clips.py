"""Generate test WAV audio clips for Sprint-007 VAD tests."""

import math
import os
import struct
import wave

SAMPLE_RATE = 16000
DURATION_S = 1
N_SAMPLES = SAMPLE_RATE * DURATION_S


def write_wav(path: str, samples: list[int]) -> None:
    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(struct.pack(f"{len(samples)}h", *samples))
    print(f"  wrote {path}  ({os.path.getsize(path)} bytes)")


os.makedirs(os.path.dirname(os.path.abspath(__file__)), exist_ok=True)
base = os.path.dirname(os.path.abspath(__file__))

speech = [int(10_000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)) for i in range(N_SAMPLES)]
write_wav(os.path.join(base, "speech_sample.wav"), speech)

silence = [0] * N_SAMPLES
write_wav(os.path.join(base, "silence_sample.wav"), silence)

bargein = [int(10_000 * math.sin(2 * math.pi * 300 * i / SAMPLE_RATE)) for i in range(N_SAMPLES)]
write_wav(os.path.join(base, "bargein_sample.wav"), bargein)

print("Done.")
