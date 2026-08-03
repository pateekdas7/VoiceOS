# GPU Deployment Checklist — VoiceOS v2

**Document type:** Mandatory per-deployment sign-off form  
**Version:** 1.0.0  
**Established:** 2026-08-03

> **Instructions:** Print or copy this checklist for every GPU deployment. Fill in every field. Sign off on every item. Save the completed checklist alongside the validation report. No deployment is complete without a signed checklist.

---

## Deployment Metadata

| Field | Value |
|---|---|
| Deployment date | __________________ |
| Deployment type | ☐ Fresh node build  ☐ Component update  ☐ Model update  ☐ Service update  ☐ Emergency rebuild |
| Target node | GPU UUID: __________________ / IP: __________________ |
| Operator | __________________ |
| Reason for deployment | __________________ |
| Sprint / change reference | __________________ |
| Validation report saved at | `deployment/gpu/validation_reports/VALIDATION_REPORT___________.txt` |

---

## Phase 1 — Pre-Deployment Audit

- [ ] **1.1** Ran audit commands from Deployment Manifest Step 1
- [ ] **1.2** Confirmed OS is Ubuntu 22.04 LTS  
  Actual: __________________
- [ ] **1.3** Confirmed kernel ≥ 5.15.0  
  Actual: __________________
- [ ] **1.4** NVIDIA driver version recorded  
  Actual: __________________ (Spec: 580.126.20)
- [ ] **1.5** CUDA version recorded  
  Actual: __________________ (Spec: 13.0)
- [ ] **1.6** NVIDIA Container Toolkit version recorded  
  Actual: __________________ (Spec: 1.19.0)
- [ ] **1.7** Docker version recorded  
  Actual: __________________ (Spec: 29.3.1)
- [ ] **1.8** Python 3.12 version recorded  
  Actual: __________________ (Spec: 3.12.13)
- [ ] **1.9** venv status: ☐ Exists  ☐ Missing  ☐ Created this deployment
- [ ] **1.10** Torch version recorded  
  Actual: __________________ (Spec: 2.11.0+cu130)
- [ ] **1.11** vLLM version recorded  
  Actual: __________________ (Spec: 0.24.0)
- [ ] **1.12** faster-whisper version recorded  
  Actual: __________________ (Spec: 1.2.1)
- [ ] **1.13** Whisper model directory size recorded  
  Actual: __________________ (Spec: ~1,547 MB)
- [ ] **1.14** Qwen model directory size recorded  
  Actual: __________________ (Spec: ~8,306 MB)
- [ ] **1.15** Veena model directory status: ☐ Exists  ☐ Missing
- [ ] **1.16** SNAC model directory status: ☐ Exists  ☐ Missing
- [ ] **1.17** voiceos-llm.service status: ☐ active  ☐ inactive  ☐ not installed
- [ ] **1.18** voiceos-stt.service status: ☐ active  ☐ inactive  ☐ not installed
- [ ] **1.19** voiceos-tts.service status: ☐ active  ☐ inactive  ☐ not installed
- [ ] **1.20** VRAM in use at audit time: __________________ / 23,034 MiB

---

## Phase 2 — Comparison and Gap Identification

- [ ] **2.1** Completed comparison table in Deployment Manifest Step 2
- [ ] **2.2** Identified all components that are Missing or Incompatible  
  List: __________________
- [ ] **2.3** Confirmed all compliant components will be skipped during installation

---

## Phase 3 — Installation (check only what was installed)

- [ ] **3.1** System packages — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.2** NVIDIA driver — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.3** CUDA toolkit — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.4** NVIDIA Container Toolkit — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.5** Docker — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.6** Docker NVIDIA runtime — configured: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.7** Python 3.12 — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.8** Python venv — created: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.9** PyTorch — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.10** vLLM — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.11** remaining Python deps — installed: ☐ Yes  ☐ Skipped (already compliant)
- [ ] **3.12** Whisper model — downloaded: ☐ Yes  ☐ Skipped (already present)
- [ ] **3.13** Qwen model — downloaded: ☐ Yes  ☐ Skipped (already present)
- [ ] **3.14** Veena + SNAC models — downloaded: ☐ Yes  ☐ Skipped (already present)
- [ ] **3.15** Confirmed no model was re-downloaded when it already existed at correct size

---

## Phase 4 — Service Configuration

- [ ] **4.1** `.env` file is present at `/opt/voiceos-gpu/.env`
- [ ] **4.2** `.env` has been populated with real credentials (not template defaults)
- [ ] **4.3** `.env` contains no secrets committed to git
- [ ] **4.4** systemd unit files deployed and match repo source (zero diff)
- [ ] **4.5** `systemctl daemon-reload` run after unit file changes
- [ ] **4.6** All three units are enabled (auto-start on boot)
- [ ] **4.7** Services started in correct order: LLM → (wait for /health) → STT + TTS

---

## Phase 5 — Validation Suite Results

Run: `python scripts/gpu_validation_suite.py` and fill in each result.

| Validation Check | Result |
|---|---|
| V-01: NVIDIA driver present and version correct | ☐ PASS  ☐ FAIL |
| V-02: CUDA available via torch.cuda.is_available() | ☐ PASS  ☐ FAIL |
| V-03: Python version == 3.12.x | ☐ PASS  ☐ FAIL |
| V-04: torch version == 2.11.0+cu130 | ☐ PASS  ☐ FAIL |
| V-05: vllm version == 0.24.0 | ☐ PASS  ☐ FAIL |
| V-06: faster-whisper version == 1.2.1 | ☐ PASS  ☐ FAIL |
| V-07: transformers version == 5.12.1 | ☐ PASS  ☐ FAIL |
| V-08: fastapi version == 0.136.3 | ☐ PASS  ☐ FAIL |
| V-09: Whisper model directory ≥ 1,400 MB | ☐ PASS  ☐ FAIL |
| V-10: Qwen model directory ≥ 7,500 MB | ☐ PASS  ☐ FAIL |
| V-11: Veena model directory non-empty | ☐ PASS  ☐ FAIL |
| V-12: SNAC model directory non-empty | ☐ PASS  ☐ FAIL |
| V-13: GPU memory allocation test passes | ☐ PASS  ☐ FAIL |
| V-14: VRAM ≤ 23,034 MiB with all services loaded | ☐ PASS  ☐ FAIL |
| V-15: STT /health/ready returns 200 | ☐ PASS  ☐ FAIL |
| V-16: LLM /health returns 200 | ☐ PASS  ☐ FAIL |
| V-17: TTS /health/ready returns 200 | ☐ PASS  ☐ FAIL |
| V-18: Port 8000 is bound | ☐ PASS  ☐ FAIL |
| V-19: Port 8100 is bound | ☐ PASS  ☐ FAIL |
| V-20: Port 8200 is bound | ☐ PASS  ☐ FAIL |
| V-21: STT end-to-end inference (transcript returned) | ☐ PASS  ☐ FAIL |
| V-22: LLM end-to-end inference (completion returned) | ☐ PASS  ☐ FAIL |
| V-23: TTS end-to-end inference (audio returned) | ☐ PASS  ☐ FAIL |
| V-24: TTS streaming (Transfer-Encoding: chunked) | ☐ PASS  ☐ FAIL |

**Overall validation result:** ☐ ALL PASS — deployment approved  ☐ FAILURES PRESENT — deployment blocked

---

## Deployment Gate Decision

```
All 24 validations PASS?
   YES → Deployment APPROVED. Continue to sign-off.
   NO  → Deployment BLOCKED. Do not proceed.
         Failed checks: __________________
         Root cause: __________________
         Remediation taken: __________________
         Re-validation result: __________________
```

---

## Deviations from Specification

List any state that differs from `GPU_RUNTIME_SPEC.md`:

| Component | Spec Value | Actual Value | Accepted? | Notes |
|---|---|---|---|---|
| | | | | |
| | | | | |

☐ No deviations — node matches spec exactly  
☐ Deviations present — listed above and accepted for this deployment

---

## Operator Sign-Off

I confirm that:
- Every step of the Deployment Manifest was followed in order
- All missing or incompatible components were remediated
- No model was re-downloaded when it already existed at the correct size
- No compliant service was restarted without cause
- The full validation suite was run and produced the results recorded above
- This completed checklist and the validation report have been saved to the repository

**Operator:** __________________  
**Date:** __________________  
**Validation report file:** `deployment/gpu/validation_reports/VALIDATION_REPORT___________.txt`
