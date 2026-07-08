# Sprint-014 — Persistent Storage — Schemas & Migrations

**Epic:** E4 — Reliability Infrastructure  
**Status:** ⬜ Pending  
**Depends on:** Sprint-002, Sprint-003  
**Blocks:** Sprint-015, Sprint-016, Sprint-017, Sprint-022  

---

## Objective

Implement all Postgres and MongoDB schemas as production-ready migrations, and create the data access repositories (DAO/Repository pattern) for every authoritative domain. This is the authoritative data layer — nothing else is authoritative.

---

## Architecture References

- Volume 3: Ch5 (Persistent Storage — Postgres authoritative, MongoDB lineage/documents, object storage)
- Volume 6: Ch7 (Data Modeling Standards — expand-contract migrations, per-tenant money types, store selection rules)
- DocSuite-03: Data Dictionary (complete schema definitions)

---

## Components to Implement

### Postgres Migrations (`scripts/db/migrations/`)

All migrations use Alembic. Each migration is additive (expand-contract); no columns dropped without a preceding `nullable + default` migration.

**Tables:**
```sql
-- 001_tenants
tenants(id UUID PK, external_id TEXT UNIQUE, name TEXT, status tenant_status_enum, 
        isolation_profile isolation_profile_enum, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)

-- 002_organizations  
organizations(id UUID PK, tenant_id UUID FK, name TEXT, type org_type_enum, parent_id UUID NULL FK)

-- 003_users
users(id UUID PK, tenant_id UUID FK, email TEXT, role user_role_enum, org_id UUID FK,
      status user_status_enum, created_at TIMESTAMPTZ)

-- 004_customers
customers(id UUID PK, tenant_id UUID FK, external_id TEXT, 
          name TEXT, phone TEXT, alt_phone TEXT, language TEXT,
          dnd_status BOOLEAN, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)

-- 005_loan_accounts
loan_accounts(id UUID PK, tenant_id UUID FK, customer_id UUID FK, 
              account_number TEXT UNIQUE, principal_amount NUMERIC(15,2), currency CHAR(3),
              emi_amount NUMERIC(15,2), dpd INTEGER, outstanding_amount NUMERIC(15,2),
              loan_status loan_status_enum, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)

-- 006_emi_schedule
emi_schedule(id UUID PK, loan_account_id UUID FK, due_date DATE, amount NUMERIC(15,2),
             status emi_status_enum, paid_at TIMESTAMPTZ NULL)

-- 007_promises_to_pay
promises_to_pay(id UUID PK, idempotency_key TEXT UNIQUE, tenant_id UUID FK, 
                loan_account_id UUID FK, customer_id UUID FK,
                amount NUMERIC(15,2), currency CHAR(3), promise_date DATE,
                status ptp_status_enum, created_by_call_id TEXT,
                created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)

-- 008_consents
consents(id UUID PK, tenant_id UUID FK, customer_id UUID FK, 
         consent_type consent_type_enum, status consent_status_enum,
         granted_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ NULL,
         call_id TEXT, recorded_audio_path TEXT NULL)

-- 009_idempotency_keys
idempotency_keys(key TEXT PK, tenant_id UUID, result JSONB, 
                 created_at TIMESTAMPTZ, expires_at TIMESTAMPTZ)

-- 010_audit_log
audit_log(id UUID PK, tenant_id UUID, event_type TEXT, actor_id TEXT, 
          resource_type TEXT, resource_id TEXT, action TEXT,
          outcome TEXT, details JSONB, occurred_at TIMESTAMPTZ,
          ip_address INET NULL, trace_id TEXT)

-- 011_campaigns
campaigns(id UUID PK, tenant_id UUID FK, name TEXT, status campaign_status_enum,
          audience_criteria JSONB, schedule_config JSONB, retry_policy JSONB,
          ab_test_config JSONB NULL, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)

-- 012_billing_subscriptions
billing_subscriptions(id UUID PK, tenant_id UUID FK UNIQUE, 
                       tier subscription_tier_enum, started_at TIMESTAMPTZ,
                       current_period_start TIMESTAMPTZ, current_period_end TIMESTAMPTZ,
                       status subscription_status_enum)

-- 013_usage_events
usage_events(id UUID PK, tenant_id UUID FK, usage_type usage_type_enum,
             quantity NUMERIC(15,4), unit TEXT, occurred_at TIMESTAMPTZ,
             call_id TEXT NULL, campaign_id UUID NULL)
```

**Indexes (all include tenant_id in composite index for per-tenant isolation):**
- `customers`: (tenant_id, external_id), (tenant_id, phone)
- `loan_accounts`: (tenant_id, customer_id), (tenant_id, account_number)
- `promises_to_pay`: (tenant_id, loan_account_id), (tenant_id, customer_id), idempotency_key UNIQUE
- `audit_log`: (tenant_id, occurred_at), (tenant_id, resource_type, resource_id)
- `usage_events`: (tenant_id, occurred_at), (tenant_id, usage_type)

### MongoDB Collections (`scripts/db/mongodb/`)

Index definitions for:
- `response_plans`: `{call_id: 1, tenant_id: 1}`, `{plan_id: 1}` (unique), TTL on `created_at` (90 days)
- `decision_envelopes`: `{call_id: 1, tenant_id: 1}`, `{envelope_id: 1}` (unique), TTL on `created_at` (90 days)
- `call_transcripts`: `{call_id: 1, tenant_id: 1}` (unique), TTL on `created_at` (configurable retention)
- `call_lineage`: `{call_id: 1, tenant_id: 1}`, TTL on `created_at` (90 days)

### Repositories (`src/libs/repositories/`)

```
src/libs/repositories/
├── __init__.py
├── base.py                 (BaseRepository: DB session, tenant scoping)
├── customer.py             (CustomerRepository: CRUD + find_by_phone, find_by_external_id)
├── loan_account.py         (LoanAccountRepository: CRUD + find_by_customer, outstanding_balance)
├── promise_to_pay.py       (PromiseToPay: create_idempotent, update_status, find_by_loan)
├── consent.py              (ConsentRepository: check_consent, record_grant, record_revoke)
├── idempotency.py          (IdempotencyRepository: check, record)
├── audit.py                (AuditRepository: append_only_insert — no updates or deletes)
├── campaign.py             (CampaignRepository: CRUD + find_active_for_tenant)
└── billing.py              (BillingRepository, UsageRepository)
```

**Tenant scoping invariant:** Every repository method MUST include `tenant_id` in WHERE clause. `BaseRepository` enforces this via a query builder that appends tenant filter.

---

## Files Expected to Change

**New:** `scripts/db/migrations/` (Alembic files), `scripts/db/mongodb/` (index files), `src/libs/repositories/` (all files)  
**New:** `tests/integration/repositories/` (integration tests using TestPostgres fixture)

---

## Acceptance Criteria

- [x] All 13 migration files run successfully against a fresh Postgres database (`alembic upgrade head`)
- [x] All migrations are reversible (`alembic downgrade -1` for each / `downgrade base`)
- [x] Tenant scoping: `CustomerRepository.find_by_phone(tenant_id, phone)` does not return records from other tenants (integration test with 2 tenants)
- [x] `AuditRepository` raises on any UPDATE or DELETE attempt (immutability enforcement) — plus a DB-level trigger as defense-in-depth
- [x] `IdempotencyRepository.check()` returns existing result for duplicate key; does not insert
- [x] MongoDB indexes are created correctly (verified against the CPU node's real MongoDB — `scripts/db/mongodb/create_indexes.py`)

---

## Required Tests

**Integration (real Postgres + MongoDB via Docker):**
- `test_migrations_upgrade_downgrade` — head then all the way back to base
- `test_tenant_isolation_customer` — 2 tenants, customer in tenant-1 not visible from tenant-2
- `test_audit_no_update` — AuditRepository.update() → raises
- `test_idempotency_double_key` — same key twice → second returns cached result
- `test_ptp_create_returns_existing_on_duplicate_key` — idempotent PTP creation

---

## Definition of Done

- [x] All AC items checked
- [x] All migration tests pass
- [x] All repository integration tests pass
- [x] Tenant scoping verified via cross-tenant isolation test
- [x] CI green (ruff, ruff format, mypy --strict, check_boundaries, pytest, coverage all pass locally and on the CPU node)
- [x] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [x] `CURRENT_SPRINT.md` updated to Sprint-015

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** All migrations and repository tests run against `TestPostgres` and `TestMongo` Docker fixtures available locally.

### Files Created

- `alembic.ini`; `scripts/db/migrations/alembic/{env.py,script.py.mako,_ddl_helpers.py,versions/0001_tenants.py … 0013_usage_events.py}` (13 Alembic revisions, formalizing the Sprint-002 raw-SQL schema)
- `scripts/db/mongodb/create_indexes.py` (applies the 4 pre-existing index JSON specs; `response_plans_indexes.json`/`decision_envelopes_indexes.json`/`call_transcripts_indexes.json`/`call_lineage_indexes.json` extended with 2 missing TTL indexes)
- `src/libs/repositories/__init__.py`, `base.py`, `customer.py`, `loan_account.py`, `promise_to_pay.py`, `consent.py`, `idempotency.py`, `audit.py`, `campaign.py`, `billing.py`
- `tests/unit/libs/repositories/` (8 unit test files, mocked-cursor based, 56 tests)
- `tests/fixtures/fake_pg.py` (mocked psycopg2-compatible test double)
- `tests/integration/repositories/conftest.py`, `test_tenant_isolation.py`, `test_audit_repository.py`, `test_idempotency_repository.py`, `test_ptp_repository.py`, `test_migration_upgrade_downgrade.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Postgres (integration) | `TestPostgres` Docker fixture (Sprint-003) | Real `alembic upgrade head` + `downgrade -1` against test DB |
| MongoDB (integration) | `TestMongo` Docker fixture (Sprint-003) | Real index creation on test MongoDB |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Migration up | `alembic upgrade head` (test DB) | All 13 migrations apply cleanly |
| Migration down | `alembic downgrade -1` × 13 (test DB) | All reverse cleanly |
| Integration tests | `pytest tests/integration/repositories/` | All pass |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- All 13 migrations apply and reverse cleanly against a fresh Postgres DB
- `CustomerRepository.find_by_phone(tenant_id="tenant-A", phone="...")` does not return tenant-B records
- `AuditRepository.update()` → raises (immutability enforcement)
- `IdempotencyRepository.check(key)` → returns existing result on second call; no second insert
- MongoDB indexes created: `response_plans`, `decision_envelopes`, `call_transcripts`, `call_lineage`

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Action | What | Why |
|---|---|---|
| Run Alembic migrations | Against production Postgres | Creates all 13 authoritative schema tables |
| Create MongoDB indexes | Against production MongoDB | Creates 4 collection indexes with TTLs |
| Deploy repository libraries | Integrated into ConversationEngine + RelationshipMemoryService | Repositories are libraries, not standalone services |

**No new standalone services are deployed this sprint.** The repository layer is a library that will be consumed by services starting in Sprint-015 and onward.

**Previously deployed services that remain running:**
- All Sprint-004–013 services

**Deployment procedure:**
1. Confirm Postgres connection string available (from environment or secrets)
2. Run `alembic upgrade head` against production Postgres — verify each migration in sequence
3. Inspect created tables: `\dt` in psql → confirm all 13 tables present
4. Create MongoDB indexes: `python scripts/db/mongodb/create_indexes.py`
5. Verify index creation: `db.response_plans.getIndexes()` → 3 indexes present

**Health checks:**
- Postgres: `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'` → 13 tables minimum
- MongoDB: `db.response_plans.getIndexes()` → TTL index present with 90-day expiry
- `alembic current` → shows `head` revision
- No migration errors in deployment logs

**Integration validation:**
- Cross-tenant isolation: insert customer in tenant-A → query with tenant-B filter → 0 results
- `idempotency_keys` table: insert → re-insert same key → unique constraint enforced
- `audit_log` table: INSERT succeeds; UPDATE → foreign key/trigger violation

**Rollback procedure:**
- Schema rollback: `alembic downgrade -1` (reversible for each migration)
- Data rollback: not applicable — migration only adds new tables; no existing data affected
- MongoDB indexes: drop via `db.<collection>.dropIndex("<index_name>")`

### GPU Node

> **GPU node is not required during this sprint.** No GPU inference. Previously deployed GPU services (Whisper, Qwen, Veena) remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- `alembic current` → confirms `head` on production Postgres
- All 13 tables visible in Postgres (`\dt public.*`)
- Tenant isolation: cross-tenant query returns empty result set (confirmed via psql)
- `audit_log` immutability: UPDATE attempt → error logged + rejected
- MongoDB TTL indexes: verify `expireAfterSeconds=7776000` (90 days) set on `created_at`

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- ConversationEngine → Postgres: latency < 20ms for simple SELECT on `customers` table
- RelationshipMemoryService → Postgres: latency < 20ms for `relationship_memory` queries

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — still passes (Postgres now has correct schema)
- ConversationQualityScorer: quality scores still persisted correctly to MongoDB
- All Sprint-013 event bus tests: `pytest tests/integration/libs/test_event_bus_integration.py`

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [x] All 13 Postgres migrations created and reversible
- [x] All 4 MongoDB index files created (plus 2 missing TTL indexes added: `idx_de_ttl`, `idx_cl_ttl`)
- [x] All repository classes implemented with tenant scoping
- [x] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [x] Migration up/down cycle passes (scratch database — see Deviations)
- [x] Tenant isolation test passes
- [x] AuditRepository immutability enforced
- [x] Coverage ≥ 85% (93.38% local)
- [x] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [x] `alembic upgrade head` completed on production Postgres — 30 tables present (29 Sprint-002 baseline + `alembic_version`)
- [x] MongoDB indexes created on all 4 collections with correct TTLs (22 indexes total)
- [x] Cross-tenant isolation verified on production Postgres
- [x] `audit_log` UPDATE and DELETE rejection confirmed on production DB (both repository-level and DB-trigger-level)
- [x] All regression tests pass (1297 passed / 1 skipped on the CPU node against real Redis/Postgres/MongoDB)
- [x] Deployment remains active as baseline for Sprint-015

---

## Implementation Notes & Deviations

- **Schema already existed:** Sprint-002 had already created all 13 logical tables (and more) as raw idempotent SQL files (`scripts/db/migrations/001_customers_and_parties.sql` … `011_relationship_memory.sql`), and all 4 MongoDB index JSON specs already existed but were never applied to a real database (confirmed: 0 Mongo collections before this sprint). Sprint-014's actual net-new work was: (1) Alembic as formal migration tooling wrapping that schema, (2) the entire repository layer (did not exist), (3) `scripts/db/mongodb/create_indexes.py` to actually apply the long-defined index specs.
- **13 Alembic revisions map to the spec's 13 named migrations**, each wrapping the pre-existing idempotent DDL (`CREATE TABLE IF NOT EXISTS`) for the corresponding table group, plus any additive (expand-only) columns/constraints layered on top — safe to run against both a fresh database and the CPU node's already-populated production database.
- **Money/enum representation:** kept the already-deployed, DM-2-compliant convention (`amount_minor BIGINT` + `currency CHAR(3)`, not `NUMERIC(15,2)`) rather than the spec's illustrative DDL, since the real tables already use minor-unit integers matching the `Money` contract. "Proper enums" is satisfied via idempotent `CHECK` constraints (`ck_<table>_<column>_enum`) added through a guarded `DO` block helper (`_ddl_helpers.py`), since Postgres has no `ADD CONSTRAINT IF NOT EXISTS` and native `ENUM` types can't have values added without an `ALTER TYPE`.
- **Idempotency-critical fix during implementation:** columns/constraints added to a table that *already exists* (e.g. `promises_to_pay.idempotency_key`, `idempotency_keys.result`, `billing_subscriptions`'s tenant-uniqueness) must be issued as standalone `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` / guarded `ADD CONSTRAINT` statements — putting them inline inside a `CREATE TABLE IF NOT EXISTS` body silently no-ops against an already-existing table and the column would never actually be added. Caught and fixed for all three cases before Phase 2.
- **`audit_log` immutability is enforced twice**: `AuditRepository.update()`/`.delete()` raise in pure Python before any SQL executes, and migration 0010 adds a `BEFORE UPDATE OR DELETE` Postgres trigger as defense-in-depth against direct SQL access bypassing the repository. Verified on the CPU node: a raw `UPDATE audit_log ...` is rejected with `audit_log is append-only: UPDATE is not permitted`.
- **`test_migrations_upgrade_downgrade` runs against a disposable scratch database** (`voiceos_migrations_scratch`), never against the shared `POSTGRES_DSN` database or the CPU node's production `voiceos` database — `alembic downgrade base` drops every table via CASCADE, which would be destructive against a database other services depend on. This mirrors Sprint-014.md's own placement of this check under Phase 1 "mock validation" against a throwaway database.
- **No local Postgres/MongoDB on the Windows dev machine**: all `requires_postgres`/`requires_mongodb`-gated tests were structurally validated and skip locally, then run for real (10/10 passing) via SSH on the CPU node in Phase 2 — the same pattern used by every prior sprint's DB integration tests.
- **Dependencies promoted/added** (`pyproject.toml`): `psycopg2-binary` promoted from dev-only to core (now imported by production repository code, not just test fixtures — same precedent as Sprint-013's `redis` promotion); `alembic` and `sqlalchemy` added to core (alembic's connection/engine layer only — no ORM models; repositories remain raw-SQL/psycopg2, consistent with `RelationshipMemoryStore`, Sprint-010).
- **`deployment/cpu/restore.sh`** already referenced `alembic upgrade head` (written ahead of this sprint) but `cd`'d into `implementation/`, where no `alembic.ini` exists — fixed to `cd` into the repo root where `alembic.ini` actually lives, and added the `create_indexes.py` step.
- **Pre-existing, out-of-scope condition observed during Phase 2 health checks** (not a Sprint-014 regression): the Sprint-013 EventBus consumer group (`main-group` on `voiceos-events`) was again absent on the CPU node — Redis has no persistence configured (`appendonly no`), the exact risk already tracked as **TT-002**. Not remediated here; Redis/EventBus is outside Sprint-014's Postgres/MongoDB scope.

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- **§12 Database Schema:** Add all Alembic migration entries — revision `0001_full_schema` applied; list all 13 Postgres tables and 4 MongoDB collections with their index definitions and TTLs
- Update Postgres health check command to include table count verification (§14)
- Update MongoDB entry (§7.3) with all 4 collection names and index specifications
- Note: no new application services deployed; repositories are library packages

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-012.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/restore.sh` | Ensure `alembic upgrade head` step is in restore sequence |
| `deployment/cpu/healthcheck.sh` | Add `alembic current` check to confirm migration version |

### DR Validation

**CPU node rebuild test (migration critical):**
```bash
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh
# restore.sh must run: alembic upgrade head
```

**Migration verification:**
```bash
# On rebuilt CPU node:
cd /opt/voiceos/app/implementation && alembic current
# Expected: at head revision (0001_full_schema or latest)

psql -h $POSTGRES_HOST -U voiceos -d voiceos -c "\dt"
# Expected: all 13 tables present

mongosh "$MONGO_URI" --eval "db.getCollectionNames()"
# Expected: call_lineage, response_plans, transcripts, call_quality all present
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; cross-tenant isolation verified
```
