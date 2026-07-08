# Sprint-020 — PII Protection, Audit, API Security & AI Safety

**Epic:** E5 — Compliance & Security  
**Status:** ✅ **DONE** (2026-07-05)  
**Depends on:** Sprint-018, Sprint-019  
**Blocks:** Sprint-021  
**Milestone:** Compliance & Security Complete — M-5 ✅ **Reached**  

---

## Final Status: COMPLETE (2026-07-05)

Both phases done and validated against real infrastructure on the CPU node. See `CHANGELOG.md`'s Sprint-020 entry for full detail. Notable deviations from this document's illustrative prose (none architecturally significant):

- **Service directory names are underscored** (`src/services/compliance_monitoring/`, `src/services/incident_response/`), not hyphenated as written above — matches every other service package and is what Python's import system actually requires.
- **`PIIDetector` is regex/heuristic, not NER-based.** The authoritative Volume 4 Ch10 spec (§10.3, §10.12, §10.20) says to reuse existing entity-extraction hooks rather than build a parallel detector, and explicitly lists ML-based detection as a *future* improvement — not this sprint's baseline. `src/engines/entity_extraction/` (Sprint-010) already established this "no ML model required" precedent for the same category of Hindi/English PII-adjacent entities.
- **No new `ConsentManagementService` was built.** `consents`/`consent_records` were already fully implemented in Sprint-014; this sprint's own Components list never specified a new service, only documentation of the existing capability.
- **`ComplianceMonitoringService`/`IncidentResponseService` are in-process library façades**, not standalone K8s Deployments — same pre-Sprint-026 precedent every VoiceOS service has followed since Sprint-013 (no service ships a standalone HTTP/gRPC listener before Sprint-026 IaC/K8s work).
- 2 real-infra-only issues found and fixed during Phase 2: a stale migration-head test assertion, and a `healthcheck.sh` `REDIS_URL`-clobbering bug that also caused a real Redis password to briefly leak into a validation script's stdout (fixed in that script and 5 other pre-existing scripts sharing the same pattern) — see BACKLOG.md TT-007.
- 1 open, non-blocking gap: MongoDB integration tests/health checks need a Mongo credential this session didn't have (Sprint-019 scope, not Sprint-020) — see BACKLOG.md TT-008.

---

## Objective

Complete the compliance and security layer: PII detection and redaction, the immutable audit trail, API security hardening, runtime security controls, and AI safety mechanisms. After this sprint, the system enforces all security and compliance requirements by construction.

---

## Architecture References

- Volume 4: Ch10 (PII Protection — detection, redaction, tokenization), Ch11 (Audit Architecture — immutable, tamper-evident), Ch12 (API Security), Ch13 (Runtime Security), Ch14 (AI Safety — content moderation, prompt injection, human oversight), Ch16 (Compliance Monitoring — real-time signal correlation, violation alerting), Ch17 (Incident Response — implemented service with playbook execution and regulatory notification timers)
- DocSuite-05: Configuration Reference

---

## Components to Implement

### `src/libs/pii/`

```
src/libs/pii/
├── __init__.py
├── detector.py             (PIIDetector: NER + regex for PII entity detection)
├── redactor.py             (PIIRedactor: masks PII in text with [REDACTED] or category label)
├── tokenizer.py            (PIITokenizer: reversible tokenization with access-controlled lookup)
└── entities.py             (PIIEntity: Aadhaar, PAN, phone, account_number, UPI_ID, name, amount)
```

**PIIDetector:**
- Rule-based patterns: Aadhaar (12-digit), PAN (AAAAA9999A format), phone (10-digit, +91 prefix), account number (10-18 digits), UPI ID (UPI pattern)
- NER-based: name extraction using IndicNER or spaCy Hindi model
- `detect(text: str) -> list[PIISpan]` where `PIISpan(start, end, entity_type, value)`

**PIIRedactor:**
- `redact(text: str) -> str` — replaces PII spans with `[{ENTITY_TYPE}]` (e.g., "[PHONE]")
- Applied to ALL log messages before they are emitted (in StructuredLogger)
- Applied to transcripts stored in MongoDB

**PIITokenizer:**
- `tokenize(value: str, entity_type: PIIEntity) -> str` — returns opaque token (e.g., "PHONE_7f3a2b")
- `detokenize(token: str, actor: AuthContext) -> str` — reverses tokenization; AUDITOR role required
- Tokens stored in Redis (TTL = session) for transient, and Postgres for persistent

### `src/libs/audit/`

```
src/libs/audit/
├── __init__.py
├── logger.py               (AuditLogger: append-only, tamper-evident log writer)
├── event.py                (AuditEvent: all required fields)
├── verifier.py             (AuditVerifier: checks hash chain integrity)
└── search.py               (AuditSearch: filtered queries for compliance review)
```

**AuditLogger:**
- Append-only: no UPDATE or DELETE operations on audit_log table
- Tamper-evident: each row includes `prev_hash` (SHA-256 of previous row) → hash chain
- Required audit events: auth events (login/logout/fail), policy decisions (DENY), data access (read PII), PTP creation, consent grant/revoke, AI governance verdicts (BLOCK/REQUIRE_HUMAN), data erasure, API key operations
- PII is redacted in audit log values (audit records the action, not the PII itself)

**AuditVerifier:**
- `verify_chain(tenant_id, start_date, end_date) -> VerificationResult` — walks hash chain and verifies integrity
- Used in compliance audits

### API Security (`src/libs/api_security/`)

```
src/libs/api_security/
├── __init__.py
├── rate_limiter.py         (API-level rate limiter, per tenant+user, uses RateLimiter from Sprint-013)
├── input_validator.py      (InputValidator: validates all incoming request bodies against schema)
├── headers.py              (SecurityHeaders: HSTS, CSP, X-Frame-Options, X-Content-Type)
└── cors.py                 (CORSPolicy: strict origin allowlist, no wildcard)
```

### Runtime Security (`src/libs/runtime_security/`)

```
src/libs/runtime_security/
├── __init__.py
├── container_policy.py     (ContainerSecurityPolicy: no privileged containers, read-only rootfs)
└── network_policy.py       (NetworkPolicy: default-deny Kubernetes network policies)
```

### AI Safety (`src/libs/ai_safety/`)

```
src/libs/ai_safety/
├── __init__.py
├── content_moderator.py    (ContentModerator: blocks abusive/out-of-scope LLM output)
├── prompt_injection.py     (PromptInjectionDetector: detects injection attempts in user input)
├── output_validator.py     (AIOutputValidator: format/length checks on LLM output)
└── human_oversight.py      (HumanOversightRouter: routes REQUIRE_HUMAN to supervisor queue)
```

**ContentModerator:**
- Checks LLM output for: abuse, threats, off-topic content (non-collections), personal bias
- Uses rule-based patterns + optional small classifier
- Returns `ModerationResult(safe: bool, blocked_category: str | None)`

**PromptInjectionDetector:**
- Scans customer utterances for injection patterns ("ignore previous instructions", "system prompt", etc.)
- Detected injection → `BargeinDetected` suppressed, log SECURITY alert, use safe fallback

### Compliance Monitoring (`src/services/compliance-monitoring/`) (V4 Ch16)

```
src/services/compliance-monitoring/
├── __init__.py
├── service.py              (ComplianceMonitoring: real-time compliance signal correlation + alerting)
├── rules.py                (ComplianceRuleSet: rules loaded from PolicyEngine)
├── correlator.py           (SignalCorrelator: correlates audit events into compliance signals)
└── alerter.py              (ComplianceAlerter: emits ComplianceViolationAlert events)
```

**ComplianceMonitoring:**
- Subscribes to `AuditEvent` stream from AuditLogger
- Correlates patterns: e.g., repeated DPDP consent-denied events → `COMPLIANCE_SIGNAL_CONSENT_BYPASS_ATTEMPT`
- Rules: loaded from PolicyEngine compliance domain (same source of truth as enforcement)
- `ingest(audit_event: AuditEvent) -> None` — processes each event in real time
- `status(tenant_id) -> ComplianceStatus` — returns current compliance posture for dashboard
- Emits `ComplianceViolationAlert` to EventBus on threshold breach → feeds Governance Dashboards (Sprint-027)

### Incident Response Service (`src/services/incident-response/`) (V4 Ch17)

```
src/services/incident-response/
├── __init__.py
├── service.py              (IncidentResponse: tracks incidents and drives playbook execution)
├── playbooks.py            (PlaybookRegistry: security/privacy/AI incident playbooks)
└── notifier.py             (RegulatoryNotifier: DPDP breach notification timer enforcement)
```

**IncidentResponse:**
- `open(incident_type: IncidentType, severity: Severity, context: dict) -> Incident` — creates incident record
- `execute_playbook(incident_id: str) -> PlaybookResult` — runs the registered playbook for this incident type
- `notify(incident_id: str, stakeholders: list[str]) -> None` — notifies on-call + stakeholders via configured channels
- `close(incident_id: str, resolution: str) -> None` — closes incident with resolution notes; emits audit event
- Playbook registry covers: security breach, data leak, AI misbehavior, unauthorized access, compliance violation, credential compromise
- DPDP breach notification timer: if incident_type=DATA_BREACH → starts 72h countdown; alerts if regulatory notification not filed within window
- All incident actions emit to AuditLogger (immutable incident record)

---

## Files Expected to Change

**New:** `src/libs/pii/`, `src/libs/audit/`, `src/libs/api_security/`, `src/libs/runtime_security/`, `src/libs/ai_safety/`, `src/services/compliance-monitoring/`, `src/services/incident-response/`  
**Modified:** `src/libs/observability/logger.py` — apply PIIRedactor before log emission  
**New:** `tests/unit/libs/test_pii.py`, `test_audit.py`, `test_ai_safety.py`  
**New:** `tests/unit/services/test_compliance_monitoring.py`, `test_incident_response.py`

---

## Acceptance Criteria

- [x] PIIDetector correctly identifies phone number "9876543210" in free text
- [x] PIIRedactor masks all PII in log strings (unit test: log contains phone → output has `[PHONE]`)
- [x] AuditLogger hash chain: 100 events → `verify_chain()` passes; tamper with event 50 → `verify_chain()` fails at event 50
- [x] Audit log has no PII values in stored records (only redacted tokens)
- [x] All required events are audited (test with mock auth/PTP/consent operations)
- [x] ContentModerator blocks output with abuse pattern
- [x] PromptInjectionDetector flags customer utterance containing "ignore instructions"
- [x] Security headers (HSTS, CSP) present on all API responses
- [x] ComplianceMonitoring: 5 consecutive CONSENT_DENIED audit events → `ComplianceViolationAlert` emitted
- [x] ComplianceMonitoring: `status(tenant_id)` returns COMPLIANT when no violations detected
- [x] IncidentResponse: `open()` creates incident with OPEN status; `execute_playbook()` runs correctly; `close()` emits audit event
- [x] DPDP breach: DATA_BREACH incident opened → 72h notification timer starts (verifiable in incident record)

---

## Required Tests

**Unit:**
- `test_pii_detector_phone` — detects "9876543210" as PHONE
- `test_pii_detector_aadhaar` — detects "1234 5678 9012" as AADHAAR
- `test_pii_redactor_masks_phone` — log with phone → "[PHONE]" in output
- `test_audit_hash_chain_valid` — 10 events → verify_chain passes
- `test_audit_hash_chain_tamper` — modify event 5 → verify_chain fails at event 5
- `test_content_moderator_blocks_abuse` — abusive text → safe=False
- `test_prompt_injection_detected` — "ignore instructions" → flagged
- `test_compliance_monitoring_violation_alert` — 5 CONSENT_DENIED events → alert emitted
- `test_compliance_monitoring_status_compliant` — no violations → status=COMPLIANT
- `test_incident_response_lifecycle` — open → execute_playbook → close → audit events present
- `test_incident_response_dpdp_timer` — DATA_BREACH incident → notification_deadline_at set to +72h

---

## Definition of Done

- [x] All AC items checked
- [x] PIIRedactor applied to ALL log paths (verified by coverage)
- [x] Audit hash chain verified clean
- [x] Zero PII in application logs (automated log scan)
- [x] CI green
- [x] **Milestone M-5 (Compliance & Security Complete) criteria verified**
- [x] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [x] `CURRENT_SPRINT.md` updated to Sprint-021

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** PIIDetector uses regex/NER patterns (no GPU); AuditLogger uses `TestPostgres`; ComplianceMonitoring uses `FakeEventBus`.

### Files Created

- `src/libs/pii/__init__.py`, `detector.py`, `redactor.py`, `tokenizer.py`, `entities.py`
- `src/libs/audit/__init__.py`, `logger.py`, `event.py`, `verifier.py`, `search.py`
- `src/libs/api_security/__init__.py`, `rate_limiter.py`, `input_validator.py`, `headers.py`, `cors.py`
- `src/libs/runtime_security/__init__.py`, `container_policy.py`, `network_policy.py`
- `src/libs/ai_safety/__init__.py`, `content_moderator.py`, `prompt_injection.py`, `output_validator.py`, `human_oversight.py`
- `src/services/compliance-monitoring/__init__.py`, `service.py`, `rules.py`, `correlator.py`, `alerter.py`
- `src/services/incident-response/__init__.py`, `service.py`, `playbooks.py`, `notifier.py`
- `tests/unit/libs/test_pii.py`, `test_audit.py`, `test_ai_safety.py`
- `tests/unit/services/test_compliance_monitoring.py`, `test_incident_response.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Postgres (audit log) | `TestPostgres` Docker fixture | Real hash chain verification against test DB |
| EventBus (audit events) | `FakeEventBus` | Captures compliance alerts |
| StructuredLogger | In-process `StructuredLogger` with PIIRedactor wired | Verifies PII redaction before emission |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations; PIIRedactor wired into all log paths |
| Unit tests | `pytest tests/unit/libs/ tests/unit/services/test_compliance_monitoring.py tests/unit/services/test_incident_response.py` | All pass |
| Hash chain test | `pytest tests/unit/libs/test_audit.py::test_audit_hash_chain_tamper` | Tamper detected at correct offset |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `PIIDetector.detect("9876543210")` → `PIISpan(entity_type=PHONE)`
- `PIIRedactor.redact("call 9876543210")` → `"call [PHONE]"`
- `AuditLogger` hash chain: 100 events → `verify_chain()` passes; tamper event 50 → fails at event 50
- `ContentModerator.check(abusive_text)` → `safe=False`
- `PromptInjectionDetector.detect("ignore instructions")` → flagged
- `ComplianceMonitoring`: 5 CONSENT_DENIED events → `ComplianceViolationAlert` emitted
- `IncidentResponse.open(DATA_BREACH)` → 72h notification deadline set

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed/integrated this sprint:**

| Action | Target | Why |
|---|---|---|
| Wire PIIRedactor into StructuredLogger | All services (rolling restarts) | Zero PII in any application log |
| Deploy AuditLogger | As shared library in all audit-generating services | Immutable tamper-evident audit trail active |
| Deploy ComplianceMonitoringService | K8s Deployment — `voiceos-runtime` | Real-time compliance signal correlation |
| Deploy IncidentResponseService | K8s Deployment — `voiceos-runtime` | Playbook execution + DPDP notification timer |
| Apply SecurityHeaders middleware | All FastAPI services (rolling restarts) | HSTS, CSP, X-Frame-Options on all responses |
| Wire PromptInjectionDetector | STTService → ConversationEngine path | Customer utterance injection detection |
| Wire ContentModerator | AIGovernanceService | Blocks abusive/off-topic LLM output |

**Previously deployed services that remain running:**
- All Sprint-004–019 services

**Deployment procedure:**
1. Rolling restart all services with PIIRedactor wired into StructuredLogger
2. Deploy ComplianceMonitoringService and IncidentResponseService
3. Confirm AuditLogger writing hash chain to Postgres `audit_log` table
4. Run log scan: `grep -E "[0-9]{10}" <log_output>` → 0 raw phone numbers visible

**Health checks:**
- ComplianceMonitoringService: `GET /health/ready` → 200; EventBus subscription active
- IncidentResponseService: `GET /health/ready` → 200; playbook registry loaded
- Audit hash chain: `AuditVerifier.verify_chain(start=now-1h, end=now)` → VALID

**Integration validation:**
- Log PII redaction: trigger a call with customer phone number → grep all service logs → `[PHONE]` appears, not raw number
- Compliance alert: simulate 5 CONSENT_DENIED audit events → `ComplianceViolationAlert` in EventBus within 60s
- DPDP incident: `IncidentResponse.open(DATA_BREACH)` → `notification_deadline_at = now + 72h` in DB
- Security headers: `curl -I https://<api-endpoint>` → `Strict-Transport-Security`, `Content-Security-Policy` present

**Rollback procedure:**
- PIIRedactor removal: redeploy previous service images (PIIRedactor is additive — no data lost)
- ComplianceMonitoringService: `kubectl rollout undo deployment/compliance-monitoring -n voiceos-runtime`

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged. PIIDetector is CPU-only (regex + lightweight NER).

### Infrastructure Validation

**CPU Validation:**
- Log audit: grep all structured logs for raw phone numbers → 0 matches (PII redacted)
- Audit hash chain: `AuditVerifier.verify_chain()` passes for last 1,000 events
- ComplianceMonitoring: `status(tenant_id)` → COMPLIANT for test tenant with no violations
- IncidentResponse: lifecycle test (open → execute_playbook → close) → all steps audit-logged
- Security headers: HSTS, CSP present on all API responses

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- ComplianceMonitoringService → EventBus: audit events received within 100ms of emission
- IncidentResponseService → notification channels: alert delivery within 60s of incident open

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — passes (PIIRedactor does not affect call flow)
- AIGovernanceService: BLOCK verdict still fired for invented facts
- Auth middleware: tokens still valid; no PII in auth logs

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [x] PIIDetector + PIIRedactor + PIITokenizer implemented
- [x] AuditLogger (hash chain, tamper-evident) implemented
- [x] ComplianceMonitoringService + IncidentResponseService implemented
- [x] ContentModerator + PromptInjectionDetector implemented
- [x] SecurityHeaders middleware implemented
- [x] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [x] Audit hash chain tamper detection verified
- [x] Coverage ≥ 85%
- [x] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [x] PIIRedactor active in all service logs — zero raw PII in any log
- [x] Audit hash chain live and verifiable in Postgres
- [x] ComplianceMonitoringService: compliance violation alert fires on threshold breach
- [x] IncidentResponseService: DATA_BREACH 72h timer confirmed
- [x] SecurityHeaders on all API responses
- [x] All regression tests pass
- [x] Milestone M-5 (Compliance & Security Complete) verified
- [x] Deployment remains active as baseline for Sprint-021

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `PIIRedactionService`, `AuditLogService`, `ConsentManagementService`, `ComplianceMonitoringService`, `IncidentResponseService`, `HumanOversightRouter` to Services table (§8.1)
- Update ALL services in §8.1: note PIIRedactor now embedded in StructuredLogger (rolling restart applied)
- Update §12 Database Schema: add `audit_log` table (append-only, hash-chained); add `consents` table
- Add environment variables: `AUDIT_LOG_RETENTION_DAYS=30`, `CONSENT_REQUIRED=true` (§11)
- Add health check commands for all 6 new services (§14)
- Update §13 Observability: note that all audit events are hash-chained (tamper-evident)

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-019.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add all 6 new services to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add audit and consent variable descriptions |

### DR Validation

**Audit chain verification after rebuild:**
```bash
# Verify audit hash chain is intact after restore
python3 scripts/validate/audit_chain.py
# Expected: AuditVerifier.verify_chain() returns True (no gaps, no tampering)
```

**Consent gate after rebuild:**
```bash
python3 scripts/validate/consent_gate.py --customer-id test-no-consent
# Expected: call blocked; ConsentRequired event emitted
```

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all services healthy; PIIRedactor active in all StructuredLogger instances
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; Milestone M-5 criteria verified after rebuild
```
