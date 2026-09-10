# SERVER-RETURN EXECUTION READINESS REPORT

**Date:** 2026-07-12  
**Prepared during:** CPU/GPU server unavailability window (6–8 hours)  
**Sprint state:** Sprint-028 INCOMPLETE/BLOCKED · Sprint-029 Phase 1 COMPLETE · Sprint-029 Phase 2 PENDING

---

## 1. Offline Fixes Applied This Session

All fixes were verified (pyflakes clean, 72/81 unit tests pass/9 skip).

| Fix | File | Status | Resolves |
|-----|------|--------|----------|
| STT asyncio.run_in_executor — blocks GPU kernel hang | `deployment/gpu/services/stt/server.py` | ✅ APPLIED | TT-024 (partial) |
| TTS speaker allowlist — ALLOWED_SPEAKERS={"kavya"}, 422 on unknown | `deployment/gpu/services/tts/server.py` | ✅ APPLIED | PEN-005/006/007 |
| PolicyEngine explicit PERMIT audit — V4 Ch11 compliance | `src/services/policy_engine/engine.py` | ✅ APPLIED | TT-027(c) partial |
| Transcript export tool (real → harness JSON) | `scripts/evaluate/export_call_transcript.py` | ✅ CREATED | Sprint-029 Phase 2 blocker |
| MOS scoring script (capture/dnsmos/crowdsource/score) | `scripts/validate/mos_check.py` | ✅ CREATED | Sprint-029 Phase 2 blocker |
| Production call-samples directory + README | `evaluation/call-samples/production/README.md` | ✅ CREATED | Sprint-029 Phase 2 |
| Audio samples directory | `evaluation/audio-samples/production/` | ✅ CREATED | Sprint-029 Phase 2 |
| TTS budget ADR (250ms → 750ms) | `implementation/adrs/ADR-004-tts-latency-budget-revision.md` | ✅ CREATED (PROPOSED) | TT-027(e) |

---

## 2. Sprint-028 Remaining Work — Exact AC Matrix

### AC-1: First-audio p95 ≤ 1,500 ms over 100 sequential calls

| | |
|---|---|
| **Status** | ❌ FAIL / BLOCKED |
| **Evidence** | Run A (Termux→GPU): p95=2,357ms. Run B (intra-DC): p95=1,950ms. Run C (post-fix): p95≈2,000ms. All FAIL. |
| **Root cause** | L4 thermal throttle after ~110s continuous inference at 72W TDP: GPU clock 2040MHz→~1000MHz (49%), all models double in latency. Cold-GPU p95≈933ms (PASS); sustained-load FAIL. |
| **Server dependency** | GPU node 217.18.55.78 AND CPU node 101.53.137.131 (intra-DC path) |
| **Command to resume** | `python3 scripts/validate/latency_validation_phase2.py --gpu-host 217.18.55.78 --calls 100 2>&1 \| tee /tmp/latency_run_d.txt` from CPU node 101.53.137.131 |
| **Gate** | first_audio p95 ≤ 1,500 ms across all 100 calls |
| **Likely outcome on return** | FAIL unless: (a) thermal throttle is solved (higher-TDP GPU/multiple L4s), or (b) GPU has been idle ≥4h (fully cooled) AND test completes before throttle onset (~22 calls) |

### AC-2: 500-concurrent-user load test — first-audio p95 ≤ 1,650 ms, error rate < 0.1%

| | |
|---|---|
| **Status** | ❌ NOT EXECUTED |
| **Evidence** | 10-user test: TTS TTFA p50=12,589ms, first_audio p95=16,524ms — 10× FAIL. Root cause: Veena single-threaded synthesis queue. 500-user test requires GPU fleet. |
| **Server dependency** | Second GPU node (not provisioned), Locust test runner node |
| **Command to resume** | `locust -f tests/load/locustfile.py --host http://101.53.137.131:8000 --users 500 --spawn-rate 0.83 --run-time 45m --headless` |
| **Blocker** | GPU fleet not available (single L4). Cannot execute without provisioning second GPU node. |

### AC-3: GPU fleet chaos — kill node-0, traffic must reroute within 30s, zero call drops

| | |
|---|---|
| **Status** | ❌ BLOCKED |
| **Evidence** | Single L4 (TT-015: cross-provider NAT blocks cluster join). GPU fleet failover requires ≥2 GPU nodes. |
| **Server dependency** | Second GPU node + WireGuard mesh between providers (TT-015 resolution) |
| **Command to resume** | `python3 scripts/validate/chaos_test.py --scenario gpu-node-failure --gpu-host-0 217.18.55.78` (after second node added) |
| **Blocker** | Requires GPU fleet. Infrastructure procurement decision. |

### AC-4: Security — API gateway deployed, pen test signed off by engineering lead

| | |
|---|---|
| **Status** | ⚠️ CONDITIONAL / PARTIAL |
| **Evidence** | 9 findings in pen-test-report.md. PEN-001/002/003 (HIGH — unauthenticated STT/LLM/TTS endpoints) conditionally acceptable only if API gateway deployed before external exposure. PEN-005/006/007 speaker allowlist: **FIXED offline this session.** PEN-004 prompt injection: requires Garak/PyRIT red-team follow-up. Engineering lead has NOT signed off. |
| **Server dependency** | API gateway deployment (CPU node 101.53.137.131 → Kubernetes API gateway service) |
| **Command to resume** | (1) Deploy Nginx/Kong/Traefik as API gateway in K8s; (2) Re-run `python3 scripts/validate/pen_test.py --host 101.53.137.131` to verify PEN-001-003 blocked; (3) Run Garak against LLM: `garak --model_type huggingface --model_name Qwen2.5-7B --probes promptinject`; (4) Engineering lead reviews and signs off `evaluation/security/pen-test-report.md` |
| **Offline progress** | PEN-005/006/007 speaker allowlist fixed — deploy with next GPU server restart |

### AC-5: Compliance — audit hash chain fully populated for live writes + PolicyEngine PERMIT auditing

| | |
|---|---|
| **Status** | ⚠️ PARTIAL (audit hash chain correctly implemented; PERMIT auditing NOW FIXED) |
| **Evidence** | `AuditRepository.append()` always computes SHA-256 hash chain correctly. PolicyEngine only audited DENY/FORBID/REQUIRE — PERMIT (explicit rule match) was skipped. **FIXED offline this session.** Audit hash chain gap was pre-Sprint-020 NULL rows — current `iter_chain()` correctly filters these with `hash IS NOT NULL`. |
| **Server dependency** | CPU node 101.53.137.131 — apply PolicyEngine fix, verify with `python3 scripts/validate/audit_chain.py` |
| **Command to resume** | `python3 scripts/validate/audit_chain.py --host 101.53.137.131` after deploying the engine.py fix |
| **Offline progress** | PolicyEngine explicit-PERMIT audit fix applied (`engine.py`) |

### AC-6: Canary deployment — 5%→25%→50%→100% traffic split without downtime

| | |
|---|---|
| **Status** | ❌ NOT EXECUTED |
| **Evidence** | No Argo Rollouts, no Flagger, no weighted ingress in the cluster. `FleetRolloutManager` is Python-only ring assignment, not Kubernetes traffic splitting. |
| **Server dependency** | CPU node K8s cluster 101.53.137.131 + Argo Rollouts or Flagger installation |
| **Command to resume** | `kubectl apply -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml` then create Rollout manifest for `voiceos-conversation-engine` |

### AC-8: BenchmarkSuite.run_benchmarks() PASS — all latency budgets met

| | |
|---|---|
| **Status** | ❌ FAIL (TTS budget 250ms vs physical minimum 642ms) |
| **Evidence** | `BenchmarkSuite.run_benchmarks()` asserts TTS_BUDGET_MS=250. Veena 3B minimum is 21 tokens × 30.2ms = 634ms. Always fails. |
| **Server dependency** | None — code fix only (after ADR-004 approved) |
| **Command to resume** | After engineering lead approves ADR-004: update `src/services/benchmark/suite.py` TTS_BUDGET_MS=250→750; re-run `python3 -m pytest tests/unit/services/test_benchmark_suite.py` |
| **Offline progress** | ADR-004 created, awaiting sign-off |

### AC-12: Threat model — reviewed and signed off by engineering lead

| | |
|---|---|
| **Status** | ❌ NOT SIGNED OFF |
| **Evidence** | `docs/security/threat-model.md` exists with all 6 STRIDE categories. Requires engineering lead signature. |
| **Server dependency** | None — human review only |
| **Command to resume** | Engineering lead reviews `docs/security/threat-model.md`; adds sign-off block at document end |

---

## 3. Sprint-029 Phase 2 Remaining Work — Exact AC Matrix

### Sprint-029 prerequisite: Sprint-028 must be complete

Sprint-029 Phase 2 cannot start until Sprint-028 is marked complete (M-7). All Sprint-028 ACs above must pass first.

### Phase 2 AC: ≥50 real AI call transcripts

| | |
|---|---|
| **Status** | ❌ NOT EXECUTED |
| **Server dependency** | GPU node 217.18.55.78 (Veena/Whisper/vLLM running), CPU node 101.53.137.131 (ConversationEngine) |
| **Command** | `python3 scripts/validate/walking_skeleton.py --gpu-host 217.18.55.78 --calls 50` to generate calls; then `python3 scripts/evaluate/export_call_transcript.py --tenant-id <uuid> --limit 100 --output evaluation/call-samples/production/` |
| **Gap** | Transcripts will have `human_intent: null` — human annotation required before IntentAccuracy can be scored |

### Phase 2 AC: Automated eval suite on production transcripts

| | |
|---|---|
| **Status** | ❌ NOT EXECUTED |
| **Server dependency** | None after transcripts are exported |
| **Command** | `python3 tests/ai_eval/founder_validation_suite.py --transcripts evaluation/call-samples/production/` |

### Phase 2 AC: First-audio p95 ≤ 1,500 ms (from OTel traces)

| | |
|---|---|
| **Status** | ❌ BLOCKED (same as Sprint-028 AC-1) |
| **Server dependency** | Sprint-028 AC-1 must pass first |
| **Command** | `python3 scripts/evaluate/export_call_transcript.py` with `JAEGER_URL=http://101.53.137.131:16686` — extracts `first_audio_ms` from Jaeger spans automatically |

### Phase 2 AC: Tone & Empathy ≥ 3.5/5, Language Naturalness ≥ 3.5/5 (human review)

| | |
|---|---|
| **Status** | ❌ PENDING — human reviewers required |
| **Server dependency** | None after audio samples captured |
| **Command** | `python3 scripts/validate/mos_check.py --mode capture --tts-host 217.18.55.78 --output-dir evaluation/audio-samples/production/`; then manual human review using rubric in `evaluation/founder-validation-report.md` |

### Phase 2 AC: Audio MOS ≥ 3.5/5 (Veena 3B output)

| | |
|---|---|
| **Status** | ❌ PENDING |
| **Server dependency** | GPU node 217.18.55.78 for audio capture |
| **Command** | (1) `python3 scripts/validate/mos_check.py --mode capture --tts-host 217.18.55.78`; (2) `python3 scripts/validate/mos_check.py --mode dnsmos --audio-dir evaluation/audio-samples/production/` (requires DNSMOS_MODEL_PATH); or (3) crowdsource via `--mode crowdsource` |

### Phase 2 AC: Call Completion Rate ≥ 90%

| | |
|---|---|
| **Status** | ❌ NOT EXECUTED |
| **Server dependency** | CPU + GPU nodes for live calls |
| **Command** | After export: count `"completed": true` in production JSONs vs total; computed automatically by `founder_validation_suite.py` |

### Phase 2 AC: Founder sign-off

| | |
|---|---|
| **Status** | ❌ PENDING |
| **Prerequisite** | All above Phase 2 ACs pass |
| **Action** | Founder reviews `evaluation/founder-validation-report.md`, checks all 9 boxes, signs and dates |

---

## 4. Phase 1 Tooling Audit — Forensic Findings

### Confirmed present (ready for real use)

| Component | Status | Notes |
|-----------|--------|-------|
| `tests/ai_eval/founder_validation_suite.py` | ✅ READY | 72/81 tests pass locally; 9 skip (pydantic v2) |
| `evaluation/call-samples/synthetic/` (5 fixtures) | ✅ READY | All 5 fixtures tested against harness |
| `LawOfAuthorityReplayChecker` | ✅ READY | Skips negotiation_offer turns (design decision) |
| `RBIComplianceChecker` | ✅ READY | Calling hours, frequency, full-name disclosure |
| `NegotiationEnvelopeChecker` | ✅ READY | Floor/ceiling enforcement |
| `IntentAccuracyEvaluator` | ✅ READY (stub DI) | Uses `_FixedIntentEvaluator` in tests; real IntentEngine on production transcripts |
| `scripts/validate/walking_skeleton.py` | ✅ EXISTS | 20-call pipeline validation (STT bypassed) |
| `scripts/validate/latency_validation_phase2.py` | ✅ EXISTS | 100-call latency gate runner |

### Gaps (addressed offline this session)

| Gap | Fix Applied |
|-----|-------------|
| No transcript export tool for real→harness JSON | `scripts/evaluate/export_call_transcript.py` created |
| `scripts/validate/mos_check.py` did not exist | `scripts/validate/mos_check.py` created |
| `evaluation/call-samples/production/` did not exist | Directory + README created |
| `evaluation/audio-samples/production/` did not exist | Directory created |
| `human_intent` label workflow not defined | Documented in production README; manual annotation required |

### Remaining gaps (require human action)

| Gap | Resolution Path |
|-----|----------------|
| `first_audio_ms` not extracted from OTel spans during live calls | `export_call_transcript.py` queries Jaeger via `JAEGER_URL` env var; requires Jaeger to have per-call traces with `PlaybackScheduler` spans |
| `negotiation_offer_inr` not tagged on real call turns | Must be extracted from `ResponsePlan.proposed_amount_minor` in audit_log `event_payload`; `export_call_transcript.py` reads `event_payload.negotiation_offer_inr` |
| `minimum_settlement_pct` not in real CustomerContext schema | Export script uses `ctx.get("minimum_settlement_pct", 0.5)` as default; per-customer value must be stored in Redis working memory or overridden manually |
| DNSMOS model not downloaded | Requires `pip install onnxruntime soundfile` + download from DNS-Challenge repo; set `DNSMOS_MODEL_PATH` env var |
| Human annotators for Tone/Empathy/Language scores | External human reviewers; rubric provided in `evaluation/founder-validation-report.md` |

---

## 5. Server-Return Execution Runbook (Dependency Order)

### Pre-flight (offline — do before servers return)

```bash
# [LOCAL] Verify all offline fixes committed
git status
# Should show: scripts/evaluate/export_call_transcript.py (new)
#              scripts/validate/mos_check.py (new)
#              evaluation/call-samples/production/README.md (new)
#              deployment/gpu/services/stt/server.py (modified)
#              deployment/gpu/services/tts/server.py (modified)
#              src/services/policy_engine/engine.py (modified)
#              implementation/adrs/ADR-004-tts-latency-budget-revision.md (new)
```

### Step 1: CPU node health check

```bash
# [CPU: ssh -i ~/.ssh/voiceos_vm_key root@101.53.137.131]
cd ~/VoiceOS
export POSTGRES_DSN="postgresql://voiceos:voiceos_pw@localhost/voiceos"
export REDIS_URL="redis://:0e539e25b3e5ea96e7434dad38c6029557a9fb3b92f0869e@localhost:6379/0"
bash deployment/cpu/healthcheck.sh
```

### Step 2: GPU node health check

```bash
# [GPU: ssh -i ~/.ssh/temporary.pem ubuntu@217.18.55.78]
nvidia-smi
systemctl status voiceos-stt voiceos-tts voiceos-llm
curl -s http://localhost:8100/health/ready && echo STT-OK
curl -s http://localhost:8200/health/ready && echo TTS-OK
curl -s http://localhost:8000/health/ready && echo LLM-OK
```

### Step 3: Deploy GPU service fixes (STT + TTS)

```bash
# [CPU → GPU copy]
scp -i ~/.ssh/temporary.pem \
  deployment/gpu/services/stt/server.py \
  deployment/gpu/services/tts/server.py \
  ubuntu@217.18.55.78:/opt/voiceos-gpu/services/

# [GPU] Restart services
sudo systemctl restart voiceos-stt voiceos-tts

# [GPU] Verify
sleep 30
curl -s http://localhost:8100/health/ready && echo STT-RESTARTED-OK
curl -s http://localhost:8200/health/ready && echo TTS-RESTARTED-OK

# [GPU] Verify TTS speaker allowlist (should return 422)
curl -s -X POST http://localhost:8200/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "test", "speaker": "mallika"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('PEN-FIX-OK' if d.get('detail','').startswith('Unknown speaker') else 'FAIL')"
```

### Step 4: Deploy CPU service fix (PolicyEngine)

```bash
# [CPU] Pull latest code
cd ~/VoiceOS && git pull

# [CPU] Verify PolicyEngine audit fix
python3 -c "
from src.services.policy_engine.engine import _ALWAYS_AUDITED_OUTCOMES, _AUDITED_OUTCOMES, PolicyEngine
from src.services.policy_engine.decision import PolicyOutcome
print('PERMIT audit fix:', PolicyOutcome.PERMIT not in _ALWAYS_AUDITED_OUTCOMES, '(PERMIT requires matched rules)')
print('DENY always audited:', PolicyOutcome.DENY in _ALWAYS_AUDITED_OUTCOMES)
"
```

### Step 5: Verify audit chain (AC-5)

```bash
# [CPU]
python3 scripts/validate/audit_chain.py
# Expected: PASS — hash chain intact
```

### Step 6: Threat model sign-off (AC-12) — human action required

```
Engineering lead: review docs/security/threat-model.md
Add sign-off block at end of file:
  "Reviewed and approved by: [Name], Date: 2026-07-XX"
```

### Step 7: Sprint-028 AC-1 latency gate — Run D

```bash
# [CPU] Must be run from CPU node for intra-DC path
python3 scripts/validate/latency_validation_phase2.py \
  --gpu-host 217.18.55.78 \
  --calls 100 \
  2>&1 | tee /tmp/latency_run_d.txt

# Gate: first_audio p95 ≤ 1500ms across all 100 calls
# WARNING: L4 thermal throttle expected after ~22 calls.
# If FAIL: escalate to GPU fleet / hardware procurement decision.
# Do NOT mark Sprint-028 complete until p95 ≤ 1500ms verified.
```

### Step 8: API gateway deployment (Sprint-028 AC-4)

```bash
# [CPU — K8s cluster at 101.53.137.131]
# Install API gateway (e.g., Nginx ingress with auth)
kubectl apply -f infra/k8s/api-gateway/

# Verify PEN-001/002/003: STT/LLM/TTS endpoints blocked without auth
# After gateway deployed, re-run pen test:
python3 scripts/validate/pen_test.py --host 101.53.137.131
# PEN-001/002/003 must show "MITIGATED (gateway auth)"
```

### Step 9: Approve ADR-004, fix BenchmarkSuite (Sprint-028 AC-8)

```bash
# After engineering lead signs ADR-004:
# [CPU — local]
# Edit src/services/benchmark/suite.py: TTS_BUDGET_MS = 750
python3 -m pytest tests/unit/services/test_benchmark_suite.py -q
# Must PASS
```

### Step 10: Sprint-028 canary deployment (AC-6)

```bash
# [CPU — K8s cluster]
kubectl apply -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl apply -f infra/k8s/rollouts/conversation-engine-rollout.yaml
kubectl argo rollouts set image conversation-engine \
  conversation-engine=voiceos/conversation-engine:latest
kubectl argo rollouts get rollout conversation-engine --watch
# Verify 5%→25%→50%→100% weight progression without downtime
```

### Step 11: Mark Sprint-028 complete → begin Sprint-029 Phase 2

```bash
# Only after ALL Sprint-028 ACs pass:
# - AC-1 latency ✓  - AC-2 load test ✓  - AC-3 chaos ✓
# - AC-4 security ✓ - AC-5 compliance ✓ - AC-6 canary ✓
# - AC-8 benchmark ✓ - AC-12 threat model ✓

# Update CURRENT_SPRINT.md, DONE.md, BACKLOG.md, CHANGELOG.md, PROJECT_STATUS.md
```

### Step 12: Generate real calls (Sprint-029 Phase 2)

```bash
# [CPU] Generate ≥50 real AI calls via walking skeleton
python3 scripts/validate/walking_skeleton.py \
  --gpu-host 217.18.55.78 \
  --calls 60 \
  2>&1 | tee /tmp/sprint029_walking_skeleton.txt
```

### Step 13: Export transcripts to harness JSON

```bash
# [CPU]
export POSTGRES_DSN="postgresql://voiceos:voiceos_pw@localhost/voiceos"
export REDIS_URL="redis://:0e539e25b3e5ea96e7434dad38c6029557a9fb3b92f0869e@localhost:6379/0"
export JAEGER_URL="http://localhost:16686"

python3 scripts/evaluate/export_call_transcript.py \
  --tenant-id <production-tenant-uuid> \
  --since 2026-07-12 \
  --limit 100 \
  --output evaluation/call-samples/production/

# Count exported transcripts
ls evaluation/call-samples/production/*.json | wc -l
# Must be ≥ 50
```

### Step 14: Human annotation of transcripts

```
For each transcript in evaluation/call-samples/production/:
  Add human_intent label to each customer turn.
  Valid labels: PAYMENT_INTENT | DISPUTE | HARDSHIP | CONSENT_GRANT |
                CONSENT_REVOKE | CALLBACK_REQUEST | ESCALATION | CONFUSION |
                ABUSIVE | OFF_TOPIC
  Leave null for unlabelable turns (excluded from accuracy denominator).
```

### Step 15: Run Phase 1 automated eval suite on production transcripts

```bash
# [LOCAL — no server required]
python3 tests/ai_eval/founder_validation_suite.py \
  --transcripts evaluation/call-samples/production/
# Phase 1 gate: 0 LoA violations, 0 RBI violations, 0 Neg violations, Intent ≥ 90%
```

### Step 16: MOS scoring

```bash
# [LOCAL, requires DNSMOS model]
# Capture audio from GPU TTS:
python3 scripts/validate/mos_check.py \
  --mode capture \
  --tts-host 217.18.55.78 \
  --output evaluation/audio-samples/production/

# Automated MOS:
export DNSMOS_MODEL_PATH=/path/to/dnsmos/sig_bak_ovr.onnx
python3 scripts/validate/mos_check.py \
  --mode dnsmos \
  --audio-dir evaluation/audio-samples/production/
# Gate: MOS ≥ 3.5/5

# OR crowdsource:
python3 scripts/validate/mos_check.py \
  --mode crowdsource \
  --audio-dir evaluation/audio-samples/production/ \
  --output evaluation/mos-annotation-tasks.csv
# Send CSV to human annotators; use --mode score on completed CSV
```

### Step 17: Populate founder-validation-report.md and obtain sign-off

```bash
# Update evaluation/founder-validation-report.md:
# - Fill Phase 1 table with actual results from Step 15
# - Fill Phase 2 scores from human reviewers + MOS + OTel traces
# - Obtain founder sign-off on all 9 checkboxes
```

---

## 6. Copy-Paste Commands by Environment

### Local (Termux)

```bash
# Run Phase 1 against synthetic fixtures (no server)
python3 tests/ai_eval/founder_validation_suite.py \
  --transcripts evaluation/call-samples/synthetic/

# Run unit tests
python3 -m pytest tests/unit/ai_eval/test_founder_validation_suite.py \
  -q --override-ini="addopts=" --noconftest

# Run Phase 1 against production transcripts (after export)
python3 tests/ai_eval/founder_validation_suite.py \
  --transcripts evaluation/call-samples/production/
```

### CPU Node (ssh -i ~/.ssh/voiceos_vm_key root@101.53.137.131)

```bash
# Health check
bash deployment/cpu/healthcheck.sh

# Walking skeleton — generates ≥50 calls for Phase 2
python3 scripts/validate/walking_skeleton.py --gpu-host 217.18.55.78 --calls 60

# Latency gate Run D (Sprint-028 AC-1)
python3 scripts/validate/latency_validation_phase2.py --gpu-host 217.18.55.78 --calls 100 2>&1 | tee /tmp/latency_run_d.txt

# Audit chain check (Sprint-028 AC-5)
python3 scripts/validate/audit_chain.py

# Pen test (Sprint-028 AC-4)
python3 scripts/validate/pen_test.py --host 101.53.137.131

# Export transcripts (Sprint-029 Phase 2)
export POSTGRES_DSN="postgresql://voiceos:voiceos_pw@localhost/voiceos"
export REDIS_URL="redis://:0e539e25b3e5ea96e7434dad38c6029557a9fb3b92f0869e@localhost:6379/0"
export JAEGER_URL="http://localhost:16686"
python3 scripts/evaluate/export_call_transcript.py \
  --tenant-id <uuid> --since 2026-07-12 --limit 100 \
  --output evaluation/call-samples/production/
```

### GPU Node (ssh -i ~/.ssh/temporary.pem ubuntu@217.18.55.78)

```bash
# Health check
nvidia-smi
curl -s http://localhost:8100/health/ready && echo STT-OK
curl -s http://localhost:8200/health/ready && echo TTS-OK
curl -s http://localhost:8000/health/ready && echo LLM-OK

# Deploy STT/TTS fixes (run from CPU, copy to GPU)
scp -i ~/.ssh/temporary.pem deployment/gpu/services/stt/server.py ubuntu@217.18.55.78:/opt/voiceos-gpu/services/
scp -i ~/.ssh/temporary.pem deployment/gpu/services/tts/server.py ubuntu@217.18.55.78:/opt/voiceos-gpu/services/
# Then on GPU:
sudo systemctl restart voiceos-stt voiceos-tts
sleep 30
curl -s http://localhost:8100/health/ready && curl -s http://localhost:8200/health/ready

# Verify speaker allowlist fix (PEN-005-007)
curl -s -X POST http://localhost:8200/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "test", "speaker": "invalid_speaker"}' | python3 -m json.tool

# Capture MOS audio
python3 scripts/validate/mos_check.py --mode capture --tts-host localhost \
  --output-dir evaluation/audio-samples/production/
```

---

## 7. Sprint-028 Overall Status

| AC | Description | Status | Server-Dep |
|----|-------------|--------|-----------|
| AC-1 | Latency p95 ≤ 1,500ms | ❌ FAIL (thermal throttle) | GPU+CPU |
| AC-2 | 500-user load test | ❌ NOT EXECUTED | GPU fleet |
| AC-3 | GPU chaos failover | ❌ BLOCKED | GPU fleet |
| AC-4 | Security/pen test sign-off | ⚠️ CONDITIONAL | CPU K8s |
| AC-5 | Compliance audit | ⚠️ PARTIAL → FIXED offline | CPU |
| AC-6 | Canary deployment | ❌ NOT EXECUTED | CPU K8s |
| AC-8 | BenchmarkSuite PASS | ❌ FAIL → ADR-004 proposed | None (ADR pending) |
| AC-12 | Threat model signed off | ❌ NOT SIGNED | None (human action) |

**Sprint-028 is INCOMPLETE. It cannot be marked complete until all 8 ACs above pass.**

**Minimum required for Sprint-029 Phase 2 to start:** Sprint-028 complete (M-7 milestone).

---

## 8. Sprint-029 Overall Status

| Phase | Status |
|-------|--------|
| Phase 1 — automated checks (offline) | ✅ COMPLETE (72/81 tests, 9 skip pydantic v2) |
| Phase 2 — human review + production traces | ❌ PENDING (all dimensions) |
| Founder sign-off | ❌ PENDING |

**Sprint-029 can be declared complete only after Sprint-028 is complete + all Phase 2 gates pass + founder signs off.**

---

## 9. Critical Blockers Summary

| Blocker | Resolution |
|---------|-----------|
| L4 thermal throttle (TT-025) | Hardware: higher-TDP GPU (A10G/H100) or GPU fleet (multiple L4s) |
| GPU fleet not available (TT-026, AC-3) | Infrastructure procurement: second GPU node + networking |
| API gateway not deployed (AC-4 PEN-001-003) | K8s deployment sprint item |
| ADR-004 not approved (AC-8) | Engineering lead sign-off |
| Threat model not signed off (AC-12) | Engineering lead sign-off |
| Canary infra not deployed (AC-6) | Argo Rollouts in K8s |
| Human annotators for Phase 2 | External reviewer coordination |
| DNSMOS model not downloaded | `pip install onnxruntime soundfile`; download model |
