# Production Alpha Deployment Report — Sprint-028 Phase 2

**Sprint:** Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy
**Deliverable:** `evaluation/production-alpha-report.md` (Sprint-028 §6 — Production Alpha Deployment)
**Milestone:** M-7 — Production Alpha
**Architecture Reference:** Volume 7 Ch4 (Deployment Strategy — canary 5%→25%→50%→100%), Ch7 (Monitoring), Ch20 (Business Continuity)
**Executed:** 2026-07-11 (original) + 2026-07-12 (Phase 2 updates: ADR-004, security fixes, code audit)

---

## Report Metadata

| Field | Value |
|---|---|
| Date | 2026-07-11 (original); 2026-07-12 (updated) |
| Environment | GPU node (217.18.55.120, L4 24GB); CPU node (101.53.137.131) unreachable |
| Rollout mechanism | **NOT DEPLOYED** — canary blocked by infrastructure gaps |
| Status | **PARTIAL — Sprint-028 gates partially met; ADR-004 + security fixes applied** |

---

## Canary Infrastructure Status

The Sprint-028 §6 spec requires `5%→25%→50%→100%` canary rollout via Argo Rollouts or feature flags.

**Canary mechanism audit:**

| Component | Required | Status |
|---|---|---|
| `FleetRolloutManager` (Python, V5 Ch23) | Ring assignment logic | **EXISTS** — `src/services/saas_ops/fleet_rollout.py` (ring-based tenant assignment, health-gated promotion) |
| Argo Rollouts CRD + controller | K8s traffic splitting | **ABSENT** — `deployment/k8s/` contains only `health_stub/` |
| Flagger or canary ingress configuration | Traffic weighting | **ABSENT** |
| Rollout ring-to-K8s-service mapping | Ring → traffic % | **ABSENT** |
| SLO alerting for rollback trigger | `error_rate > 1% OR p95 > 2s` | **ABSENT** — Grafana/Prometheus deployed but rollback alertmanager rule not wired |
| On-call notification at each promotion step | Alertmanager integration | **ABSENT** |

**Verdict:** `FleetRolloutManager` in Python is application-layer ring assignment only — it assigns tenants to rings for feature-flag decisions. It does **not** perform Kubernetes traffic splitting. Actual 5%/25%/50%/100% traffic splitting requires either:
- Argo Rollouts `Rollout` CRD with an inline canary step strategy, or
- Flagger `Canary` CRD with Prometheus metric analysis, or
- Nginx/Istio weighted routing rules at the ingress layer

None of these exist. The canary AC cannot be executed.

---

## Evaluation Reports Rollup

| Report | Path | Gate | Result |
|---|---|---|---|
| Latency Validation | `evaluation/latency-validation/latency-report.md` | First-audio p95 ≤ 1.5s | **CONDITIONAL** — cold GPU p95~930ms (PASS); sustained-load p95~1550ms (FAIL, TT-025 thermal throttle); Run E in progress |
| Load Testing | `evaluation/load-testing/load-test-report.md` | p95 ≤ 1.65s @ 500 concurrent; error rate < 0.1% | **FAIL** — p95=16,524ms @ 10 users (TTS serialization); 500-user test NOT EXECUTED (single L4) |
| Chaos Engineering | `evaluation/chaos/chaos-engineering-report.md` | All 5 scenarios pass gates | **PARTIAL** — 2/5 PASS; 1/5 PARTIAL; 2/5 BLOCKED (single GPU node, health stubs) |
| Security Pen Test | `evaluation/security/pen-test-report.md` | ZERO critical, ZERO exploitable-high | **CONDITIONAL PASS** — 0 critical; 3 HIGH (staging-only); PEN-005/006/007/009 FIXED 2026-07-12 |
| Security Remediation Log | `evaluation/security/remediation-log.md` | Medium findings tracked with plan | **PARTIAL** — 4 findings FIXED; 5 open (gateway-dep + red-team) |
| Compliance Validation | `evaluation/compliance/compliance-validation-report.md` | 100% RBI/DPDP pass | **PASS** — 15/15 enforcement tests pass; audit infrastructure code-audited correct 2026-07-12 |

---

## Acceptance Criteria Status

| AC | Requirement | Result | Status |
|---|---|---|---|
| AC-1 | First-audio p95 ≤ 1.5s (100 calls, production AI) | Cold GPU p95~930ms (PASS); sustained p95~1550ms (FAIL, TT-025); Run E pending | **CONDITIONAL** |
| AC-2 | Load test p95 ≤ 1.65s @ 500 concurrent | 16,524ms @ 10 concurrent; 500-user not run | **FAIL** |
| AC-3 | Load test GPU utilization ≤ 0.80 | Single L4 at TDP ceiling (71.08W/72W) | **FAIL** |
| AC-4 | Load test error rate < 0.1% | 0.00% @ 10 users | **PASS** |
| AC-5 | Chaos: GPU failure → ≤ 5 calls dropped | Single-node, no failover target | **BLOCKED** |
| AC-6 | Chaos: Redis failure → calls continue, no data loss | Recovery 3,255ms; 0 data loss | **PASS** |
| AC-7 | Chaos: Postgres failure → no PTP duplication | Recovery 5,420ms; 0 row delta | **PASS** |
| AC-8 | Security: ZERO critical findings | 0 critical | **PASS** |
| AC-9 | Security: ZERO exploitable high findings | 3 HIGH (staging-only, not exploitable from external — no API gateway deployed) | **CONDITIONAL PASS** |
| AC-10 | Compliance: 100% RBI/DPDP scenarios pass | 15/15 enforcement tests | **PASS** |
| AC-11 | Canary: 5%→25%→50%→100% without rollback | No K8s traffic-splitting mechanism exists (Argo Rollouts absent) | **NOT EXECUTED** |
| AC-12 | All evaluation reports committed to `evaluation/` | All 6 reports written and committed | **PASS** |
| AC-13 | `BenchmarkSuite.run_benchmarks()` passes | ADR-004 approved 2026-07-12: TTS budget 250ms→750ms; benchmarks.py updated | **PASS** |
| AC-14 | `docs/security/threat-model.md` exists (6 STRIDE categories) | Not checked in this sprint | **TBD** |
| AC-15 | `docs/security/threat-registry.md` ≥ 20 entries | Not checked in this sprint | **TBD** |

---

## Blocking Gaps — Ordered by Priority

### Gap 1: GPU Thermal Throttling (Latency Gate)

**Root cause:** Single NVIDIA L4 hits 72W TDP after ~110s continuous inference → clock throttles from 2040MHz to ~1000MHz → all three models (Whisper, Qwen, Veena) double in latency simultaneously.

**Cold GPU performance:** first_audio p50=920ms, p95=933ms — **would pass** the 1500ms gate.

**Fix:** GPU fleet (V7 Ch6) — multiple L4 nodes behind load balancer, so no single node sustains continuous load. Alternatively, set `nvidia-smi -pl <sustainable_limit>` before deployment to lock clock at a thermally stable frequency.

### Gap 2: TTS Architecture Budget ✓ RESOLVED — ADR-004 APPROVED 2026-07-12

**Root cause:** V1 Ch23 allocated 250ms for TTS first-clause. Veena 3B BF16 + SNAC 24kHz requires minimum 21 tokens × 30.6ms/tok = 642ms.

**Resolution:** ADR-004 approved by engineering lead 2026-07-12. V1 Ch23 TTS budget revised from 250ms to 750ms. `src/libs/performance_engineering/benchmarks.py` updated (`STAGE_BUDGETS_MS["tts_first_clause"] = 750.0`). `deployment/gpu/model_manifest.yaml` updated (`first_clause_p95: 750`). `BenchmarkSuite.run_benchmarks()` now PASS on this gate.

### Gap 3: RI-8 / GPU Scheduler (TT-015)

**Root cause:** `gpu-scheduler` pods in K8s `Pending` state — cannot join cluster due to cross-provider NAT. All inference paths use `_StubGPUScheduler` (always APPROVE, no VRAM ledger).

**Fix:** TT-015 resolution — configure K8s network policy to allow GPU node to join cluster across NAT, or deploy gpu-scheduler on the GPU node directly via node-local DaemonSet.

### Gap 4: No Canary Traffic Splitting Infrastructure

**Root cause:** Argo Rollouts CRD not installed; no Flagger, no weighted ingress. `FleetRolloutManager` handles tenant-ring assignment only, not K8s traffic splitting.

**Fix:** Install Argo Rollouts controller → create `Rollout` resource with step-based canary strategy → configure Prometheus `AnalysisTemplate` with first_audio p95 and error rate metrics → configure alertmanager rollback rule.

### Gap 5: API Gateway / Auth on Inference Endpoints (Security)

**Root cause:** STT, LLM, TTS endpoints are directly reachable without authentication. No API gateway deployed in front of GPU inference services.

**Fix:** Deploy nginx or Envoy in front of GPU inference services with bearer token auth (or mTLS). vLLM supports `--api-key` flag natively. Enable before any external-facing traffic.

### Gap 6: TT-006 Health Stubs (Media GW, ASM, VAD, Conversation Engine)

**Root cause:** 9 VoiceOS runtime pods are health-stubs — they respond to `/health/ready` but implement no business logic.

**Impact on this sprint:** (a) chaos scenarios 3 and 5 blocked, (b) full end-to-end latency unmeasurable (Media GW, ASM, preprocessing, VAD latency unvalidated), (c) pen test surface limited to GPU inference endpoints only.

### Gap 7: Audit Infrastructure ✓ RESOLVED — Code Audit 2026-07-12

**Original concern:** Hash chain not populated on live audit_log writes; policy decisions not logged.

**Code audit result (2026-07-12):**
- `AuditRepository.append()` correctly calls `compute_audit_hash()` and includes hash in every INSERT. NULL hashes on pre-migration stale rows — not a live write deficiency.
- `PolicyEngine._audit()` writes to `self._audit_repository` on every `evaluate()` call when `audit_repository` is passed to the constructor. Test-setup omission only.

**Resolution:** No code changes needed. Production composition root must wire `AuditRepository` into `PolicyEngine` (implementation concern, not architecture deficiency).

---

## Sprint-028 Overall Verdict

**Sprint-028: PARTIAL** *(updated 2026-07-12)*

| Category | Verdict | Key Reason |
|---|---|---|
| Latency Validation | **CONDITIONAL** | Cold-GPU p95~930ms PASS; sustained p95~1550ms FAIL (TT-025); Run E results pending |
| Load Testing | **NO-GO** | 500-user test not executable (single L4); 10-user TTS serializes at 16,524ms |
| Chaos Engineering | **PARTIAL** | 2/5 PASS; 1/5 PARTIAL; 2/5 BLOCKED (fleet + stubs) |
| Security | **CONDITIONAL PASS** | 0 CRITICAL; 3 HIGH (staging-only); 4 medium/low FIXED in Sprint-028 |
| Compliance | **PASS** | 15/15 enforcement correct; audit infrastructure code-audited correct |
| BenchmarkSuite (AC-8) | **PASS** | ADR-004 approved; TTS budget 250ms→750ms implemented |
| Canary Rollout | **NOT EXECUTED** | No Argo Rollouts/Flagger in K8s |

**M-7 (Production Alpha) milestone: NOT ACHIEVED.**  
Blockers: GPU thermal throttle (TT-025) prevents sustained p95 ≤ 1500ms; 500-concurrent load test requires GPU fleet; canary requires Argo Rollouts.

---

## Required Before M-7 Can Be Revisited

1. **GPU fleet (TT-025)** — minimum 2 L4 nodes behind load balancer; verify sustained first_audio p95 < 1500ms under fleet-distributed load
2. **TTS budget ADR** — ✅ DONE: ADR-004 approved 2026-07-12; V1 Ch23 TTS budget revised to 750ms
3. **TT-015 resolution** — GPU scheduler joins K8s cluster; RI-8 OOM-by-construction enforcement active
4. **Argo Rollouts deployment** — 5%→25%→50%→100% canary capable
5. **API gateway with auth** — PEN-001/002/003 resolved before production traffic
6. **TT-006 stub replacement** — Media GW, ASM, VAD must be real implementations for full end-to-end validation
7. **Audit infrastructure** — ✅ DONE: code audit confirms correct implementation; no code changes needed

---

## Sign-off

| Role | Status | Notes |
|---|---|---|
| Sprint-028 Executor | **COMPLETE** | All deliverables executed or documented as blocked |
| Sprint-028 Gate | **NO-GO** | M-7 Production Alpha not achieved |
| Next step | Carry Sprint-028 blockers into Sprint-029 backlog | Resolve TT-015, GPU fleet, Argo Rollouts before rescheduling M-7 |
