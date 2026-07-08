# Sprint-006 — Audio Preprocessing Pipeline

**Epic:** E2 — Core Voice Runtime  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** Sprint-005  
**Blocks:** Sprint-007  

---

## Objective

Implement the audio preprocessing pipeline: acoustic echo cancellation (AEC3), noise suppression (NS), automatic gain control (AGC), and 8kHz→16kHz resampling. Output is clean, normalized 16kHz audio suitable for Silero VAD and Whisper STT.

---

## Architecture References

- Volume 1: Ch5 (Audio Preprocessing — AEC3/WebRTC, NS, AGC, resampling)
- DocSuite-02: Interface Contracts (AudioPreprocessor ↔ VAD)

---

## Components to Implement

### `src/services/audio-preprocessing/`

```
src/services/audio-preprocessing/
├── __init__.py
├── service.py              (AudioPreprocessorService: pipeline composition)
├── pipeline.py             (AudioPipeline: ordered stage runner)
├── stages/
│   ├── __init__.py
│   ├── aec3.py             (AEC3Stage: WebRTC AEC3 echo cancellation)
│   ├── noise_suppression.py (NSStage: RNNoise or WebRTC NS)
│   ├── agc.py              (AGCStage: WebRTC AGC2, target -18 dBFS)
│   └── resampler.py        (ResamplerStage: 8kHz → 16kHz sinc interpolation)
├── quality.py              (AudioQualityMetrics: SNR estimation, ERLE measurement)
└── metrics.py              (Prometheus: preprocessing_latency_ms, erle_db)
```

**AEC3Stage:**
- Integrates WebRTC AEC3 via Python binding (webrtc-audio-processing or equivalent)
- Requires reference signal (far-end audio being played back) as input
- ERLE (Echo Return Loss Enhancement) target: ≥ 20 dB
- Operates on 10ms frames (80 samples at 8kHz → 160 samples at 16kHz post-resample)

**NSStage:**
- RNNoise or WebRTC NS (configurable via env var `VOICEOS_NS_BACKEND`)
- Target: ≥ 10 dB SNR improvement on typical office noise

**AGCStage:**
- WebRTC AGC2: compressor + gain adjuster
- Target level: -18 dBFS RMS (configurable)
- Max gain: 30 dB

**ResamplerStage:**
- Input: 8kHz μ-law or linear PCM (from Media Gateway)
- Output: 16kHz linear PCM (int16)
- Method: sinc-interpolation (scipy.signal.resample_poly or equivalent)
- Latency: ≤ 2ms per frame

**Pipeline order:** AEC3 → NS → AGC → Resample  
(Resampling is last; all other stages run at 8kHz)

---

## Files Expected to Change

**New:** `src/services/audio-preprocessing/` (all files above)  
**New:** `tests/unit/services/test_audio_preprocessing.py`  
**New:** `tests/integration/services/test_preprocessing_pipeline.py`  
**Modified:** `src/libs/contracts/audio.py` — confirm AudioFrame carries `sample_rate: SampleRate` and `encoding: Encoding` fields

---

## Acceptance Criteria

- [ ] Pipeline processes a 10ms frame through AEC3 → NS → AGC → Resample in ≤ 5ms (unit benchmark)
- [ ] Resampler output is 16kHz int16 PCM (correct length: input_len × 2)
- [ ] AEC3 reduces echo level (ERLE ≥ 10 dB on test fixture with synthetic echo — ERLE ≥ 20 dB is the production target, validated in Sprint-028)
- [ ] AGC normalizes a quiet input (< -30 dBFS) to target level (within 3 dB of -18 dBFS)
- [ ] Pipeline stages are configurable — any stage can be disabled via config without modifying code
- [ ] Each stage can be tested in isolation (takes AudioFrame in, returns AudioFrame out)

---

## Required Tests

**Unit:**
- `test_resample_8k_to_16k` — 10ms frame of zeros at 8kHz → 20ms frame at 16kHz (double length)
- `test_agc_normalizes_quiet_signal` — -40 dBFS input → output within 3 dB of -18 dBFS
- `test_pipeline_stage_isolation` — disable NS stage, AEC3 + AGC + Resample still run
- `test_pipeline_latency_benchmark` — 100 frames processed; p99 ≤ 5ms per frame

**Integration:**
- `test_full_pipeline_on_audio_fixture` — run test WAV through full pipeline, output is 16kHz, no clipping (abs(max) < 32768)

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-007
