# CALL-001 TRIAL-001 REPORT
## Sprint-029 Phase 2 — Founder Validation

**Date:** 2026-07-12  
**Time:** 17:54:39 UTC → 17:56:04 UTC  
**Trial:** call-001 / trial-001  
**Evaluator:** Claude (automated) + Prateek Das (founder, audio review pending)

---

## Primary Goal

Prove that the customer can hear a clear, natural, responsive AI voice.  
Evaluation target: Veena TTS (maya-research/Veena, Kavya voice) producing a Hindi/Hinglish collections greeting, delivered over Twilio PSTN to the founder's phone.

---

## Result

**PENDING FOUNDER AUDIO REVIEW**

Automated measurements complete. Awaiting founder's subjective audio assessment (the authoritative gate for Call-001).

---

## Call Artifact

| Field | Value |
|---|---|
| Call SID | CA54a74f177ec3e45feba3faa709a75a67 |
| Twilio Recording SID | RE28e691d187db32d14edfdf470f8f251c |
| Call status | completed |
| Call duration (PSTN) | 29 s |

### Primary WAV — 24kHz reference (for quality review)

| Field | Value |
|---|---|
| Filename | `call-001-trial-001.wav` |
| Format | WAV PCM16, 24000 Hz, 1ch, 16-bit |
| Duration | **8.513 s** |
| Size | 408,652 bytes |
| SHA-256 | `e363e98bd7cda118373846883b7e67c3f400233f82e8912f0ee3e9b0e6560483` |
| Local path | `evaluation/founder-validation/call-001/trial-001/call-001-trial-001.wav` |

### Twilio call recording — telephone quality (what founder actually heard)

| Field | Value |
|---|---|
| Filename | `call-001-trial-001-twilio.wav` |
| Format | WAV PCM16, 8000 Hz, 1ch, 16-bit |
| Duration | **25.045 s** (8.5s AI audio + 8s silence pause + 8.5s connection overhead) |
| Size | 400,758 bytes (downloaded from Twilio) |
| SHA-256 | `7f7287007925253d5c102c3b0111535180ffcb467d043f2a44bb1892223eb2af` |
| Local path | `evaluation/founder-validation/call-001/trial-001/call-001-trial-001-twilio.wav` |

**Retrieval:** Both files are present at the local paths above on the Termux device.  
To share `call-001-trial-001.wav` with Gemini or any audio analysis tool, use the path above directly.

---

## Greeting Delivered

```
Namaste, Prateek Das ji. Main Kavya hun, Rajat Finance ke collections team se
baat kar rahi hun. Aapke loan account mein pachaas hazaar rupay ka outstanding
balance hai, jo tees July do hazaar chhabbees tak due hai. Kya aap aaj payment
ke baare mein baat kar sakte hain? Main aapki poori madad karna chahti hun.
```

**Scenario:** Prateek Das, Loan LOAN-TEST-001, ₹50,000 outstanding, due 2026-07-30.

---

## Criterion Scores (automated / measurable)

Scoring scale: 0 (not assessed) | 1 (fail) | 2 (poor) | 3 (fair) | 4 (good) | 5 (excellent)  
Human dimensions marked **PENDING** — await founder review.

| Criterion | Score | Evidence |
|---|---|---|
| **TTS TTFA** | 4 | 664 ms (budget ≤1500 ms — PASS, 836 ms headroom) |
| **First-audio latency (pre-generated)** | 4 | Twilio delivered WAV within ~5s of call answer (HTTP GET at T+5s) |
| **Audio format validity** | 5 | Valid RIFF WAV, 24kHz PCM16, correctly formed |
| **Duration completeness** | 5 | 8.513 s — full greeting rendered, no truncation |
| **Audio clarity** | PENDING | Founder review |
| **Intelligibility** | PENDING | Founder review |
| **Voice naturalness** | PENDING | Founder review |
| **Robotic characteristics** | PENDING | Founder review |
| **Hindi pronunciation** | PENDING | Founder review (Namaste, hun, baat, chahti) |
| **English loan-words in Hindi** | PENDING | Founder review (loan account, balance, payment, team) |
| **Hinglish code-switching** | PENDING | Founder review |
| **Name pronunciation** | PENDING | Founder review (Prateek Das ji) |
| **Number pronunciation** | PENDING | Founder review (pachaas hazaar rupay = ₹50,000) |
| **Date pronunciation** | PENDING | Founder review (tees July do hazaar chhabbees) |
| **Speaking pace** | PENDING | Founder review |
| **Prosody / intonation** | PENDING | Founder review |
| **Emotional appropriateness** | PENDING | Founder review |
| **Volume consistency** | PENDING | Founder review |
| **Clipping / distortion** | PENDING | Founder review |
| **Codec artifacts (SNAC)** | PENDING | Founder review |
| **Chunk boundary stitching** | PENDING | Founder review |
| **Streaming smoothness** | PENDING | Founder review |
| **Unexpected silence / gaps** | PENDING | Founder review |
| **TTS MOS (Audio Quality)** | PENDING | Founder review (gate ≥ 3.5/5) |

---

## Measured Evidence

| Metric | Value | Budget / Gate | Status |
|---|---|---|---|
| TTS TTFA | **664 ms** | ≤ 1500 ms | ✅ PASS |
| TTS total generation | **22,122 ms** | N/A (pre-gen) | ⚠️ P1 issue (see below) |
| Audio duration | **8.513 s** | — | measured |
| Call PSTN duration | **29 s** | — | measured |
| Call final status | **completed** | must be completed | ✅ |
| WAV SHA-256 integrity | verified | — | ✅ |
| Twilio recording retrieved | **25.045 s** | — | ✅ |

---

## Issues Found

### P1 — TTS total generation time: 22,122 ms (2.6× real-time)

**Symptom:** TTS generation of 8.513 s of audio took 22.1 s wall-clock.  
**Expected:** ~4–5 s (as measured in previous latency runs).  
**Impact on this trial:** None — audio was pre-generated before the call was placed. The founder heard the full greeting uninterrupted.  
**Impact on production:** In a real-time call, TTS synthesis must complete faster than real-time. A 22 s generation for 8.5 s of audio would stall the call pipeline.

**Root cause: UNDER INVESTIGATION.**  
Hypotheses:
1. GPU thermal state: The GPU ran 100 inference calls immediately prior (Run F latency validation). It may have been in sustained power-cap throttle (1635 MHz) or even memory-pressure state.
2. VRAM fragmentation: vLLM's memory allocator may have fragmented available VRAM, leaving TTS with reduced bandwidth.
3. HTTP streaming: TTFA was 664 ms (first chunk fast), but subsequent chunks slow — suggests token generation rate dropped mid-synthesis.

**Evidence needed:** GPU clock / VRAM / power reading at time of TTS call; `nvidia-smi dmon` during synthesis.

**Severity:** P1 in production. P3 for this trial (no call impact).

### P3 — Twilio recording format is 8kHz

**Symptom:** Twilio call recording is 8000 Hz PCM (standard telephony codec).  
**Impact:** The 24kHz reference WAV preserves full Veena quality; Twilio recording captures telephone-quality downsampled audio.  
**Expected:** This is normal for PSTN calls. The 24kHz WAV (`call-001-trial-001.wav`) is the authoritative quality reference.  
**Action:** Use `call-001-trial-001.wav` (24kHz) for MOS scoring and quality assessment, not the Twilio recording.

---

## Files Changed

None — this is evaluation execution, no source code changed.

---

## Deployment Changes

None — pre-existing GPU services used (STT :8100, LLM :8000, TTS :8200).

---

## Before vs After

N/A — Trial-001 is the first trial; no prior baseline to compare.

---

## Regression Check

No prior calls to regress against.

---

## Recommendation

**AWAITING FOUNDER AUDIO REVIEW**

The automated system has:
1. ✅ Delivered a real PSTN call to +919911954448
2. ✅ Generated a Hindi/Hinglish collections greeting via Veena (Kavya voice)
3. ✅ Produced `call-001-trial-001.wav` (24kHz, 8.513 s) — the primary quality artifact
4. ✅ Captured the Twilio call recording (`call-001-trial-001-twilio.wav`, 25 s)
5. ✅ TTS TTFA = 664 ms — within 1500 ms budget
6. ⚠️ TTS total generation = 22 s — P1 issue to investigate (no call impact here)

**Next step:** Please listen to both WAV files and provide your audio observations. Your review is the gate for Call-001. Until you approve, we do not advance to Call-002.
