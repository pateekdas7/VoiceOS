# Sprint-002 — Event Contracts & Data Models

**Epic:** E1 — Foundation & Engineering Infrastructure  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** Sprint-001  
**Blocks:** Sprint-003, Sprint-014, Sprint-017  

---

## Objective

Complete the full event contract library — all DomainEvent subtypes for every domain — and all persistent data models (Postgres schema DDL, MongoDB collection shapes). This gives every service a complete set of typed models to work with.

---

## Architecture References

- Volume 3: Ch3 (Event Bus — DomainEvent schema), Ch5 (Persistent Storage — Postgres + MongoDB models)
- Volume 5: Ch4 (CRM — Customer, Party), Ch5 (Collections — LoanAccount, PTP)
- Volume 6: Ch6 (Event & Message Standards), Ch7 (Data Modeling Standards)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### 1. Domain Event Subtypes (`src/libs/contracts/events/`)
All DomainEvent subtypes, one file per domain:

- `audio_events.py` — AudioSessionStarted, AudioFrameReceived, BargeinDetected, VADSpeechStart, VADSpeechEnd, AudioSessionEnded
- `dialogue_events.py` — TurnStarted, TurnCompleted, ResponsePlanCreated, PlaybackStarted, PlaybackCompleted, PlaybackFlushed
- `intelligence_events.py` — IntentClassified, EntityExtracted, RiskFlagRaised, StrategySelected, NegotiationMoveProposed, ResponsePlanAssembled
- `reliability_events.py` — SnapshotCreated, RecoveryStarted, RecoveryCompleted, IdempotencyKeyCreated, CircuitBreakerOpened, CircuitBreakerClosed
- `compliance_events.py` — ConsentRecorded, ConsentRevoked, PolicyDecisionMade, AuditEventEmitted, PIIRedacted, DataErasureRequested
- `saas_events.py` — TenantProvisioned, TenantSuspended, CustomerCreated, LoanAccountUpdated, PTPreated, CampaignStarted, CallDispositioned, UsageEventRecorded, BillingInvoiceGenerated

### 2. Persistent Data Models (`src/libs/contracts/models/`)
- `customer.py` — Customer, Party (borrower/co-borrower/guarantor), ContactInfo, Address
- `loan.py` — LoanAccount, EMISchedule, EMIEntry, DPDRecord, OutstandingBalance
- `collections.py` — PromiseToPay, PTPStatus, Settlement, SettlementStatus, CallbackRequest, EscalationRecord
- `consent.py` — Consent, ConsentType, ConsentStatus, ConsentRecord
- `campaign.py` — Campaign, CampaignStatus, AudienceCriteria, RetryPolicy, ABTestVariant
- `tenant.py` — Tenant, TenantStatus, IsolationProfile, Organization, BusinessUnit, Branch
- `user.py` — User, Role, RoleAssignment, OrgScope
- `billing.py` — BillingSubscription, SubscriptionTier, UsageEvent, UsageType, Invoice, InvoiceStatus

### 3. Database Schemas
- `scripts/db/migrations/` — Alembic migration files for all Postgres tables:
  - `001_customers_and_parties.sql`
  - `002_loan_accounts_and_emi.sql`
  - `003_promises_to_pay.sql`
  - `004_consents.sql`
  - `005_idempotency_keys.sql`
  - `006_audit_log.sql`
  - `007_tenants_and_orgs.sql`
  - `008_users_and_roles.sql`
  - `009_campaigns.sql`
  - `010_billing_and_usage.sql`
- `scripts/db/mongodb/` — MongoDB collection index definitions:
  - `response_plans_indexes.json`
  - `decision_envelopes_indexes.json`
  - `call_transcripts_indexes.json`
  - `call_lineage_indexes.json`

---

## Files Expected to Change

**New:** `src/libs/contracts/events/` (all event files), `src/libs/contracts/models/` (all model files), `scripts/db/migrations/` (Alembic files), `scripts/db/mongodb/` (index files)

**Modified:** `src/libs/contracts/__init__.py` — re-export all new types

---

## Acceptance Criteria

- [ ] All event subtypes are proper subclasses of `DomainEvent` with correctly typed fields
- [ ] All persistent models are fully typed and pass `mypy --strict`
- [ ] All Postgres migration files are valid SQL (can be `alembic upgrade head` against empty DB)
- [ ] MongoDB index files are valid JSON
- [ ] No circular imports between any contracts modules
- [ ] `src/libs/contracts/__init__.py` exports all public types from all sub-modules

---

## Required Tests

- `tests/unit/contracts/test_domain_events.py` — instantiate one event per type, check `event_type` and `payload` serialization
- `tests/unit/contracts/test_models.py` — instantiate each model with valid fields, test field validation
- `tests/integration/test_migrations.py` — run `alembic upgrade head` and `alembic downgrade base` against test Postgres (Docker)

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass (unit + migration integration test)
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-003
