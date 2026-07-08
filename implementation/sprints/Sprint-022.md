# Sprint-022 — CRM & Loan/Collections Management

**Epic:** E6 — SaaS Platform  
**Status:** ✅ Done (2026-07-06)  
**Depends on:** Sprint-021, Sprint-014, Sprint-015  
**Blocks:** Sprint-023, Sprint-024, Sprint-028  

---

## Objective

Implement the authoritative CRM (party data) and the collections system of record (loan accounts, EMI schedules, DPD tracking, PTPs, settlements, callbacks, escalation). Also implement the CustomerContext assembler that combines CRM + collections data into the authoritative context object used by the AI conversation engine.

---

## Architecture References

- Volume 5: Ch4 (CRM — authoritative party data, CustomerContext assembly, Law of Authority dependency), Ch5 (Loan & Collections Management — LoanAccount, EMI, DPD, PTP idempotent, settlement, callback, escalation)
- Volume 2: Ch1 (Law of Authority — CustomerContext is the authoritative source for all facts)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### `src/services/crm/`

```
src/services/crm/
├── __init__.py
├── service.py              (CustomerService: CRUD + search)
├── repository.py           (CustomerRepository wrapper — delegates to src/libs/repositories/)
├── party.py                (PartyService: borrower/co-borrower/guarantor party management)
├── context_assembler.py    (CustomerContextAssembler: builds CustomerContext from CRM + Collections)
├── import.py               (CustomerImporter: bulk import from CSV/XLSX, validates + deduplicates)
└── metrics.py              (customer_count, context_assembly_latency_ms)
```

**CustomerContextAssembler (Law of Authority critical):**
- `assemble(tenant_id: str, customer_id: str, call_id: str) -> CustomerContext`
- Fetches from CRM: customer basic info, party relationships, language preference, DND status
- Fetches from Collections: loan accounts, outstanding amounts, EMI schedule, DPD, active PTPs, consents
- Validates all amounts via `assert_ri5_law_of_authority(fact, value, source="crm_collections")`
- Returns sealed, immutable `CustomerContext` (from contracts library)
- CustomerContext is assembled once per call at call start — NEVER updated during a call

### `src/services/collections/`

```
src/services/collections/
├── __init__.py
├── loan_account.py         (LoanAccountService: CRUD + DPD calculation)
├── emi_schedule.py         (EMIScheduleService: schedule management, payment posting)
├── promise_to_pay.py       (PromiseToPay: idempotent PTP creation + lifecycle)
├── settlement.py           (SettlementService: offer → accept → authorize → disburse)
├── callback.py             (CallbackScheduler: schedule callback → campaign integration)
├── escalation.py           (EscalationWorkflow: auto-escalate on risk flags)
└── metrics.py              (ptps_created, ptps_fulfilled, ptps_broken, dpd_distribution)
```

**PromiseToPay (idempotent creation — critical):**
- `create(tenant_id, loan_account_id, customer_id, amount, promise_date, call_id) -> PromiseToPay`
- Idempotency key: `f"ptp:{tenant_id}:{call_id}:{loan_account_id}:{promise_date}"` (unique per call+date)
- Uses `IdempotencyGuard.execute_once(key, _create_ptp_fn)` — guaranteed exactly-once creation
- Validates: amount ≥ minimum EMI (from loan account), date within allowed window
- Policy check: `PolicyEngine.check(domain=COLLECTIONS, action=CREATE_PTP)`
- Emits `PTPreated` domain event

**DPD Calculation (from EMI schedule):**
- `calculate_dpd(loan_account_id) -> int` — counts calendar days since first unpaid EMI
- Real-time calculation; not stored (derived from EMI schedule)

---

## Files Expected to Change

**New:** `src/services/crm/`, `src/services/collections/`  
**New:** `tests/unit/services/test_crm.py`, `test_collections.py`, `test_ptp_idempotency.py`  
**New:** `tests/integration/services/test_customer_context_assembly.py`

---

## Acceptance Criteria

- [x] `CustomerContextAssembler.assemble()` returns a frozen CustomerContext with correct outstanding amount from Collections (integration test against test Postgres)
- [x] `assert_ri5_law_of_authority` is called for every amount and date placed in CustomerContext
- [x] `PromiseToPay.create()` with same call_id + loan_account_id + promise_date twice → exactly one PTP record (idempotency integration test)
- [x] DPD calculation: 3 unpaid EMIs, oldest due 30 days ago → DPD = 30
- [x] Settlement workflow: offer → accept → authorize transitions correctly
- [x] CustomerContext is sealed (immutable) after assembly — mutation attempt raises

---

## Required Tests

**Unit:**
- `test_dpd_calculation` — loan with 3 missed EMIs → correct DPD
- `test_customer_context_immutable` — attempt to set field after assembly → raises
- `test_ptp_idempotency_key_deterministic` — same inputs → same idempotency key

**Integration:**
- `test_customer_context_assembly` — real Postgres: assemble CustomerContext → correct fields from DB
- `test_ptp_create_idempotent` — submit same PTP twice → one record in DB
- `test_ptp_policy_check_called` — mock PolicyEngine, verify check called on PTP creation

---

## Definition of Done

- [x] All AC items checked
- [x] PTP idempotency integration test passes (concurrent submission still creates one record)
- [x] Law of Authority called for all amounts in CustomerContext (verified by coverage)
- [x] CI green
- [x] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [x] `CURRENT_SPRINT.md` updated to Sprint-023

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** CRM and collections tests use `TestPostgres`; PTP idempotency tests use real concurrent Postgres transactions.

### Files Created

- `src/services/crm/__init__.py`, `service.py`, `repository.py`, `party.py`, `context_assembler.py`, `import.py`, `metrics.py`
- `src/services/collections/__init__.py`, `loan_account.py`, `emi_schedule.py`, `promise_to_pay.py`, `settlement.py`, `callback.py`, `escalation.py`, `metrics.py`
- `tests/unit/services/test_crm.py`, `test_collections.py`, `test_ptp_idempotency.py`
- `tests/integration/services/test_customer_context_assembly.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Postgres | `TestPostgres` Docker fixture | Real CRM + collections DB for integration tests |
| PolicyEngine | `FakePolicyEngine` stub | Returns PERMIT for PTP creation without PolicyEngineService |
| IdempotencyGuard | Real implementation | Uses `TestPostgres` for concurrent PTP creation test |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations; `assert_ri5_law_of_authority` in all CustomerContext assembly paths |
| Unit tests | `pytest tests/unit/services/test_crm.py tests/unit/services/test_collections.py` | All pass |
| Concurrent PTP test | `pytest tests/integration/services/test_customer_context_assembly.py::test_ptp_create_idempotent` | 1 record after 10 concurrent requests |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `CustomerContextAssembler.assemble()`: sealed, immutable `CustomerContext`; mutation attempt raises
- `assert_ri5_law_of_authority` called for every amount in CustomerContext
- PTP idempotency: 10 concurrent POSTs with same `call_id + loan_account_id + promise_date` → 1 DB record
- DPD calculation: 3 unpaid EMIs, oldest due 30 days ago → DPD = 30

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| CRMService (CustomerService, ContextAssembler) | K8s Deployment — `voiceos-platform` | Authoritative customer data + CustomerContext assembly |
| CollectionsService (LoanAccount, PTP, Settlement, Callback, Escalation) | K8s Deployment — `voiceos-platform` | System of record for collections lifecycle |

**Previously deployed services that remain running:**
- All Sprint-004–021 services

**Deployment procedure:**
1. Deploy CRMService and CollectionsService; confirm Postgres + PolicyEngineService connectivity
2. Seed test customer + loan account data via `scripts/db/seed-db.sh`
3. Verify: `CustomerContextAssembler.assemble(tenant_id, customer_id)` returns correct context from seeded data
4. Run concurrent PTP idempotency test via deployed API (10 concurrent HTTP requests)

**Health checks:**
- CRMService + CollectionsService: `GET /health/ready` → 200
- `context_assembly_latency_ms` Prometheus histogram: p99 < 100ms
- PolicyEngine check on PTP creation: verify `PolicyDecisionMade` audit event emitted

**Integration validation:**
- ConversationEngine → CRMService: assemble CustomerContext at call start → correct amounts + DPD
- PTP creation: `PromiseToPay.create()` with same call_id twice → 1 record in Postgres
- Settlement workflow: offer → accept → authorize state transitions via API

**Rollback procedure:**
- `kubectl rollout undo deployment/crm-service -n voiceos-platform`
- `kubectl rollout undo deployment/collections-service -n voiceos-platform`
- Data unaffected (stateless services with external Postgres)

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- CustomerContext: immutability enforcement verified via API (PATCH on sealed context → 400)
- `assert_ri5_law_of_authority` coverage: all amount fields in CustomerContext trace through this guard
- PTP concurrent creation: 10 concurrent HTTP requests → 1 Postgres row (verified `SELECT COUNT(*) FROM promises_to_pay`)

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- CRMService → Postgres: CustomerContext assembly latency < 100ms
- CRMService → CollectionsService: loan account + PTP query within same context assembly call

### Regression Validation

- Walking skeleton e2e test: passes with CRMService providing real CustomerContext
- PolicyEngineService: PTP creation policy check fires correctly
- AuditLogger: PTP creation audit event persisted

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [x] CRMService (CustomerContextAssembler, RI-5) implemented
- [x] CollectionsService (PTP idempotent, DPD calculation) implemented
- [x] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [x] Concurrent PTP idempotency test passes
- [x] Law of Authority coverage confirmed
- [x] Coverage ≥ 85%
- [x] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [x] CRMService + CollectionsService deployed and healthy
- [x] CustomerContext assembled from live Postgres data correctly
- [x] PTP concurrent creation: 1 record after 10 concurrent requests (on production Postgres)
- [x] All regression tests pass
- [x] Deployment remains active as baseline for Sprint-023

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `CRMService`, `CollectionsService` to Services table (§8.1) — namespace: `voiceos-platform`
- Update §12 Database Schema: add `customers`, `loan_accounts`, `emi_schedule`, `promises_to_pay`, `settlements`, `callbacks`, `escalations` tables
- Update Service Dependencies (§8.2): ConversationEngine → CRMService for CustomerContext at call start
- Add environment variables: `CRM_SERVICE_URL`, `COLLECTIONS_SERVICE_URL` (§11)
- Add health check commands for CRMService and CollectionsService (§14)

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-019.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add CRMService, CollectionsService to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add CRM and Collections URL variable descriptions |

### DR Validation

**CustomerContext assembly after rebuild:**
```bash
# Seed test customer + loan account
bash scripts/db/seed-db.sh --tenant test-tenant

# Assemble CustomerContext
python3 scripts/validate/customer_context.py --customer-id test-customer-001
# Expected: sealed CustomerContext with correct amounts and DPD
```

**PTP idempotency after rebuild:**
```bash
python3 scripts/validate/ptp_idempotency.py --concurrent 10
# Expected: exactly 1 record in Postgres after 10 concurrent requests
```

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all voiceos-platform namespace services healthy
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
```
