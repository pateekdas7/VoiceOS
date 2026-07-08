# Sprint-017 — Policy Engine & Regulatory Compliance

**Epic:** E5 — Compliance & Security  
**Status:** ⬜ Pending  
**Depends on:** Sprint-001, Sprint-002, Sprint-013, Sprint-014  
**Blocks:** Sprint-018  

---

## Objective

Implement the central Policy Decision Point (PDP) that governs all authorization, compliance, and conversational policy decisions across the entire VoiceOS system. Encode RBI fair-practice rules and DPDP consent requirements as first-class policy packs.

---

## Architecture References

- Volume 4: Ch2 (Regulatory Compliance — RBI, DPDP, recording consent, retention schedules), Ch4 (Policy Engine — unified PDP, PERMIT/DENY/REQUIRE/FORBID, inheritance, deny-overrides, emergency/break-glass)
- DocSuite-05: Configuration Reference (policy pack configuration)

---

## Components to Implement

### `src/services/policy-engine/`

```
src/services/policy-engine/
├── __init__.py
├── service.py              (PolicyEngineService: gRPC/REST PDP API)
├── engine.py               (PolicyEngine: evaluates PolicyRequest → PolicyDecision)
├── decision.py             (PolicyDecision, PolicyOutcome enum: PERMIT|DENY|REQUIRE|FORBID)
├── rule.py                 (PolicyRule, PolicyCondition, PolicyEffect)
├── policy_set.py           (PolicySet: ordered rules, first-match or deny-overrides)
├── inheritance.py          (PolicyInheritance: global → tenant → campaign → call-level)
├── break_glass.py          (BreakGlassPolicy: emergency override with mandatory audit)
├── packs/
│   ├── __init__.py
│   ├── rbi.py              (RBIPolicyPack: RBI fair-practice code rules)
│   ├── dpdp.py             (DPDPPolicyPack: DPDP consent and data protection rules)
│   ├── authorization.py    (AuthorizationPolicyPack: RBAC rules)
│   ├── ai_governance.py    (AIGovernancePolicyPack: Law of Authority, output guardrails)
│   └── conversational.py   (ConversationalPolicyPack: what can be said per context)
└── metrics.py              (policy_decisions_total by outcome, policy_latency_ms)
```

**PolicyEngine evaluation:**
1. Request: `PolicyRequest(domain, action, subject, resource, context)` where context includes `tenant_id`, `call_id`, `time_of_day`, `customer_dpd`, etc.
2. Resolve applicable PolicySets: global + tenant-level + campaign-level (from Redis cache, backed by Postgres)
3. Evaluate all rules in deny-override order: any DENY → DENY outcome
4. Return `PolicyDecision(outcome, matching_rules, reason)`

**RBIPolicyPack (critical rules):**
- `CALLING_HOURS`: DENY if `time_of_day` outside 08:00–20:00 local time
- `CALLING_FREQUENCY`: DENY if `calls_today_count >= 3` for this customer
- `ABUSE_PROHIBITION`: FORBID utterances classified as threatening/abusive
- `IDENTITY_VERIFY_FIRST`: REQUIRE identity_verification before any debt disclosure
- `DISCLOSURE_REQUIRED`: REQUIRE disclosure of agent identity and purpose at call start
- `RECORDING_CONSENT`: REQUIRE recording consent before call proceeds

**DPDPPolicyPack:**
- `CONSENT_REQUIRED_FOR_PROCESSING`: DENY processing customer data without valid consent record
- `ERASURE_HONOR`: REQUIRE immediate processing of right-to-erasure request
- `PURPOSE_LIMITATION`: DENY using customer data for purposes not covered by consent
- `RETENTION_SCHEDULE`: REQUIRE flagging data past retention period for deletion

**PolicyDecision audit:**
- Every DENY or REQUIRE decision emits `PolicyDecisionMade` domain event (via Event Bus)
- Stored in audit_log table

---

## Files Expected to Change

**New:** `src/services/policy-engine/` (all files above)  
**New:** `tests/unit/services/test_policy_engine.py`  
**New:** `tests/integration/services/test_policy_engine_integration.py`

---

## Acceptance Criteria

- [ ] `RBIPolicyPack.CALLING_HOURS` returns DENY for a request at 21:00 local time
- [ ] `RBIPolicyPack.CALLING_HOURS` returns PERMIT for a request at 10:00 local time
- [ ] `DPDPPolicyPack.CONSENT_REQUIRED_FOR_PROCESSING` returns DENY when no consent record exists
- [ ] Deny-override: tenant-level PERMIT + global-level DENY → final outcome is DENY
- [ ] `PolicyDecisionMade` event is emitted for every DENY decision (integration test)
- [ ] Break-glass policy: supervisor override → PERMIT logged with mandatory audit event
- [ ] Policy evaluation latency: p99 < 10ms (cached rules from Redis)

---

## Required Tests

**Unit:**
- `test_rbi_calling_hours_deny_at_21h` — 21:00 → DENY
- `test_rbi_calling_hours_permit_at_10h` — 10:00 → PERMIT
- `test_rbi_frequency_deny_after_3_calls` — 3 calls today → DENY
- `test_dpdp_deny_without_consent` — no consent record → DENY
- `test_deny_override_precedence` — PERMIT at tenant + DENY at global → DENY
- `test_break_glass_permits_with_audit` — break-glass → PERMIT, audit event emitted
- `test_policy_decision_event_emitted_on_deny` — DENY → PolicyDecisionMade event emitted

**Integration:**
- `test_policy_engine_caches_rules_in_redis` — load rules once, subsequent calls use Redis cache
- `test_policy_engine_full_rbi_compliance_suite` — run all RBI test scenarios, assert 100% pass

---

## Definition of Done

- [ ] All AC items checked
- [ ] All RBI rule scenarios pass
- [ ] All DPDP rule scenarios pass
- [ ] Audit event emitted for every DENY (verified in integration test)
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-018

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Policy rules are deterministic; all tests use in-process evaluation with `FakeRedisClient` for rule caching and `FakeEventBus` for audit events.

### Files Created

- `src/services/policy-engine/__init__.py`, `service.py`, `engine.py`, `decision.py`, `rule.py`, `policy_set.py`, `inheritance.py`, `break_glass.py`, `metrics.py`
- `src/services/policy-engine/packs/__init__.py`, `rbi.py`, `dpdp.py`, `authorization.py`, `ai_governance.py`, `conversational.py`
- `tests/unit/services/test_policy_engine.py`
- `tests/integration/services/test_policy_engine_integration.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Redis (rule cache) | `FakeRedisClient` (Sprint-003) | Policy rules cached without real Redis |
| EventBus (audit events) | `FakeEventBus` (in-memory) | Captures `PolicyDecisionMade` events |
| Postgres (policy persistence) | `TestPostgres` Docker fixture | Rules loaded from test DB for integration tests |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/services/test_policy_engine.py` | All pass |
| RBI compliance suite | `pytest tests/integration/services/test_policy_engine_integration.py::test_policy_engine_full_rbi_compliance_suite` | 100% pass |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `RBIPolicyPack.CALLING_HOURS`: 21:00 → DENY; 10:00 → PERMIT
- `RBIPolicyPack.CALLING_FREQUENCY`: 3 calls today → DENY on 4th call attempt
- `DPDPPolicyPack.CONSENT_REQUIRED`: no consent record → DENY
- Deny-override: tenant PERMIT + global DENY → final = DENY
- Break-glass: PERMIT + `PolicyDecisionMade` audit event emitted
- Policy evaluation p99 < 10ms with cached rules

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| PolicyEngineService | K8s Deployment — `voiceos-runtime` namespace | Central PDP; all services will query it for authorization + compliance |

**Integration wiring this sprint:**
- ConversationEngine → PolicyEngineService: query `CALLING_HOURS` before each call start
- ScheduleEngine (Sprint-023) will query PolicyEngineService for `CALLING_FREQUENCY`
- DialoguePolicyEngine (Sprint-011) begins querying PolicyEngineService for live rule updates

**Previously deployed services that remain running:**
- All Sprint-004–016 services

**Deployment procedure:**
1. Build and push PolicyEngineService container
2. Apply `kubectl apply -f infra/k8s/policy-engine/`
3. Seed policy rules: `python scripts/seed_policies.py --env production` (loads RBI + DPDP packs)
4. Seed rules cached to Redis: `KEYS policy:*` in Redis → 20+ policy rule keys
5. Verify ConversationEngine uses PolicyEngineService for call admission

**Health checks:**
- `GET /health/live` and `GET /health/ready` → 200
- `policy_latency_ms` Prometheus histogram p99 < 10ms
- `policy_decisions_total{outcome="PERMIT"}` counter incrementing on test calls

**Integration validation:**
- PolicyEngineService DENY at 21:00: POST `PolicyRequest(domain=calling, time=21:00)` → DENY
- Audit event: `PolicyDecisionMade` appears in EventBus for DENY decisions
- ConversationEngine: call attempt outside 08:00–20:00 → blocked by PolicyEngineService

**Rollback procedure:**
- `kubectl rollout undo deployment/policy-engine-service -n voiceos-runtime`
- ConversationEngine falls back to DialoguePolicyEngine local rules (Sprint-011) on PolicyEngineService unavailability

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- PolicyEngineService: p99 evaluation latency < 10ms (cached rules from Redis)
- RBI calling hours: live API call at simulated 21:00 → DENY response
- Audit events: every DENY produces `PolicyDecisionMade` in EventBus (verified in logs)
- Rule inheritance: global DENY overrides tenant PERMIT (deny-overrides tested via API)

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- ConversationEngine → PolicyEngineService: gRPC/HTTP call < 5ms intra-cluster
- PolicyEngineService → Redis rule cache: < 2ms per cache hit

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — passes with PolicyEngineService wired in
- ConversationEngine CircuitBreaker: PolicyEngineService circuit breaker in CLOSED state
- DialoguePolicyEngine Sprint-011 tests: `pytest tests/unit/engines/test_dialogue_policy.py`

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] PolicyEngineService with all 5 policy packs implemented
- [ ] RBI + DPDP full compliance suites pass (100%)
- [ ] Deny-override evaluated correctly
- [ ] Break-glass emits audit event
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Policy evaluation p99 < 10ms (cached rules)
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] PolicyEngineService deployed and healthy
- [ ] RBI calling hours DENY verified on live API
- [ ] Audit events emitting for every DENY
- [ ] ConversationEngine queries PolicyEngineService for call admission
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-018

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `PolicyEngineService` to Services table (§8.1) — deployed to `voiceos-runtime`
- Update Service Dependencies (§8.2): ConversationEngine → PolicyEngineService (call admission) before every call
- Add `POLICY_ENGINE_URL` environment variable (§11)
- Add health check command for PolicyEngineService (§14)
- Add port for PolicyEngineService to Port Map (§9.1)
- Note: Redis policy rule cache with TTL — add to §7.1 Redis notes

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-012.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add PolicyEngineService to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add `POLICY_ENGINE_URL` variable description |

### DR Validation

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: PolicyEngineService healthy; ConversationEngine queries it for call admission
```

**RBI calling hours validation after rebuild:**
```bash
# Test call outside 08:00-20:00 → DENY
python3 scripts/validate/rbi_calling_hours.py --hour 21
# Expected: PolicyDecisionMade event with DENY verdict emitted
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
```
