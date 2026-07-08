# Sprint-023 — Campaign Management & Contact Center Platform

**Epic:** E6 — SaaS Platform  
**Status:** ✅ Done (2026-07-06)  
**Depends on:** Sprint-022  
**Blocks:** Sprint-025, Sprint-028  

---

## Objective

Implement campaign orchestration (audience targeting, RBI-compliant scheduling, A/B testing, retry policies) and the contact center platform (AI+human blended operations, live transfer, supervisor join/monitor/barge). These services enable the operational use of VoiceOS by financial institution customers.

---

## Architecture References

- Volume 5: Ch6 (Campaign Management — audience selection, RBI scheduling, retry, A/B testing), Ch7 (Contact Center Platform — AI+human blended, skills-based routing, live transfer, supervisor)
- Volume 4: Ch2 (RBI calling hours enforcement), Ch15 (Human-In-The-Loop — full HITL queue, SLA enforcement, escalation dashboard, override audit, human review UI)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### `src/services/campaign-management/`

```
src/services/campaign-management/
├── __init__.py
├── service.py              (CampaignService: CRUD + lifecycle)
├── lifecycle.py            (CampaignLifecycle: DRAFT→REVIEW→APPROVED→ACTIVE→PAUSED→COMPLETED→ARCHIVED)
├── audience.py             (AudienceSelector: SQL-based cohort builder)
├── scheduler.py            (ScheduleEngine: RBI-compliant scheduling + retry)
├── retry_policy.py         (RetryPolicyEngine: attempt cadence, max_attempts, backoff)
├── ab_testing.py           (ABTestingFramework: variant assignment, result tracking)
├── call_dispatcher.py      (CallDispatcher: emits call initiation events for active campaigns)
└── metrics.py              (calls_dispatched, completion_rate, ptp_rate_by_variant)
```

**ScheduleEngine (RBI-compliant — critical):**
- `schedule_next_call(customer_id, campaign_id) -> datetime | None`
- Checks PolicyEngine: `CALLING_HOURS` → DENY if outside 08:00–20:00 local time
- Checks PolicyEngine: `CALLING_FREQUENCY` → DENY if 3+ calls already today
- Schedules next attempt within permitted window + retry_policy.interval_hours
- Returns `None` if customer has exhausted max_attempts or is on DND

**AudienceSelector:**
- SQL-based cohort: `SELECT customer_id FROM loan_accounts WHERE dpd BETWEEN :min_dpd AND :max_dpd AND outstanding > :min_outstanding AND tenant_id = :tenant_id`
- Consent filter: only customers with valid recording consent included
- Deduplication: customer in multiple campaigns → only assigned to highest-priority campaign

**ABTestingFramework:**
- Variant assignment: deterministic hash of (customer_id + campaign_id) → variant A or B
- Each variant can have: different voice (TTS adapter config), different strategy (opening approach), different calling time preference
- Result tracking: per-variant PTP rate, completion rate

### `src/services/contact-center/`

```
src/services/contact-center/
├── __init__.py
├── service.py              (ContactCenterService: overall CC orchestration)
├── router.py               (SkillsBasedRouter: matches call to available agent by skill)
├── live_transfer.py        (LiveTransferService: AI → human transfer with context handoff)
├── supervisor.py           (SupervisorService: join/monitor/barge-in with live context)
├── agent_screen.py         (AgentScreenContext: context packet assembled for human agent)
└── metrics.py              (transfer_count, supervisor_interventions, avg_handle_time)
```

**LiveTransferService:**
- Triggered by: ESCALATE strategy action, REQUIRE_HUMAN governance verdict, supervisor override
- `initiate_transfer(call_id, reason) -> TransferResult`
  1. Assembles AgentScreenContext (CustomerContext + DecisionEnvelope + full transcript + AI summary + current open issues)
  2. Routes to SkillsBasedRouter to find available agent
  3. Bridges audio to agent's softphone
  4. AI audio muted; human agent takes over
  5. Emits `CallTransferred` event

**SupervisorService:**
- `monitor(call_id, supervisor_id)` — supervisor receives live audio + real-time transcript + AI decision stream (read-only)
- `barge_in(call_id, supervisor_id)` — supervisor audio mixed in; all parties hear supervisor; AI muted
- `override(call_id, supervisor_id)` — supervisor takes full control; AI conversation engine paused; full context on supervisor screen
- All supervisor actions emit audit events

### Human-In-The-Loop Platform (`src/services/hitl/`) (V4 Ch15 — Full Implementation)

```
src/services/hitl/
├── __init__.py
├── queue.py                (HITLQueue: durable queue for REQUIRE_HUMAN decisions)
├── sla_enforcer.py         (SLAEnforcer: monitors HITL queue SLA; escalates on breach)
├── review_api.py           (HumanReviewAPI: REST endpoints for review UI)
├── override_logger.py      (OverrideLogger: logs human decision with mandatory rationale)
└── dashboard.py            (HITLDashboard: escalation dashboard showing queue depth, SLA status)
```

**HITLQueue:**
- Receives `REQUIRE_HUMAN` verdicts from AI Governance layer (Sprint-018) and from HumanOversightRouter (Sprint-020)
- Durable: backed by Postgres `hitl_queue` table (survives restarts)
- SLA: CRITICAL items → supervisor notified within 5 minutes; HIGH → 30 minutes; MEDIUM → 4 hours
- `enqueue(call_id, verdict, context, priority) -> HITLItem` — adds to queue
- `dequeue(supervisor_id) -> HITLItem | None` — pops next item for supervisor

**SLAEnforcer:**
- Polls queue every 60s; if item age > SLA threshold → escalate to on-call via PagerDuty/Slack
- Emits `HITLSLABreached` event to EventBus for compliance monitoring

**HumanReviewAPI:**
- `GET /hitl/queue` — returns pending items for authenticated SUPERVISOR role
- `POST /hitl/items/{id}/decision` — records decision with mandatory `rationale: str` field; dispatches override to ConversationEngine

**OverrideLogger:**
- Every human override is audited: who reviewed, what the decision was, and the rationale text
- Feeds AI governance board review process (model governance — periodic review of override patterns)

---

## Files Expected to Change

**New:** `src/services/campaign-management/`, `src/services/contact-center/`, `src/services/hitl/`  
**New:** `tests/unit/services/test_campaign_management.py`, `test_contact_center.py`, `test_hitl.py`  
**New:** `tests/integration/services/test_rbi_scheduling.py`, `test_hitl_sla.py`

---

## Acceptance Criteria

- [x] `ScheduleEngine` returns `None` for a customer with 3 calls already today (RBI frequency limit)
- [x] `ScheduleEngine` returns `None` for a 21:00 scheduling request (RBI calling hours)
- [x] `ABTestingFramework` assigns variants deterministically (same customer_id + campaign_id → same variant)
- [x] `LiveTransferService` assembles AgentScreenContext including CustomerContext, transcript, AI summary
- [x] Campaign lifecycle: DRAFT → REVIEW (cannot be ACTIVE without APPROVED first)
- [x] Supervisor monitor: does not interrupt call (read-only)
- [x] Supervisor barge-in: AI is muted when supervisor barge-in is active
- [x] HITLQueue: REQUIRE_HUMAN verdict enqueued → visible via `GET /hitl/queue` for SUPERVISOR role
- [x] HITLQueue: item older than CRITICAL SLA (5 min) → `HITLSLABreached` event emitted
- [x] HumanReviewAPI: decision without `rationale` field → 400 Bad Request
- [x] OverrideLogger: every human decision has audit event with rationale and supervisor identity

---

## Required Tests

**Unit:**
- `test_schedule_engine_rbi_frequency_limit` — 3 calls today → schedule returns None
- `test_schedule_engine_rbi_calling_hours` — 21:00 → returns None
- `test_ab_variant_deterministic` — same inputs → same variant (call 5 times)
- `test_campaign_lifecycle_no_active_without_approved` — DRAFT → ACTIVE raises
- `test_live_transfer_context_includes_transcript` — context includes full transcript
- `test_hitl_queue_enqueue_and_dequeue` — enqueue REQUIRE_HUMAN → dequeue returns same item
- `test_hitl_sla_enforcer_breach` — item age > 5 min → SLABreached event emitted
- `test_human_review_requires_rationale` — POST /hitl/items/{id}/decision with no rationale → 400
- `test_override_logger_audit_event` — decision recorded → audit event has supervisor_id + rationale

**Integration:**
- `test_rbi_scheduling_compliance_suite` — 20 scheduling scenarios, all fail/pass as expected
- `test_hitl_sla_integration` — real Postgres queue + time mock → SLA enforcement triggers correctly

---

## Definition of Done

- [x] All AC items checked
- [x] RBI scheduling compliance suite: 100% pass
- [x] Supervisor barge-in mutes AI (integration test)
- [x] CI green
- [x] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [x] `CURRENT_SPRINT.md` updated to Sprint-024

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Campaign scheduling uses `FakePolicyEngine`; HITL queue uses `TestPostgres`; supervisor tests use in-process audio routing mocks.

### Files Created

- `src/services/campaign-management/__init__.py`, `service.py`, `lifecycle.py`, `audience.py`, `scheduler.py`, `retry_policy.py`, `ab_testing.py`, `call_dispatcher.py`, `metrics.py`
- `src/services/contact-center/__init__.py`, `service.py`, `router.py`, `live_transfer.py`, `supervisor.py`, `agent_screen.py`, `metrics.py`
- `src/services/hitl/__init__.py`, `queue.py`, `sla_enforcer.py`, `review_api.py`, `override_logger.py`, `dashboard.py`
- `tests/unit/services/test_campaign_management.py`, `test_contact_center.py`, `test_hitl.py`
- `tests/integration/services/test_rbi_scheduling.py`, `test_hitl_sla.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| PolicyEngine | `FakePolicyEngine` | Returns DENY for outside-hours requests; PERMIT for others |
| Postgres (HITL queue) | `TestPostgres` Docker fixture | Real HITL queue durability test |
| EventBus | `FakeEventBus` | Captures `CallTransferred`, `HITLSLABreached` events |
| Audio routing | `FakeAudioBridge` | Simulates supervisor barge-in without real telephony |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/services/` | All pass |
| RBI compliance suite | `pytest tests/integration/services/test_rbi_scheduling.py` | 20 scenarios: 100% pass |
| HITL SLA test | `pytest tests/integration/services/test_hitl_sla.py` | SLA breach fires within tolerance |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `ScheduleEngine`: 21:00 request → None; 3 calls today + attempt 4th → None
- `ABTestingFramework`: same `customer_id + campaign_id` → same variant (5× calls)
- `Campaign`: DRAFT → ACTIVE (without APPROVED) → raises
- `HITLQueue`: REQUIRE_HUMAN verdict enqueued → `GET /hitl/queue` returns it for SUPERVISOR
- `SLAEnforcer`: item age > 5min → `HITLSLABreached` event emitted
- `POST /hitl/items/{id}/decision` without rationale → 400

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| CampaignManagementService | K8s Deployment — `voiceos-platform` | RBI-compliant scheduling + A/B testing |
| ContactCenterService | K8s Deployment — `voiceos-platform` | AI+human blended; live transfer |
| HITLService | K8s Deployment — `voiceos-platform` | Durable REQUIRE_HUMAN queue + SLA enforcement |

**Previously deployed services that remain running:**
- All Sprint-004–022 services

**Deployment procedure:**
1. Deploy CampaignManagementService, ContactCenterService, HITLService
2. Create test campaign: DRAFT → REVIEW → APPROVED → ACTIVE
3. Verify ScheduleEngine: test call request at 21:00 → DENY from PolicyEngine → schedule returns None
4. Trigger REQUIRE_HUMAN verdict in ConversationEngine → verify item appears in HITL queue
5. Supervisor barge-in: supervisor calls `barge_in(call_id)` → AI audio muted in test call

**Health checks:**
- All 3 services: `GET /health/ready` → 200
- HITL queue depth: `hitl_queue_depth` Prometheus gauge = 0 at steady state
- SLAEnforcer: polling interval confirmed (60s) via log timestamps

**Integration validation:**
- RBI frequency: create test customer with 3 calls today → attempt 4th → DENY from PolicyEngine → not scheduled
- A/B variant: dispatch 100 calls → ~50% in variant A, ~50% in variant B (deterministic hash check)
- Live transfer: ESCALATE action in ConversationEngine → `CallTransferred` event + AgentScreenContext assembled
- HITL: REQUIRE_HUMAN verdict → queue item → supervisor approves → ConversationEngine resumes

**Rollback procedure:**
- `kubectl rollout undo deployment/<service> -n voiceos-platform` per service
- HITL queue: Postgres-backed, survives rollback

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- RBI calling hours enforcement: API test at 21:00 → schedule returns None (not error, not call)
- HITL SLA: item created at T → SLAEnforcer fires `HITLSLABreached` at T + 5min (CRITICAL SLA)
- Supervisor barge-in: AI audio muted during barge-in (verified via audio routing mock on staging)
- Campaign lifecycle: DRAFT → ACTIVE (without APPROVED) → 422 from API

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- CampaignManagementService → PolicyEngineService: scheduling check < 5ms
- ContactCenterService → ConversationEngine: transfer context delivery < 100ms

### Regression Validation

- Walking skeleton e2e test: passes (campaign now manages call dispatch)
- CRM + Collections: PTP creation still idempotent after campaign integration
- HITL: audit events logged for all supervisor actions

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [x] CampaignManagementService (RBI-compliant scheduler, A/B testing) implemented
- [x] ContactCenterService (live transfer, supervisor barge-in) implemented
- [x] HITLService (durable queue, SLA enforcer, review API) implemented
- [x] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [x] RBI scheduling compliance suite: 100% pass
- [x] HITL SLA enforcement verified
- [x] Coverage ≥ 85%
- [x] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [x] All 3 services deployed and healthy
- [x] RBI scheduling enforcement verified on live API
- [x] HITL queue functional; SLA alerting fires correctly
- [x] Supervisor barge-in mutes AI (staging integration test)
- [x] All regression tests pass
- [x] Deployment remains active as baseline for Sprint-024

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `CampaignManagementService`, `ContactCenterService`, `HITLService` to Services table (§8.1) — namespace: `voiceos-platform`
- Update §12 Database Schema: add `campaigns`, `campaign_audiences`, `campaign_results`, `hitl_queue`, `hitl_decisions` tables
- Add environment variables: `CAMPAIGN_MGMT_URL`, `CONTACT_CENTER_URL`, `HITL_SERVICE_URL`, `HITL_SLA_CRITICAL_MINUTES=5` (§11)
- Add health check commands for all 3 new services (§14)
- Update Prometheus metrics list: add `hitl_queue_depth` gauge

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-019.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add CampaignManagementService, ContactCenterService, HITLService to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add campaign, contact center, HITL variable descriptions |

### DR Validation

**RBI scheduling validation after rebuild:**
```bash
python3 scripts/validate/rbi_scheduling.py --hour 21
# Expected: schedule returns None (DENY from PolicyEngine)
```

**HITL queue validation after rebuild:**
```bash
# Trigger REQUIRE_HUMAN verdict; verify queue item
python3 scripts/validate/hitl_queue.py
# Expected: item in queue; supervisor can retrieve and approve
```

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
```
