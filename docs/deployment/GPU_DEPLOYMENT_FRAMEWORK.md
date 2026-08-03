# GPU Deployment Framework — VoiceOS v2

**Document type:** Master framework index and operational guide  
**Version:** 1.0.0  
**Established:** 2026-08-03

---

## Overview

The GPU Deployment Framework provides a deterministic, repeatable procedure for deploying and validating the VoiceOS v2 GPU inference stack. Every deployment follows the same sequence, produces the same validation report, and gates on the same acceptance criteria — regardless of operator.

**Stack:** Whisper STT + Qwen2.5 LLM + Veena TTS on NVIDIA L4 (23,034 MiB VRAM)  
**Host:** `/opt/voiceos-gpu/`  
**Branch:** `claude/vscode-extension-setup-fa9090`

---

## Document Map

| Document | Purpose |
|---|---|
| [`GPU_RUNTIME_SPEC.md`](GPU_RUNTIME_SPEC.md) | Canonical version and path requirements for every component |
| [`GPU_COMPATIBILITY_MATRIX.md`](GPU_COMPATIBILITY_MATRIX.md) | Supported component combinations; known incompatibilities |
| [`GPU_DEPLOYMENT_MANIFEST.md`](GPU_DEPLOYMENT_MANIFEST.md) | 10-step installation procedure with skip logic |
| [`GPU_DEPLOYMENT_CHECKLIST.md`](GPU_DEPLOYMENT_CHECKLIST.md) | Per-deployment sign-off form (print or copy for each deployment) |
| **This document** | Master index; quick start; script map; gate definitions |

---

## Script Map

| Script | Purpose | Run as |
|---|---|---|
| `deployment/gpu/audit.sh` | Capture current node state — versions, models, services, VRAM | Any user |
| `deployment/gpu/deploy.sh` | 10-step master deployment orchestrator | `root` |
| `scripts/gpu_validation_suite.py` | 46 automated checks: driver, CUDA, deps, models, ports, inference | Any user |
| `scripts/gpu_ai_validation.py` | STT→LLM→TTS pipeline test; latency and VRAM measurement | Any user |
| `scripts/gpu_benchmark.py` | Performance benchmarks: p50/p95/p99 for each service | Any user |
| `scripts/gpu_acceptance_gates.py` | Master gate runner: executes all scripts, decides PASS/FAIL | Any user |
| `scripts/gpu_deployment_report.py` | Generates full deployment report markdown | Any user |

---

## Quick Start

### First-time deployment (fresh Ubuntu 22.04 node)

```bash
# 1. Clone repo and enter deployment directory
git clone <repo> voiceos && cd voiceos

# 2. Audit current node state
bash deployment/gpu/audit.sh | tee deployment/gpu/deployment_reports/AUDIT_$(date +%Y%m%d).txt

# 3. Run full deployment (as root)
sudo bash deployment/gpu/deploy.sh 2>&1 | tee deployment/gpu/deployment_reports/DEPLOY_$(date +%Y%m%d).txt

# 4. Run validation suite
python scripts/gpu_validation_suite.py

# 5. Run AI pipeline validation
python scripts/gpu_ai_validation.py

# 6. Run benchmarks
python scripts/gpu_benchmark.py

# 7. Run acceptance gates (master)
python scripts/gpu_acceptance_gates.py

# 8. Generate deployment report
python scripts/gpu_deployment_report.py
```

### Component update (existing node)

```bash
# Audit first — never update blindly
bash deployment/gpu/audit.sh

# Run validation to establish pre-update baseline
python scripts/gpu_validation_suite.py --output deployment/gpu/deployment_reports/PRE_UPDATE.txt

# Apply update (targeted steps only — see GPU_DEPLOYMENT_MANIFEST.md)
# ...

# Validate after update
python scripts/gpu_acceptance_gates.py
python scripts/gpu_deployment_report.py
```

---

## Acceptance Gate Definitions

Gates are evaluated in order. A failed gate stops evaluation — later gates are not meaningful if earlier gates fail.

| Gate | ID | Checks | Blocking |
|---|---|---|---|
| Runtime Foundation | G-01 | V-01, V-02, V-03, V-25, V-26, V-27, V-28 | Yes |
| Dependencies | G-02 | V-04 to V-08, V-41, V-42 | Yes |
| Models | G-03 | V-09 to V-12, V-43, V-44 | Yes |
| Infrastructure | G-04 | V-29, V-30, V-31, V-32 to V-37, V-38, V-39 | Yes |
| AI Services | G-05 | V-15 to V-17, V-18 to V-20, V-45, V-46 | Yes |
| End-to-End Inference | G-06 | V-21, V-22, V-23, V-24 | Yes |
| GPU Resources | G-07 | V-13, V-14, V-40 | Yes |
| AI Pipeline | G-08 | Full STT→LLM→TTS flow (gpu_ai_validation.py) | Yes |
| Performance | G-09 | Latency budgets (gpu_benchmark.py) | Yes |
| Stability | G-10 | VRAM growth ≤ 200 MiB over 5 iterations | Yes |

### Latency Budgets (Gate G-09)

| Service | Metric | Budget |
|---|---|---|
| STT | p95 latency per audio second | ≤ 300 ms |
| LLM | p95 TTFT | ≤ 500 ms |
| TTS | p95 TTFA (time-to-first-audio) | ≤ 1,500 ms |
| E2E pipeline | p95 total | ≤ 3,000 ms |

---

## Report Locations

| Report type | Default path |
|---|---|
| Audit output | `deployment/gpu/deployment_reports/AUDIT_{DATE}.txt` |
| Validation suite | `deployment/gpu/validation_reports/VALIDATION_REPORT_{DATE}.txt` |
| AI validation | `deployment/gpu/deployment_reports/AI_VALIDATION_{DATE}.txt` |
| Benchmark | `deployment/gpu/deployment_reports/BENCHMARK_{DATE}.txt` |
| Acceptance gates | `deployment/gpu/deployment_reports/GATES_{DATE}.txt` |
| Full deployment report | `deployment/gpu/deployment_reports/REPORT_{DATE}.md` |

---

## Deployment Gate Decision

```
Run: python scripts/gpu_acceptance_gates.py

ALL 10 GATES PASS?
   YES → Print the deployment report, sign the checklist, mark deployment APPROVED
   NO  → Investigate failed gate, remediate, re-run from failed gate onwards
         Never sign off on a deployment with any failing gate
```

---

## Architecture Reference

| Volume | Chapters |
|---|---|
| V1 Ch8 | STT (Whisper adapter, GPU inference server) |
| V1 Ch9 | LLM (vLLM engine, Qwen adapter) |
| V1 Ch11 | TTS (Veena + SNAC, streaming, TTFA) |
| V1 Ch26 | Runtime Deployment Topology & Compute Architecture |
| V7 | Operations & Scaling |
