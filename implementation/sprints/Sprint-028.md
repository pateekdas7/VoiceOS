# Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy

**Epic:** E7 — Production Alpha  
**Status:** ⬜ Pending  
**Depends on:** Sprint-026, Sprint-027, Sprint-022, Sprint-023  
**Blocks:** Sprint-029  
**Milestone:** Production Alpha — M-7  

---

## Objective

Execute all production-readiness gates: latency validation (first-audio p95 ≤ 1.5s), load testing at target concurrency, chaos engineering, external security penetration testing, automated compliance validation (RBI/DPDP), and deploy the production alpha via canary rollout to 100% of traffic.

---

## Architecture References

- Volume 1: Ch23 (Latency Budget — per-stage targets)
- Volume 3: Ch19 (Performance Engineering — reusable profiling harness, per-stage benchmark fixtures, systematic optimization methodology, regression-detection gates)
- Volume 4: Ch20 (Threat Modeling — STRIDE analysis, data-flow diagram with threats annotated, threat registry, attack-surface mapping), Ch21 (Penetration Testing)
- Volume 7: Ch4 (Deployment Strategy — canary 5%→25%→50%→100%), Ch7 (Monitoring), Ch15 (Performance Validation), Ch20 (Business Continuity)
- DocSuite-09: Deployment Cookbook
- DocSuite-10: AI Evaluation Handbook

---

## Deliverables

### 1. Latency Validation (`evaluation/latency-validation/`)

**Procedure:**
- Use production infrastructure (warm GPU, real AI models)
- Inject 100 test calls over 30 minutes
- Instrument every stage with OpenTelemetry spans
- Record per-stage p50/p95/p99: Media GW → ASM → Preprocessing → VAD → STT → CIL → LLM (TTFT) → TTS (first clause) → Playback start
- Assert first-audio p95 ≤ 1.5s (fail if exceeded; identify bottleneck and fix before proceeding)
- Per-stage budget (from V1 Ch23): endpoint 120ms + STT 300ms + context+prompt 90ms + LLM TTFT 350ms + validate 40ms + TTS 250ms + resample 30ms = ~1060ms p50 headroom
- Output: `evaluation/latency-validation/latency-report.md`

**Action on failure:** Identify stage exceeding budget → optimize (model size, batching, caching) → re-run. Do NOT proceed to canary deploy until p95 ≤ 1.5s.

### 2. Load Testing (`evaluation/load-testing/`)

**Tool:** Locust or k6

**Scenario:**
- Ramp to 500 concurrent calls over 10 minutes
- Hold for 30 minutes at 500 concurrent
- Ramp down over 5 minutes
- Assert: first-audio p95 ≤ 1.65s (10% degradation budget at load), GPU utilization ≤ 0.80, error rate < 0.1%, no OOM events, no circuit breaker opens (sustained)

**Output:** `evaluation/load-testing/load-test-report.md`

### 3. Chaos Engineering (`evaluation/chaos/`)

**Scenarios (using Chaos Mesh or manual Kubernetes disruption):**
1. Kill GPU node-0 during 200 concurrent calls → assert graceful failover to GPU node-1, ≤ 5 calls dropped
2. Kill Redis primary during calls → assert degraded-mode continuation (no data loss, calls complete with possible extra latency)
3. 20% packet loss on RTP path → assert PLC compensates, STT accuracy within 5% of baseline
4. Kill Postgres primary → assert crash recovery, standby promotes, no PTP duplication
5. Kill conversation-engine pod during active call → assert session state recovered from Redis snapshot

**Output:** `evaluation/chaos/chaos-engineering-report.md`

### 4. Security Penetration Testing (`evaluation/security/`)

**Scope (external pen test or internal using OWASP methodology):**
- API: SQL injection, command injection, IDOR (tenant isolation bypass), API key brute-force, JWT tampering
- Prompt injection: adversarial customer utterances attempting to override agent behavior
- Tenant isolation: cross-tenant data access via API, via event bus, via shared Redis namespace
- Auth bypass: mTLS certificate spoofing, JWT algorithm confusion
- DoS: rate limiting effectiveness, WebSocket flood

**Exit criteria:** ZERO critical findings, ZERO high findings that are exploitable in production. Medium findings must have a documented remediation plan and timeline.

**Output:** `evaluation/security/pen-test-report.md` + remediation log

### 5. Compliance Validation (`evaluation/compliance/`)

**Automated test suite (run against staging environment):**
- RBI calling hours: 50 test calls attempted outside 08:00–20:00 → all blocked
- RBI frequency: customer with 3 calls today → 4th call blocked
- DPDP consent gate: 10 test customers without consent → all calls blocked
- Recording disclosure: verify first utterance includes disclosure phrase
- Audit completeness: run 100 calls → verify audit trail has all required event types
- Data retention: verify no data exists past configured retention period (test with artificially aged records)
- Right to erasure: trigger erasure → verify DataErasureCertificate created, PII inaccessible

**Output:** `evaluation/compliance/compliance-validation-report.md`

### 7. Performance Engineering Framework (`src/libs/performance-engineering/`) (V3 Ch19)

```
src/libs/performance-engineering/
├── __init__.py
├── profiler.py             (ContinuousProfiler: per-stage latency profiling harness, always-on in staging)
├── benchmarks.py           (BenchmarkSuite: per-stage fixture-driven benchmarks with baseline tracking)
├── regression_gate.py      (RegressionDetector: fails CI if any stage exceeds its p95 budget by >10%)
└── optimization.py         (OptimizationPlaybook: documented procedures for each stage bottleneck)
```

**ContinuousProfiler:**
- Wraps each pipeline stage with automated timing capture
- Stores p50/p95/p99 per stage per day in `performance_baselines` Postgres table
- `profile_stage(stage_name: str, fn: Callable) -> TimingResult` — decorator for instrumented measurement

**BenchmarkSuite:**
- Per-stage baseline benchmarks run in CI: STT ≤ 300ms, CIL ≤ 120ms, LLM TTFT ≤ 350ms, TTS first-clause ≤ 250ms
- `run_benchmarks(environment: str) -> BenchmarkReport` — executes all stage benchmarks and compares against baselines

**RegressionDetector:**
- CI gate: if any stage p95 is more than 10% above its registered baseline → CI fails
- Prevents performance regressions from merging silently

### 8. Threat Model (`docs/security/threat-model.md`) (V4 Ch20)

Produce the living threat model document using STRIDE methodology:

**`docs/security/`:**
- `threat-model.md` — master threat model document: system boundaries, data flows, trust zones
- `dfd-level0.svg` — Level 0 DFD: VoiceOS system as a black box with external entities
- `dfd-level1.svg` — Level 1 DFD: major components and data flows between them, threats annotated
- `threat-registry.md` — structured threat list: each entry has ID, STRIDE category, component, description, current controls, residual risk, acceptance status

**STRIDE threat categories covered:**
- **S**poofing: caller identity, JWT forgery, SIP caller-ID spoofing
- **T**ampering: audit log modification, event stream tampering, prompt injection
- **R**epudiation: untraceable AI decisions, audit log gaps
- **I**nformation Disclosure: PII in logs, cross-tenant data leakage, model extraction
- **D**oS: GPU VRAM exhaustion, RTP flood, LLM token bomb
- **E**levation of Privilege: RBAC bypass, tenant privilege escalation

**Attack surface mapping:**
- External API surface (REST, WebSocket, WebRTC)
- Telephony surface (SIP, PSTN)
- LLM surface (prompt injection, model extraction)
- Admin surface (admin portal, internal APIs)

### 6. Production Alpha Deployment

**Canary rollout procedure (V7 Ch4):**
1. Deploy to 5% of traffic (feature flag or Argo Rollouts)
2. Hold 30 minutes: monitor SLO dashboards, error rate, latency
3. Auto-rollback trigger: if error rate > 1% OR first-audio p95 > 2s → automatic rollback
4. If passing: promote to 25% → hold 1 hour → promote to 50% → hold 1 hour → promote to 100%
5. Notify on-call team at each promotion step

**Output:** `evaluation/production-alpha-report.md` (all gate results + canary deployment log)

---

## Files Expected to Change

**New:** `evaluation/latency-validation/`, `evaluation/load-testing/`, `evaluation/chaos/`, `evaluation/security/`, `evaluation/compliance/`, `evaluation/production-alpha-report.md`  
**New:** Load test scripts in `tests/load/`  
**New:** Chaos test scripts in `tests/chaos/`  
**New:** Compliance test suite in `tests/compliance/`  
**New:** `src/libs/performance-engineering/` (ContinuousProfiler, BenchmarkSuite, RegressionDetector)  
**New:** `docs/security/threat-model.md`, `docs/security/threat-registry.md`, `docs/security/dfd-level0.svg`, `docs/security/dfd-level1.svg`

---

## Acceptance Criteria

- [ ] First-audio p95 ≤ 1.5s on latency validation run (100 calls, production AI models)
- [ ] Load test: p95 ≤ 1.65s at 500 concurrent calls; GPU utilization ≤ 0.80; error rate < 0.1%
- [ ] Chaos: GPU failure → ≤ 5 calls dropped; Redis failure → calls continue; Postgres failure → no PTP duplication
- [ ] Security: ZERO critical findings; ZERO exploitable high findings; pen test report signed off
- [ ] Compliance: 100% of RBI/DPDP test scenarios pass
- [ ] Canary: 5%→25%→50%→100% completed without auto-rollback trigger
- [ ] All evaluation reports committed to `evaluation/`
- [ ] `BenchmarkSuite.run_benchmarks()` passes for all stages (all p95 values within budget)
- [ ] `RegressionDetector` CI gate is wired into `.github/workflows/` and fails correctly on simulated regression
- [ ] `docs/security/threat-model.md` exists with all 6 STRIDE categories covered
- [ ] `docs/security/threat-registry.md` has ≥ 20 threat entries with controls documented
- [ ] Threat model reviewed and signed off by engineering lead

---

## Definition of Done

- [ ] All AC items checked
- [ ] All evaluation reports completed and committed
- [ ] Production alpha live at 100% traffic
- [ ] SLO dashboards showing live data
- [ ] **Milestone M-7 (Production Alpha) criteria verified**
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-029

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** The performance engineering library (ContinuousProfiler, BenchmarkSuite, RegressionDetector) is implemented and unit-tested against fixture timing data. Compliance test suite is wired with mock policy responses. Load/chaos scripts are written and validated syntactically. Threat model and pen-test scope documents are produced.

### Files Created

- `src/libs/performance-engineering/__init__.py`, `profiler.py`, `benchmarks.py`, `regression_gate.py`, `optimization.py`
- `tests/load/locustfile.py` (or `k6-script.js`) — load test scripts
- `tests/chaos/chaos-scenarios.py` — chaos injection scripts (Chaos Mesh or kubectl disruption)
- `tests/compliance/test_rbi_compliance.py`, `test_dpdp_compliance.py`
- `evaluation/latency-validation/latency-report.md` (template)
- `evaluation/load-testing/load-test-report.md` (template)
- `evaluation/chaos/chaos-engineering-report.md` (template)
- `evaluation/security/pen-test-report.md` (template + scope)
- `evaluation/compliance/compliance-validation-report.md` (template)
- `evaluation/production-alpha-report.md` (template)
- `docs/security/threat-model.md`, `docs/security/threat-registry.md`, `docs/security/dfd-level0.svg`, `docs/security/dfd-level1.svg`
- `tests/unit/libs/test_performance_engineering.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Stage timing | Fixed-duration fixtures | `ContinuousProfiler` and `BenchmarkSuite` use deterministic fixture timings |
| Postgres (baselines) | `TestPostgres` Docker fixture | Baseline storage for regression detection |
| PolicyEngine | `FakePolicyEngine` | Compliance test suite uses mock policy responses |
| Production AI models | Not used in Phase 1 | Load/latency tests require real GPU — Phase 2 only |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Performance engineering unit tests | `pytest tests/unit/libs/test_performance_engineering.py` | `BenchmarkSuite` passes for fixture data; `RegressionDetector` fails on simulated 15% regression |
| Compliance suite (mock) | `pytest tests/compliance/` | All RBI/DPDP scenarios pass with FakePolicyEngine |
| Threat model existence | `ls docs/security/` | `threat-model.md`, `threat-registry.md`, `dfd-level0.svg`, `dfd-level1.svg` all present |
| Threat registry completeness | `grep -c "^|" docs/security/threat-registry.md` | ≥ 22 rows (≥ 20 threat entries + header rows) |
| Coverage | `pytest --cov=src/libs/performance-engineering --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `BenchmarkSuite.run_benchmarks()`: fixture data within all stage budgets → PASS
- `RegressionDetector`: fixture with STT p95 at 115% of budget → CI gate FAILS (correct behavior)
- `RegressionDetector`: fixture within budget → PASS
- Threat registry: ≥ 20 threats with STRIDE category, component, controls, residual risk
- All 6 STRIDE categories covered in `threat-model.md`
- Compliance test suite: 50 outside-hours attempts → all blocked (mock); consent-denied customers → all blocked (mock)

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely. All evaluation runs require the full production infrastructure (CPU node + GPU node + real AI models).

### CPU Node

**Services deployed/integrated this sprint:**

| Action | Target | Why |
|---|---|---|
| Deploy ContinuousProfiler | Wired into all hot-path services | Per-stage p95 tracking in staging |
| Wire RegressionDetector | `.github/workflows/ci.yml` | Performance regression CI gate |
| Production alpha canary rollout | All services → 100% traffic | Milestone M-7 gate |

**Previously deployed services that remain running:**
- All Sprint-004–027 services

**Deployment procedure (validation sequence):**
1. Run latency validation: 100 test calls → record per-stage p50/p95/p99 → assert first-audio p95 ≤ 1.5s
2. Run load test: Locust/k6 ramp to 500 concurrent calls → hold 30 min → record results
3. Run chaos scenarios (1–5) → record outcomes → assert all pass gates
4. Run compliance validation suite against staging: all 50 RBI + DPDP scenarios
5. Conduct / review pen test: ZERO critical findings confirmed
6. Canary rollout: 5% → 25% → 50% → 100% with 30–60 min holds at each step

**Health checks during canary:**
- Grafana SLO dashboard: first-audio p95 < 2s (auto-rollback trigger if exceeded)
- Error rate: < 1% at all traffic percentages
- GPU utilization: ≤ 0.80 at 500 concurrent calls

**Rollback procedure:**
- Automatic: Argo Rollouts or feature flag rollback if p95 > 2s OR error rate > 1%
- Manual: `argocd app rollback voiceos-platform` or `helm rollback voiceos-platform`

### GPU Node

**GPU validation under load:**

| Validation | Target |
|---|---|
| Latency at baseline (100 calls) | STT TTFW ≤ 500ms; LLM TTFT ≤ 500ms; TTS first-clause ≤ 300ms |
| Latency under load (500 concurrent) | First-audio p95 ≤ 1.65s (10% degradation budget) |
| GPU utilization under load | ≤ 0.80 (fleet average; no node at 100%) |
| GPU node failure chaos | Kill GPU node-0 → ≤ 5 calls dropped; node-1 absorbs load |
| Streaming validation | LLM tokens stream to TTS without buffering |

GPU models remain as deployed in Sprint-009: Whisper Large-v3 Turbo FP8, Qwen2.5-7B-Instruct-FP8 via vLLM, Veena TTS FP16.

### Infrastructure Validation

**CPU Validation:**
- Latency report: `evaluation/latency-validation/latency-report.md` — first-audio p95 ≤ 1.5s confirmed
- Load report: `evaluation/load-testing/load-test-report.md` — all thresholds met at 500 concurrent
- Chaos report: `evaluation/chaos/chaos-engineering-report.md` — all 5 scenarios pass their gates
- Compliance report: `evaluation/compliance/compliance-validation-report.md` — 100% RBI/DPDP pass

**GPU Validation:**
- GPU utilization logged every 60s during load test; peak ≤ 0.80
- No GPU OOM events in Kubernetes events during load test

**Networking Validation:**
- RTP path validated under 20% packet loss (chaos scenario 3): STT accuracy within 5% of baseline
- Canary traffic split: Prometheus confirms correct percentage routing at each stage

### Regression Validation

- All sprints' regression suites pass on production alpha
- Pen test: ZERO critical findings; medium findings have documented remediation plan
- Performance: `BenchmarkSuite.run_benchmarks()` passes all stage budgets
- `RegressionDetector` wired in CI: simulated 15% regression on test branch → CI fails

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] ContinuousProfiler, BenchmarkSuite, RegressionDetector implemented and unit-tested
- [ ] Compliance test suite written with mock policy responses
- [ ] Load test and chaos scripts written and validated
- [ ] Threat model (`threat-model.md`, `threat-registry.md`, DFDs) complete with ≥ 20 threats
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] RegressionDetector CI gate wired into `ci.yml`
- [ ] Coverage ≥ 85% (performance engineering library)
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] Latency validation: first-audio p95 ≤ 1.5s (100 calls, production AI)
- [ ] Load test: p95 ≤ 1.65s at 500 concurrent; GPU utilization ≤ 0.80; error rate < 0.1%
- [ ] Chaos: all 5 scenarios pass their gates
- [ ] Pen test: ZERO critical findings; ZERO exploitable high findings
- [ ] Compliance: 100% RBI/DPDP scenarios pass on staging
- [ ] Canary rollout: 5%→25%→50%→100% completed without auto-rollback trigger
- [ ] All evaluation reports committed to `evaluation/`
- [ ] Milestone M-7 (Production Alpha) verified
- [ ] Deployment remains active as baseline for Sprint-029

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-028 is the production alpha — this snapshot must be sufficient to rebuild the entire production environment.

### CPU_NODE_STATE.md — Updates This Sprint

- Update §8.1 Services table: mark all services with current production image tag (e.g., `voiceos/service:production-alpha`)
- Add `ContinuousProfiler` and `RegressionDetector` as embedded library in all hot-path services (note in §8.1)
- Update §13 Observability: add `performance_baselines` Postgres table to schema (§12); note RegressionDetector CI gate wired
- Update §8.3 Startup Order: full production startup sequence documented end-to-end
- Add §15 Rollback Information: canary rollback commands, Argo Rollouts rollback procedure
- Note: first-audio p95 ≤ 1.5s confirmed at 100% traffic — add to §14 Verification Commands

### GPU_NODE_STATE.md — Updates This Sprint

- Update §8: confirm all GPU models running at 100% production traffic
- Update §15 Latency Validation Commands: fill in production-validated latency commands from Sprint-028 latency report
- Add load-test VRAM utilization result: GPU utilization ≤ 0.80 at 500 concurrent calls
- Update `GPU_NODE_STATE.md` last_updated: Sprint-028

### Scripts to Update

| File | Change |
|---|---|
| `deployment/gpu/model_manifest.yaml` | Add `post_restore_validation.commands` filled in with actual latency validation scripts |
| `deployment/cpu/restore.sh` | Add: `RegressionDetector` CI gate wired into restore validation step |
| `deployment/gpu/restore.sh` | Add: latency validation step after GPU services ready |
| `deployment/gpu/healthcheck.sh` | Add load-test utilization threshold check |

### DR Validation

**Production alpha rebuild test (full end-to-end):**
```bash
# CPU node
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh

# GPU node (separate server)
sudo bash deployment/gpu/bootstrap.sh
bash deployment/gpu/restore.sh

# Verify latency after rebuild (must match production alpha result)
python3 scripts/validate/walking_skeleton.py --calls 20
# Expected: first-audio p95 ≤ 1.5s
```

**Security validation after rebuild:**
```bash
# API key auth still working
curl -sf -H "X-API-Key: $TEST_API_KEY" http://<api-platform>/v1/customers/test-001
# mTLS still enforced
openssl s_client -connect <service-host>:<port> -tls1_3 -cert /opt/voiceos/certs/client.crt
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Plus compliance suite:
pytest tests/compliance/ -v
# Expected: 100% RBI/DPDP scenarios pass on rebuilt infrastructure
```
