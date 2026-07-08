# Sprint-007 — VAD & Endpointing

**Epic:** E2 — Core Voice Runtime  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** Sprint-006  
**Blocks:** Sprint-009 (indirectly, via Sprint-008 GPU Scheduler and Sprint-009 STT)  

---

## Objective

Implement voice activity detection (VAD), adaptive endpointing, barge-in detection, and backchannel discrimination. Output is segmented utterance boundaries (speech start/end) and real-time barge-in signals that trigger playback flush downstream.

---

## Architecture References

- Volume 1: Ch6 (VAD & Endpointing — Silero VAD, adaptive pause, barge-in, backchannel discrimination)
- DocSuite-02: Interface Contracts (VAD ↔ STT, VAD → DialogueManager barge-in signal)

---

## Components to Implement

### `src/services/vad-endpointing/`

```
src/services/vad-endpointing/
├── __init__.py
├── service.py              (VADEndpointingService: orchestrates VAD + endpoint + barge-in)
├── vad_engine.py           (VADEngine: Silero VAD ONNX inference)
├── endpoint_detector.py    (EndpointDetector: adaptive silence threshold → speech_end signal)
├── bargein_detector.py     (BargeinDetector: real-time barge-in during agent playback)
├── backchannel.py          (BackchannelDiscriminator: filters "hmm"/"haan"/"okay" fillers)
├── models/                 (Silero VAD ONNX model file + download script)
└── metrics.py              (vad_speech_ratio, endpoint_latency_ms, bargein_count)
```

**VADEngine (Silero VAD):**
- Model: Silero VAD v4 (ONNX format, ~1MB)
- Input: 16kHz int16 PCM frames (512 samples = 32ms windows)
- Output: `speech_probability: float` per window
- Threshold: configurable (default 0.5 for speech, 0.35 for silence)
- ONNX runtime: `onnxruntime` CPU inference (GPU not needed for VAD — too small)
- Latency: < 5ms per window on CPU

**EndpointDetector:**
- Tracks running `SpeechState`: IN_SPEECH | IN_SILENCE | ENDPOINT_DETECTED
- `start_threshold_ms`: consecutive speech frames before declaring speech start (default 100ms)
- `end_threshold_ms`: adaptive — starts at 600ms, increases to 1200ms when human pauses frequently
- Adapts `end_threshold_ms` per-call: if ≥ 3 false endpoints detected (speech resumes after silence), increase threshold by 200ms
- Emits `VADSpeechStart` event when speech start detected
- Emits `VADSpeechEnd` event when silence exceeds threshold → this triggers STT to finalize

**BargeinDetector:**
- Active only when agent is playing audio (PlaybackActive state)
- Uses VAD probability: if `speech_probability > barge_in_threshold` (default 0.65) for ≥ 200ms during playback → barge-in detected
- Emits `BargeinDetected` event → received by PlaybackScheduler (flushes audio) and DialogueManager (yields turn)
- Higher threshold than normal speech (0.65 vs 0.5) to reduce false barge-ins from background noise

**BackchannelDiscriminator:**
- Short speech segments (< 800ms after barge-in) are classified as backchannel vs. full turn
- Uses duration heuristic: < 800ms = likely backchannel ("hmm", "haan", "theek hai")
- Backchannel: emit `BackchannelDetected` (agent continues playing) instead of full `BargeinDetected`
- Passes short utterance to STT for transcription anyway, but doesn't yield turn

---

## Files Expected to Change

**New:** `src/services/vad-endpointing/` (all files above)  
**New:** `src/services/vad-endpointing/models/download_silero.py`  
**New:** `tests/unit/services/test_vad.py`  
**New:** `tests/unit/services/test_endpointing.py`  
**New:** `tests/unit/services/test_bargein.py`  
**New:** `tests/audio_clips/speech_sample.wav`, `tests/audio_clips/silence_sample.wav`, `tests/audio_clips/bargein_sample.wav`

---

## Acceptance Criteria

- [ ] VADEngine correctly classifies speech (probability > 0.5) vs. silence (probability < 0.5) on labeled test audio clips (≥ 95% precision and recall)
- [ ] EndpointDetector emits `VADSpeechEnd` after configurable silence threshold (default 600ms)
- [ ] EndpointDetector adapts threshold upward after repeated false endpoints
- [ ] BargeinDetector emits `BargeinDetected` only when `speech_probability > 0.65` for ≥ 200ms
- [ ] BackchannelDiscriminator correctly classifies short utterances (< 800ms) as backchannel fillers
- [ ] VAD inference latency: p99 < 5ms per 32ms window on CPU
- [ ] `BargeinDetected` event carries correct `call_id`, `tenant_id`, `timestamp_ms`

---

## Required Tests

**Unit:**
- `test_vad_speech_detection` — speech audio clip → probability > 0.5
- `test_vad_silence_detection` — silence audio clip → probability < 0.35
- `test_endpoint_speech_start` — 150ms of speech → VADSpeechStart emitted
- `test_endpoint_speech_end_600ms` — 600ms silence after speech → VADSpeechEnd emitted
- `test_endpoint_adaptation` — 3 false endpoints → threshold increases to 800ms
- `test_bargein_threshold` — speech during playback at 0.70 probability for 250ms → BargeinDetected
- `test_backchannel_suppression` — 500ms utterance during playback → BackchannelDetected (not BargeinDetected)

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass
- [ ] Silero ONNX model file committed or download script documented
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-008
