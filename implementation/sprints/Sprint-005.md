# Sprint-005 — Audio Session Manager

**Epic:** E2 — Core Voice Runtime  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** Sprint-004  
**Blocks:** Sprint-006  

---

## Objective

Implement the Audio Session Manager — the service that receives raw audio frames from the Media Gateway, compensates for network jitter and packet loss, maintains the session clock, and outputs a clean, time-aligned audio stream for preprocessing.

---

## Architecture References

- Volume 1: Ch4 (Audio Session Manager — jitter buffer, PLC, session clock, session lifecycle)
- DocSuite-02: Interface Contracts (AudioSessionManager ↔ AudioPreprocessor)

---

## Components to Implement

### `src/services/audio-session-manager/`

```
src/services/audio-session-manager/
├── __init__.py
├── service.py              (AudioSessionManagerService: manages per-call sessions)
├── session.py              (AudioSession: session state machine + lifecycle)
├── jitter_buffer.py        (AdaptiveJitterBuffer: target delay, min/max bounds)
├── plc.py                  (PacketLossConcealer: G.711/G.722 PLC)
├── clock.py                (SessionClock: RTP timestamp → wall clock mapping)
└── metrics.py              (jitter_ms, loss_rate, buffer_depth gauges)
```

**Session lifecycle state machine:**
```
CONNECTING → ACTIVE → BARGE_IN → ENDING → CLOSED
                ↑          |
                └──────────┘ (barge-in ends, returns to ACTIVE)
```

**AdaptiveJitterBuffer:**
- Target delay: configurable (default 80ms), adjusts based on observed jitter
- Min delay: 20ms, Max delay: 200ms
- Reorder buffer: handles out-of-order RTP packets (up to 5 packets out of order)
- Overflow: when buffer depth > max_depth, oldest packets are dropped (RI-3 compliant)
- Emits `jitter_ms` metric per 100ms window

**PacketLossConcealer:**
- Detects sequence gaps (lost packets)
- G.711 PLC: repeats last frame with fade-out over 3 frames (for gaps ≤ 3 frames)
- G.722 PLC: waveform similarity repeat
- Marks concealed frames with `is_plc: bool` in AudioFrame

**SessionClock:**
- Maps RTP timestamp to wall-clock milliseconds
- Handles clock wrap-around (32-bit RTP timestamp)
- Provides `timestamp_ms()` for current real-time position in stream

---

## Files Expected to Change

**New:** `src/services/audio-session-manager/` (all files above)  
**New:** `tests/unit/services/test_audio_session_manager.py`  
**Modified:** `src/libs/contracts/audio.py` — add `is_plc: bool` field to AudioFrame if not already present

---

## Acceptance Criteria

- [ ] Jitter buffer reorders out-of-order packets correctly (unit test)
- [ ] Jitter buffer enforces max depth — overflow drops oldest frame, not newest
- [ ] PLC correctly synthesizes 3 concealment frames for a 3-packet gap (unit test)
- [ ] SessionClock maps RTP timestamps correctly across a simulated 10-minute call (unit test)
- [ ] Session transitions from CONNECTING → ACTIVE on first frame received
- [ ] Session transitions to BARGE_IN state when BargeinDetected event received
- [ ] Session transitions to CLOSED cleanly on ENDING (no dangling resources)
- [ ] `jitter_ms` metric is emitted periodically during active session

---

## Required Tests

**Unit:**
- `test_jitter_buffer_reorder` — packets arrive [3,1,2], output is [1,2,3]
- `test_jitter_buffer_overflow` — enqueue max_depth+1 packets, oldest is dropped
- `test_plc_gap_3_frames` — gap of 3, output contains 3 PLC frames with is_plc=True
- `test_session_clock_mapping` — RTP timestamp 0 → 0ms, RTP timestamp 8000 → 1000ms (at 8kHz)
- `test_session_lifecycle` — CONNECTING → ACTIVE → BARGE_IN → ACTIVE → ENDING → CLOSED
- `test_plc_marks_frames` — PLC frames have is_plc=True, real frames have is_plc=False

---

## Definition of Done

- [x] All AC items checked
- [x] All tests pass (495/495, 25 skipped live-DB)
- [x] CI green (ruff ✓, mypy --strict 93 files ✓, boundary check 0 violations ✓)
- [x] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [x] `CURRENT_SPRINT.md` updated to Sprint-006
