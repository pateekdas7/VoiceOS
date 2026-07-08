# Sprint-033 — Workflow Automation & Customer Success Platform

**Epic:** E6-Extended — SaaS Platform (Operational Capability)
**Status:** ⬜ Pending
**Depends on:** Sprint-025, Sprint-013, Sprint-022, Sprint-023, Sprint-024, Sprint-032
**Blocks:** Sprint-031
**Milestone:** Workflow & Customer Success Complete

---

## Objective

Implement two major platform subsystems: (1) the Workflow Automation Engine — a durable, event-driven trigger-condition-action system that allows customers to automate collections workflows without code; and (2) the Customer Success Platform — health scoring, churn prediction, and onboarding tracking that allows the VoiceOS team to monitor and retain enterprise customers.

---

## Architecture References

- Volume 5: Ch17 (Workflow Automation — WorkflowEngine, visual builder, event-driven execution, approval flows, durable idempotent execution), Ch18 (Customer Success Platform — onboarding milestones, tenant health scoring, churn prediction, NPS/CSAT collection, in-product guidance)
- Volume 3: Ch3 (Event Bus — workflows subscribe to domain events for trigger evaluation)
- Volume 4: Ch15 (Human-In-The-Loop — approval flows connect to HITL queue from Sprint-023)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### `src/services/workflow-engine/` (V5 Ch17)

```
src/services/workflow-engine/
├── __init__.py
├── engine.py               (WorkflowEngine: core execution engine)
├── executor.py             (WorkflowExecutor: durable step-by-step execution with state persistence)
├── registry.py             (WorkflowRegistry: CRUD for workflow definitions)
├── trigger.py              (TriggerEvaluator: evaluates trigger conditions against incoming domain events)
├── conditions.py           (ConditionEvaluator: evaluates guard conditions for branch logic)
├── actions.py              (ActionDispatcher: executes action types — HTTP call, send notification, create PTP, pause campaign, etc.)
├── approval.py             (ApprovalFlowAdapter: integrates approval steps with HITLQueue from Sprint-023)
├── builder_api.py          (WorkflowBuilderAPI: REST API for visual builder frontend)
└── metrics.py              (workflow_executions_total, workflow_latency_ms, approval_pending_count)
```

**WorkflowEngine:**
- Workflow definition: JSON schema with `trigger`, `conditions`, `steps` fields
- Trigger types: `EVENT` (subscribes to domain events from EventBus), `SCHEDULE` (cron), `WEBHOOK` (external HTTP call)
- Example trigger: `{ "type": "EVENT", "event": "CallCompleted", "filter": "outcome == UNSUCCESSFUL" }`
- Step types: `ACTION` | `CONDITION_BRANCH` | `APPROVAL` | `WAIT` | `SUB_WORKFLOW`

**WorkflowExecutor:**
- Durable: each step's completion state persisted in Postgres `workflow_executions` table
- Idempotent: `execute_step(execution_id, step_id, action)` uses IdempotencyGuard — replay-safe
- On process restart: resumes from last persisted step (no double-execution)
- `execute(workflow_id, trigger_event) -> ExecutionHandle` — starts execution

**TriggerEvaluator:**
- Subscribes to EventBus; on each domain event, evaluates all matching workflow triggers
- Filter expressions: simple field-equality and comparison language (no Turing-complete code)
- Matched triggers → dispatches to WorkflowExecutor

**ActionDispatcher — supported action types:**
- `HTTP_CALL` — authenticated HTTP webhook to customer system
- `SEND_NOTIFICATION` — in-platform notification (email, Slack)
- `CREATE_PTP` — creates PromiseToPay via CollectionsService
- `PAUSE_CAMPAIGN` — pauses a campaign via CampaignService
- `ASSIGN_HUMAN_AGENT` — routes call to LiveTransferService
- `REQUIRE_APPROVAL` — enqueues approval request to HITLQueue (Sprint-023); execution pauses until approved

**WorkflowBuilderAPI:**
- `GET /workflows` — list tenant workflows
- `POST /workflows` — create workflow definition (JSON schema validated)
- `POST /workflows/{id}/activate` — activates workflow (starts receiving triggers)
- `GET /workflows/{id}/executions` — list execution history with step-level status

### `src/services/customer-success/` (V5 Ch18)

```
src/services/customer-success/
├── __init__.py
├── service.py              (CustomerSuccessService: orchestrates all CS functions)
├── onboarding.py           (OnboardingTracker: milestone-based onboarding progress)
├── health_scorer.py        (TenantHealthScorer: composite health score from usage + SLO + billing)
├── churn_predictor.py      (ChurnPredictor: risk scoring for tenant churn)
├── nps.py                  (NPSCollector: in-product NPS/CSAT survey delivery and collection)
└── guidance.py             (InProductGuidance: surfaces next-action hints in admin portal)
```

**OnboardingTracker:**
- Milestones: ACCOUNT_CREATED → FIRST_CAMPAIGN_CREATED → FIRST_CALL_COMPLETED → FIRST_PTP_CREATED → BILLING_CONFIGURED → PRODUCTION_LIVE
- `get_progress(tenant_id) -> OnboardingProgress` — returns completed milestones and next recommended action
- Auto-detected: milestones are triggered by observing domain events (no manual tracking required)

**TenantHealthScorer:**
- Composite score (0–100): usage frequency (30%) + SLO attainment (30%) + billing health (20%) + feature adoption (20%)
- Score bands: HEALTHY (80–100) | AT_RISK (50–79) | CRITICAL (0–49)
- `score(tenant_id) -> HealthScore` — computed on-demand; cached in Redis 1h TTL
- Daily job: compute scores for all tenants, store in `tenant_health_history` table

**ChurnPredictor:**
- Features: days since last call, 30d call volume trend, SLO complaints, billing payment failures, support ticket count
- Model: logistic regression (simple, auditable) trained on historical churn labels
- `predict_churn_risk(tenant_id) -> ChurnRisk(probability: float, reason: list[str])` — returns risk + top contributing factors
- Risk threshold ≥ 0.7 → CS team alerted automatically

**NPSCollector:**
- `send_survey(tenant_id, survey_type: NPS|CSAT) -> SurveyHandle` — sends in-product survey to tenant admin
- `record_response(survey_id, score: int, comment: str | None) -> None` — records response
- Aggregates: `get_nps_score(tenant_id, period) -> float` — Net Promoter Score

---

## Files Expected to Change

**New:** `src/services/workflow-engine/`, `src/services/customer-success/`
**New:** `tests/unit/services/test_workflow_engine.py`, `test_customer_success.py`
**New:** `tests/integration/services/test_workflow_execution.py`, `test_onboarding_tracker.py`
**Modified:** `src/services/contact-center/` — connect `ASSIGN_HUMAN_AGENT` action to LiveTransferService

---

## Acceptance Criteria

- [ ] WorkflowEngine: workflow with `CallCompleted` trigger + `UNSUCCESSFUL` filter → executes correctly when matching event arrives
- [ ] WorkflowExecutor: execution survives process restart and resumes from correct step (integration test)
- [ ] ApprovalFlowAdapter: REQUIRE_APPROVAL step → item appears in HITLQueue; approval → execution resumes
- [ ] ActionDispatcher: `HTTP_CALL` action → HTTP request sent to mock endpoint with correct payload
- [ ] WorkflowBuilderAPI: `POST /workflows` with valid definition → workflow stored; `GET /workflows/{id}/executions` → returns execution history
- [ ] OnboardingTracker: domain events → correct milestones auto-detected and recorded
- [ ] TenantHealthScorer: tenant with zero calls in 30d → AT_RISK score; active tenant → HEALTHY score
- [ ] ChurnPredictor: high-risk features → probability ≥ 0.7 → CS alert emitted
- [ ] NPSCollector: send survey → response recorded → NPS score calculated correctly

---

## Required Tests

**Unit:**
- `test_trigger_evaluator_matches_event` — CallCompleted with filter UNSUCCESSFUL → matched
- `test_trigger_evaluator_no_match` — CallCompleted with filter SUCCESSFUL → not matched
- `test_workflow_executor_idempotent` — execute same step twice → one effect (IdempotencyGuard)
- `test_action_dispatcher_http_call` — HTTP_CALL action → mock HTTP endpoint receives request
- `test_action_dispatcher_create_ptp` — CREATE_PTP action → PTP created via CollectionsService
- `test_approval_flow_enqueues_hitl` — REQUIRE_APPROVAL step → item in HITLQueue
- `test_onboarding_milestone_auto_detection` — FirstCallCompleted event → milestone marked COMPLETE
- `test_health_scorer_at_risk` — zero calls 30d + billing failure → score < 50 (AT_RISK)
- `test_churn_predictor_high_risk` — construct high-risk feature vector → probability ≥ 0.7
- `test_nps_score_calculation` — responses [9, 10, 3, 8] → NPS = 50 (2 promoters - 1 detractor / 4 * 100)

**Integration:**
- `test_workflow_execution_survives_restart` — start execution, simulate process restart, verify resumes from step 2
- `test_onboarding_tracker_full_flow` — create tenant → run campaign → complete call → all milestones detected

---

## Definition of Done

- [ ] All AC items checked
- [ ] WorkflowEngine idempotency verified under simulated process restart
- [ ] Approval flow connects to HITL queue (integration test)
- [ ] Health scorer daily job runs without error on test tenant set
- [ ] Churn predictor returns risk probability for all tenants (no errors on edge cases)
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-034

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** WorkflowEngine uses `TestPostgres` for durable execution state and `FakeEventBus` for trigger delivery; HTTP_CALL actions use `aiohttp` test server; CustomerSuccess uses `FakeRedisClient` for health score caching.

### Files Created

- `src/services/workflow-engine/__init__.py`, `engine.py`, `executor.py`, `registry.py`, `trigger.py`, `conditions.py`, `actions.py`, `approval.py`, `builder_api.py`, `metrics.py`
- `src/services/customer-success/__init__.py`, `service.py`, `onboarding.py`, `health_scorer.py`, `churn_predictor.py`, `nps.py`, `guidance.py`
- `tests/unit/services/test_workflow_engine.py`, `test_customer_success.py`
- `tests/integration/services/test_workflow_execution.py`, `test_onboarding_tracker.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| EventBus | `FakeEventBus` | Publishes `CallCompleted`, `PTPreated`, `CampaignPaused` to trigger evaluator |
| Postgres | `TestPostgres` Docker fixture | Workflow definitions, execution state, health history |
| Redis | `FakeRedisClient` | Health score 1h TTL cache |
| HTTP endpoint (HTTP_CALL) | `aiohttp.web` test server | Receives workflow HTTP_CALL action |
| HITLQueue | Real implementation | Approval flow uses actual HITLQueue against `TestPostgres` |
| CollectionsService | `AsyncMock` | `CREATE_PTP` action mock |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/services/test_workflow_engine.py tests/unit/services/test_customer_success.py` | All pass |
| Workflow execution restart | `pytest tests/integration/services/test_workflow_execution.py` | Resumes from step 2 after simulated restart |
| Onboarding tracker | `pytest tests/integration/services/test_onboarding_tracker.py` | All milestones auto-detected |
| Coverage | `pytest --cov=src/services/workflow-engine --cov=src/services/customer-success --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `TriggerEvaluator`: `CallCompleted` with `UNSUCCESSFUL` filter → matched; `SUCCESSFUL` → not matched
- `WorkflowExecutor`: idempotency guard prevents double-execution on same step
- `HTTP_CALL` action: test aiohttp server receives correct payload
- `REQUIRE_APPROVAL` step: item appears in `TestPostgres` HITLQueue
- `OnboardingTracker`: `CallCompleted` event → `FIRST_CALL_COMPLETED` milestone → auto-detected
- `TenantHealthScorer`: zero calls 30d + billing failure → score < 50 (AT_RISK)
- `ChurnPredictor`: high-risk feature vector → probability ≥ 0.7
- NPS: responses [9, 10, 3, 8] → NPS = 50

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| WorkflowEngineService | K8s Deployment — `voiceos-platform` | Durable event-driven workflow execution |
| CustomerSuccessService | K8s Deployment — `voiceos-platform` | Tenant health scoring, churn prediction, NPS |

**Previously deployed services that remain running:**
- All Sprint-004–032 services

**Deployment procedure:**
1. Deploy WorkflowEngineService and CustomerSuccessService
2. Create test workflow: `CallCompleted` trigger + `HTTP_CALL` action → verify execution on next test call
3. Test approval flow: workflow with `REQUIRE_APPROVAL` step → verify HITL queue item appears
4. Health scorer: trigger daily scoring job → verify health scores in Postgres for test tenants
5. Onboarding: create test tenant → run first campaign → confirm all milestones auto-detected

**Health checks:**
- Both services: `GET /health/ready` → 200
- WorkflowEngineService: `workflow_executions_total` Prometheus counter exposed
- CustomerSuccessService: `tenant_health_score` Prometheus gauge exposed

**Integration validation:**
- Workflow trigger: live `CallCompleted` event → workflow executes → HTTP_CALL action fires to test endpoint
- Approval flow: REQUIRE_APPROVAL step → HITL queue item → supervisor approves → execution resumes
- Health scorer: AT_RISK tenant → CS alert emitted to EventBus
- Churn predictor: high-risk tenant → `ChurnRiskAlert` event visible in Prometheus

**Rollback procedure:**
- `kubectl rollout undo deployment/workflow-engine-service -n voiceos-platform`
- `kubectl rollout undo deployment/customer-success-service -n voiceos-platform`
- Workflow execution state in Postgres survives rollback; in-flight executions resume after redeploy

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- Workflow idempotency: kill WorkflowEngineService pod mid-execution → on restart, execution resumes from correct step
- Approval flow: HITL queue item created and approval dispatched via live API
- Health score: all test tenants have scores in `tenant_health_history` after daily job

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- WorkflowEngineService → EventBus: event subscription confirmed (all trigger types)
- WorkflowEngineService → HITLService: approval dispatch < 100ms

### Regression Validation

- Walking skeleton e2e test: passes (workflow trigger fires on test call)
- HITL queue: unaffected by workflow approval integration
- Campaign management: `PAUSE_CAMPAIGN` action pauses correctly

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] WorkflowEngine (trigger, executor, conditions, actions, approval adapter) implemented
- [ ] WorkflowBuilderAPI implemented
- [ ] CustomerSuccess (onboarding, health scorer, churn predictor, NPS) implemented
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Workflow execution restart test passes
- [ ] Approval flow enqueues to HITL (integration test)
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] WorkflowEngineService deployed and healthy
- [ ] CustomerSuccessService deployed and healthy
- [ ] Live workflow trigger fires on test `CallCompleted` event
- [ ] Approval flow connects to live HITL queue
- [ ] Health scorer runs on all tenants without errors
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-034

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `WorkflowEngineService`, `CustomerSuccessService` to §8.1 Services table — namespace: `voiceos-platform`
- Update §12 Database Schema: add `workflow_definitions`, `workflow_instances`, `workflow_steps`, `health_scores`, `escalation_records`, `success_playbooks` tables
- Add environment variables: `WORKFLOW_ENGINE_URL`, `CUSTOMER_SUCCESS_URL`, `HEALTH_SCORE_THRESHOLD` (§11)
- Add health check commands for both new services (§14)
- Update §8.2 Startup Order: WorkflowEngineService depends on EventBusService and HITLService

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-030.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add WorkflowEngineService, CustomerSuccessService to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add workflow engine and customer success variable descriptions |
| `deployment/cpu/restore.sh` | Add: verify EventBus consumer group exists for workflow engine `XINFO GROUPS voiceos-events` |

### DR Validation

**Workflow engine after rebuild:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh

# Trigger test workflow via EventBus
python3 scripts/validate/workflow_trigger.py --event CallCompleted --tenant test-tenant
# Expected: workflow instance created; steps execute in order
```

**Approval flow to HITL:**
```bash
python3 scripts/validate/hitl_workflow.py --scenario approval-required
# Expected: approval task enqueued to HITL queue; CustomerSuccessService notified
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; HITL and workflow services operational
```
