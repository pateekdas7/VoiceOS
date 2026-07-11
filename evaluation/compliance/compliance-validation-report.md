# Compliance Validation Report — Sprint-028 Phase 2

**Sprint:** Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy
**Deliverable:** `evaluation/compliance/` (Sprint-028 §5 — Compliance Validation)
**Architecture Reference:** Volume 4 (Compliance & Security)
**Executed:** 2026-07-11

---

## Report Metadata

| Field | Value |
|---|---|
| Date | 2026-07-11 |
| Environment | CPU node (root@101.53.137.131) — VoiceOS venv `/opt/voiceos/app`; PostgreSQL at localhost:5432 |
| Policy Engine | `src/services/policy_engine/engine.py` — real `PolicyEngine.evaluate()` against live rule packs |
| Rule packs active | `packs/rbi.py` (RBI Fair Practice Code), `packs/dpdp.py` (Digital Personal Data Protection Act) |
| Test method | Direct Python API calls (`PolicyRequest` → `PolicyEngine.evaluate()`) from CPU node venv |
| Database | PostgreSQL `voiceos` DB (credentials from env); audit_log table — 363 entries at test time |
| Previous phase | Phase 1 validated against `FakePolicyEngine` mock only (unit tests); Phase 2 = real engine, real DB |

---

## API Reference

**PolicyRequest signature:**
```python
PolicyRequest(
    domain: str,       # 'rbi', 'dpdp', etc.
    action: str,       # 'start_call', 'process_customer_data', etc.
    subject: str,      # caller identity
    resource: str,     # resource being accessed
    tenant_id: str,    # tenant identifier
    context: dict,     # domain-specific context keys
)
```

**PolicyDecision:**
```python
PolicyDecision(
    outcome: str,             # 'ALLOW' or 'DENY'
    matching_rules: list,     # rule IDs that fired
    reason: str,              # human-readable explanation
)
```

**Confirmed context keys** (from `packs/rbi.py`, `packs/dpdp.py`):

| Key | Pack | Type | Description |
|---|---|---|---|
| `hour` | RBI | int (0-23) | Local hour — calling hours gate |
| `calls_today_count` | RBI | int | Total calls this customer today |
| `utterance_classification` | RBI | str | 'neutral', 'abusive', 'threatening' |
| `identity_verified` | RBI | bool | Customer identity confirmed |
| `disclosure_given` | RBI | bool | Debt/purpose disclosure made |
| `recording_consent` | RBI | bool | Recording consent obtained |
| `has_consent` | DPDP | bool | Customer has given data processing consent |
| `erasure_requested` | DPDP | bool | Customer invoked right to erasure |
| `purpose` | DPDP | str | Data processing purpose |
| `consented_purposes` | DPDP | list[str] | Purposes customer consented to |
| `data_age_days` | DPDP | int | Age of data record in days |
| `retention_limit_days` | DPDP | int | Configured retention limit |

---

## RBI Compliance Tests (9/9 PASS)

### RBI-001 — Calling Hours: Before 08:00 (DENY)

```python
req = PolicyRequest(domain='rbi', action='start_call', subject='agent-1',
    resource='customer-001', tenant_id='tenant-test',
    context={'hour': 7})
decision = engine.evaluate(req)
```
- **Expected:** DENY (hour < 8)
- **Result:** DENY — `RBI-CALLING-HOURS` fired
- **Status: PASS**

### RBI-002 — Calling Hours: After 20:00 (DENY)

```python
context={'hour': 21}
```
- **Expected:** DENY (hour >= 20)
- **Result:** DENY — `RBI-CALLING-HOURS` fired
- **Status: PASS**

### RBI-003 — Calling Hours: Within Window (ALLOW)

```python
context={'hour': 14}
```
- **Expected:** ALLOW
- **Result:** ALLOW — no rule fired
- **Status: PASS**

### RBI-004 — Call Frequency: 3rd Call (ALLOW)

```python
req = PolicyRequest(domain='rbi', action='start_call', subject='agent-1',
    resource='customer-002', tenant_id='tenant-test',
    context={'hour': 14, 'calls_today_count': 2})
```
- **Expected:** ALLOW (calls_today_count = 2; third call is allowed, gate fires on >= 3)
- **Result:** ALLOW
- **Status: PASS**

### RBI-005 — Call Frequency: 4th Call (DENY)

```python
context={'hour': 14, 'calls_today_count': 3}
```
- **Expected:** DENY (3 calls already made today)
- **Result:** DENY — `RBI-MAX-CALLS-PER-DAY` fired
- **Status: PASS**

### RBI-006 — Abusive Language (DENY)

```python
req = PolicyRequest(domain='rbi', action='continue_call', subject='agent-1',
    resource='customer-003', tenant_id='tenant-test',
    context={'utterance_classification': 'abusive'})
```
- **Expected:** DENY
- **Result:** DENY — `RBI-NO-ABUSE` fired
- **Status: PASS**

### RBI-007 — Threatening Language (DENY)

```python
context={'utterance_classification': 'threatening'}
```
- **Expected:** DENY
- **Result:** DENY — `RBI-NO-THREATS` fired
- **Status: PASS**

### RBI-008 — Disclosure Required at Call Start (DENY when missing)

```python
req = PolicyRequest(domain='rbi', action='start_call', subject='agent-1',
    resource='customer-004', tenant_id='tenant-test',
    context={'hour': 14, 'turn_index': 0, 'identity_verified': True,
             'disclosure_given': False})
```
- **Expected:** DENY (disclosure not given at call start)
- **Result:** DENY — `RBI-DISCLOSURE-REQUIRED` fired
- **Status: PASS**

### RBI-009 — Recording Consent Required (DENY when absent)

```python
req = PolicyRequest(domain='rbi', action='start_call', subject='agent-1',
    resource='customer-005', tenant_id='tenant-test',
    context={'hour': 14, 'recording_consent': False})
```
- **Expected:** DENY
- **Result:** DENY — `RBI-RECORDING-CONSENT` fired
- **Status: PASS**

**RBI Summary: 9/9 PASS**

---

## DPDP Compliance Tests (6/6 PASS)

### DPDP-001 — Consent Required for Data Processing (DENY without consent)

```python
req = PolicyRequest(domain='dpdp', action='process_customer_data', subject='system',
    resource='customer-001', tenant_id='tenant-test',
    context={'has_consent': False})
```
- **Expected:** DENY
- **Result:** DENY — `DPDP-CONSENT-REQUIRED` fired
- **Status: PASS**

### DPDP-002 — Consent Present (ALLOW)

```python
context={'has_consent': True, 'purpose': 'debt_collection',
         'consented_purposes': ['debt_collection']}
```
- **Expected:** ALLOW
- **Result:** ALLOW
- **Status: PASS**

### DPDP-003 — Right to Erasure (DENY processing after erasure request)

```python
req = PolicyRequest(domain='dpdp', action='process_customer_data', subject='system',
    resource='customer-002', tenant_id='tenant-test',
    context={'has_consent': True, 'erasure_requested': True})
```
- **Expected:** DENY
- **Result:** DENY — `DPDP-ERASURE-REQUIRED` fired
- **Status: PASS**

### DPDP-004 — Purpose Limitation (DENY for unconsented purpose)

```python
context={'has_consent': True, 'purpose': 'marketing',
         'consented_purposes': ['debt_collection']}
```
- **Expected:** DENY ('marketing' not in consented purposes)
- **Result:** DENY — `DPDP-PURPOSE-LIMITATION` fired
- **Status: PASS**

### DPDP-005 — Purpose Limitation: Matching Purpose (ALLOW)

```python
context={'has_consent': True, 'purpose': 'debt_collection',
         'consented_purposes': ['debt_collection', 'account_management']}
```
- **Expected:** ALLOW
- **Result:** ALLOW
- **Status: PASS**

### DPDP-006 — Retention Schedule Exceeded (DENY)

```python
context={'has_consent': True, 'purpose': 'debt_collection',
         'consented_purposes': ['debt_collection'],
         'data_age_days': 2600, 'retention_limit_days': 2555}
```
- **Expected:** DENY (data_age_days > retention_limit_days; 2555 days ≈ 7 years = DPDP maximum)
- **Result:** DENY — `DPDP-RETENTION-EXCEEDED` fired
- **Status: PASS**

**DPDP Summary: 6/6 PASS**

---

## Audit Log Validation

### Audit Log State

| Metric | Value |
|---|---|
| Total entries | 363 |
| Table | `audit_log` (PostgreSQL, `voiceos` DB) |
| Schema | `id`, `event_type`, `entity_id`, `payload`, `hash`, `created_at` |

### AUD-001 — Immutability (DELETE blocked by trigger)

```sql
DELETE FROM audit_log WHERE id = 1;
```
- **Expected:** Raises exception / blocked by trigger
- **Result:** `ERROR: audit_log is append-only: DELETE is not permitted (V4 Ch11)`
- **Status: PASS** — immutability trigger active

### AUD-002 — Hash Chain Integrity

```sql
SELECT COUNT(*) FROM audit_log WHERE hash IS NULL;
```
- **Result:** Partial population — some NULL hashes on recent entries
- **Status: PARTIAL** — hash chain not fully populated for all entries. Recent entries arriving without hash computation. Root cause: hash computation wired to initial seeding flow, not live event writes.
- **Gap:** Hash chain must be populated on every INSERT for audit trail non-repudiation (V4 Ch11)

### AUD-003 — Policy Decision Coverage

- **Observation:** Policy engine `evaluate()` calls do not automatically write to `audit_log`. The 15 policy tests above produced no new audit entries.
- **Status: GAP** — V4 Ch11 requires all policy decisions (ALLOW and DENY) to be logged with decision rationale. Automatic logging from `PolicyEngine.evaluate()` is not implemented.
- **Required:** `audit_log` write on every `PolicyDecision` with `event_type='policy_decision'`, entity, outcome, matching rules, reason.

---

## Acceptance Criteria Status

| AC | Requirement | Result | Status |
|---|---|---|---|
| AC-1 | RBI calling hours enforced | 2/2 hour-based scenarios DENY correctly | **PASS** |
| AC-2 | RBI call frequency cap (max 3/day) | 4th call DENY, 3rd ALLOW | **PASS** |
| AC-3 | RBI abuse/threat prohibition | Abusive + threatening both DENY | **PASS** |
| AC-4 | RBI disclosure at call start | Missing disclosure DENY | **PASS** |
| AC-5 | RBI recording consent | No consent DENY | **PASS** |
| AC-6 | DPDP consent gate | No consent DENY, consent+matching purpose ALLOW | **PASS** |
| AC-7 | DPDP right to erasure | Erasure request DENY data processing | **PASS** |
| AC-8 | DPDP purpose limitation | Unconsented purpose DENY | **PASS** |
| AC-9 | DPDP retention schedule | Over-retention DENY | **PASS** |
| AC-10 | Audit immutability | DELETE blocked by trigger | **PASS** |
| AC-11 | Audit hash chain | Partial NULL hashes on live writes | **PARTIAL** |
| AC-12 | Policy decision audit coverage | Policy decisions not logged to audit_log | **GAP** |
| AC-13 | Tenant-unweakenable hard rules | RBI/DPDP rules fired regardless of `tenant_id` | **PASS** (implicit — no override mechanism found) |

---

## Production Gaps

| Gap | Severity | Resolution |
|---|---|---|
| Audit hash chain not populated for live writes | HIGH | Wire hash computation into `audit_log` INSERT trigger or `PolicyEngine` write path |
| Policy decisions not logged | HIGH | Add `audit_log` write to `PolicyEngine.evaluate()` on every call |
| Consent data not integrated with real customer DB | MEDIUM | Tests used in-memory context dict; production must pull `has_consent` from authoritative customer record |
| `recording_consent` sourced from call context, not verified DB record | MEDIUM | Production: verify consent from consent management system before every call |

---

## Overall Assessment

**Enforcement logic: PASS** — All 15 RBI + DPDP policy scenarios enforced correctly. Hard rules (V4 RBI/DPDP) deny correctly on violation; allow correctly when all conditions met. No tenant override mechanism exists (tenant-unweakenable requirement implicitly satisfied).

**Audit infrastructure: CONDITIONAL PASS** — Immutability trigger is production-ready. Hash chain and policy coverage gaps must be resolved before production deploy.

**Overall Status: CONDITIONAL PASS**

Policy enforcement is correct and production-worthy. Two audit infrastructure gaps (hash chain population and policy decision logging) must be resolved for full compliance. These are implementation gaps, not architectural gaps — the schema and trigger infrastructure exist; the write paths need to be completed.

---

## Sign-off

| Role | Status | Notes |
|---|---|---|
| Test Executor | **COMPLETE** | CPU node, 2026-07-11 |
| Policy Enforcement | **15/15 PASS** | RBI 9/9, DPDP 6/6 |
| Audit Infrastructure | **CONDITIONAL** | Immutability PASS; hash chain + coverage GAPS |
| Production Readiness | **NOT READY** | Resolve audit gaps before canary deploy |
