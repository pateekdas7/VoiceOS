# Sprint-018 — Authentication, Authorization/RBAC & AI Governance

**Epic:** E5 — Compliance & Security  
**Status:** ⬜ Pending  
**Depends on:** Sprint-017  
**Blocks:** Sprint-019, Sprint-020, Sprint-021  

---

## Objective

Implement the complete auth layer (OAuth2/OIDC/JWT for users, mTLS for services, API keys), the RBAC+ABAC authorization engine with tenant isolation enforcement, and the AI Governance layer that enforces the Law of Authority and issues GovernanceVerdicts for every LLM output.

---

## Architecture References

- Volume 4: Ch3 (AI Governance — GovernanceVerdict, Law of Authority, explainability), Ch5 (Authentication — OAuth2/OIDC/JWT, mTLS, API keys), Ch6 (Authorization/RBAC — RBAC+ABAC, tenant isolation invariant, JIT privileges)
- DocSuite-02: Interface Contracts (Auth API)
- DocSuite-05: Configuration Reference

---

## Components to Implement

### `src/services/auth/`

```
src/services/auth/
├── __init__.py
├── service.py              (AuthService: validates credentials per type)
├── jwt_validator.py        (JWTValidator: validates JWT, extracts claims, checks expiry)
├── oidc_provider.py        (OIDCProvider: integration with OIDC IdP, token exchange)
├── mtls_enforcer.py        (mTLSEnforcer: validates client certificate for service-to-service calls)
├── api_key_validator.py    (APIKeyValidator: validates API keys, resolves tenant_id + scopes)
├── middleware.py           (AuthMiddleware: FastAPI/gRPC middleware for all incoming requests)
└── models.py               (AuthContext: authenticated identity, scopes, tenant_id)
```

**AuthMiddleware:**
- Applied to ALL service endpoints (no endpoint is unauthenticated)
- Detects auth type: Bearer JWT | API Key (`X-API-Key` header) | mTLS client cert
- Populates `request.auth_context: AuthContext` if valid
- Returns 401 if invalid, 403 if insufficient permissions

**mTLS enforcer:**
- Service-to-service calls require mutual TLS: both client and server present valid certs
- Validates cert against internal CA (self-managed PKI)
- No plaintext service-to-service calls permitted (AR-3 compliance)

### `src/services/authz/`

```
src/services/authz/
├── __init__.py
├── service.py              (AuthzService: RBAC check API)
├── rbac_engine.py          (RBACEngine: role → permission mapping)
├── abac_evaluator.py       (ABACEvaluator: attribute-based access control, org scope)
├── roles.py                (Role enum: ADMIN|SUPERVISOR|MANAGER|AGENT|AUDITOR + permissions per role)
├── jit_privilege.py        (JITPrivilege: temporary escalation with approval workflow + TTL)
├── tenant_isolation.py     (TenantIsolationGuard: enforces tenant_id scope on every authz check)
└── models.py               (AuthorizationRequest, AuthorizationResult)
```

**TenantIsolationGuard (critical):**
- Wrapped around every `RBACEngine.check()` call
- Ensures `request.auth_context.tenant_id == resource.tenant_id` (or system-level role)
- If mismatch: raises `TenantIsolationViolationError` (logged as CRITICAL security event)
- This is the enforcement point for AR-8

**Roles and permissions (V4 Ch6):**
```
ADMIN:       full CRUD on all resources within tenant
SUPERVISOR:  read all, write campaigns/users, monitor calls, barge-in
MANAGER:     read all, write campaigns, view analytics
AGENT:       handle escalated calls only (human agent context)
AUDITOR:     read-only audit log + transcripts
```

### `src/services/ai-governance/`

```
src/services/ai-governance/
├── __init__.py
├── service.py              (AIGovernanceService: evaluates LLM output)
├── governance_layer.py     (GovernanceLayer: orchestrates all governance checks)
├── law_of_authority.py     (LawOfAuthorityChecker: verifies no invented facts in LLM output)
├── verdict.py              (GovernanceVerdict: APPROVE|REQUIRE_HUMAN|BLOCK)
├── explainability.py       (ExplainabilityEngine: generates human-readable rationale from DecisionEnvelope)
└── metrics.py              (governance_verdicts_by_outcome, law_of_authority_violations)
```

**GovernanceLayer (mandatory gate):**
- Called on every LLM output BEFORE it reaches TTS (hardwired in ConversationEngine)
- Checks (in order):
  1. `LawOfAuthorityChecker`: scan for facts in output, verify each against ResponsePlan.facts
  2. `PolicyEngine.check(domain=AI_GOVERNANCE, action=OUTPUT_APPROVAL)`: conversational policy check
  3. AI Safety check: content moderation (no abusive/out-of-scope output)
- Returns `GovernanceVerdict`:
  - `APPROVE` — pass to TTS
  - `REQUIRE_HUMAN` — route to supervisor queue, pause AI response
  - `BLOCK` — suppress output, use fallback template, log violation

**LawOfAuthorityChecker:**
- Extracts all amounts, dates, account numbers from LLM output (regex + NER)
- Verifies each against `ResponsePlan.facts` (exact match required for amounts, ±1 day for dates)
- Any fact not in `ResponsePlan.facts` → `BLOCK` verdict
- Calls `assert_ri5_law_of_authority()` invariant for each extracted fact

---

## Files Expected to Change

**New:** `src/services/auth/`, `src/services/authz/`, `src/services/ai-governance/`  
**New:** `tests/unit/services/test_auth.py`, `test_authz.py`, `test_ai_governance.py`  
**New:** `tests/integration/services/test_auth_integration.py`

---

## Acceptance Criteria

- [ ] JWT with valid signature and non-expired → AuthContext populated, request proceeds
- [ ] JWT with invalid signature → 401 returned
- [ ] API key without `X-API-Key` header → 401 returned
- [ ] RBAC: AUDITOR role cannot write (POST/PUT/DELETE → 403)
- [ ] Tenant isolation: request from tenant-A trying to access tenant-B resource → TenantIsolationViolationError raised and 403 returned
- [ ] AI Governance: LLM output with fact "₹15,000" when ResponsePlan.facts has "₹12,500" → BLOCK verdict
- [ ] AI Governance: clean LLM output (all facts match) → APPROVE verdict
- [ ] REQUIRE_HUMAN verdict → supervisor queue event emitted
- [ ] `law_of_authority_violations` Prometheus counter increments on BLOCK verdict

---

## Required Tests

**Unit:**
- `test_jwt_valid` — valid JWT → AuthContext with correct tenant_id and role
- `test_jwt_expired` — expired JWT → 401
- `test_rbac_auditor_cannot_write` — AUDITOR + POST → DENY
- `test_rbac_admin_can_write` — ADMIN + POST → PERMIT
- `test_tenant_isolation_cross_tenant` — tenant-A subject + tenant-B resource → TenantIsolationViolationError
- `test_law_of_authority_block_hallucination` — invented amount → BLOCK
- `test_law_of_authority_approve_grounded` — all facts from ResponsePlan.facts → APPROVE
- `test_governance_require_human_routes_to_supervisor` — REQUIRE_HUMAN verdict → supervisor event emitted

---

## Definition of Done

- [ ] All AC items checked
- [ ] AI Governance gate wired into ConversationEngine (mandatory, not optional)
- [ ] Tenant isolation violation test: 100% blocked
- [ ] Law of Authority red-team test: zero invented facts pass through
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-019

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Auth and governance tests use fake JWT tokens, mock OIDC providers, and in-process PolicyEngine stub.

### Files Created

- `src/services/auth/__init__.py`, `service.py`, `jwt_validator.py`, `oidc_provider.py`, `mtls_enforcer.py`, `api_key_validator.py`, `middleware.py`, `models.py`
- `src/services/authz/__init__.py`, `service.py`, `rbac_engine.py`, `abac_evaluator.py`, `roles.py`, `jit_privilege.py`, `tenant_isolation.py`, `models.py`
- `src/services/ai-governance/__init__.py`, `service.py`, `governance_layer.py`, `law_of_authority.py`, `verdict.py`, `explainability.py`, `metrics.py`
- `tests/unit/services/test_auth.py`, `test_authz.py`, `test_ai_governance.py`
- `tests/integration/services/test_auth_integration.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| JWT issuer | In-process key pair (RSA) | Generate test tokens with `python-jose` |
| OIDC provider | Mock OIDC server (responses fixture) | Returns test token without real IdP |
| PolicyEngine | `FakePolicyEngine` stub | Returns PERMIT/DENY without real PolicyEngineService |
| EventBus (governance verdicts) | `FakeEventBus` | Captures `GovernanceVerdict` events |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/services/test_auth.py tests/unit/services/test_authz.py tests/unit/services/test_ai_governance.py` | All pass |
| Red-team test | `pytest tests/unit/services/test_ai_governance.py -k "law_of_authority"` | Zero invented facts pass |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- Valid JWT → `AuthContext` populated; expired JWT → 401
- AUDITOR role + POST → 403 (RBAC enforcement)
- Cross-tenant resource access → `TenantIsolationViolationError`
- LLM output "₹15,000" vs ResponsePlan "₹12,500" → BLOCK verdict
- Clean LLM output (all facts match) → APPROVE verdict
- `law_of_authority_violations` Prometheus counter increments on BLOCK

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| AuthService | K8s Deployment — `voiceos-runtime` | JWT/API-key validation for all incoming requests |
| AuthzService | K8s Deployment — `voiceos-runtime` | RBAC check gateway for all resource access |
| AIGovernanceService | K8s Deployment — `voiceos-runtime` | Mandatory LLM output gate before TTS |

**Integration wiring this sprint:**
- Auth middleware applied to all existing FastAPI/gRPC services (rolling restarts)
- AIGovernanceService hardwired into ConversationEngine (between OutputValidator and TTS)
- mTLS enforced for all inter-service gRPC calls (PKI certificates issued)

**Previously deployed services that remain running:**
- All Sprint-004–017 services

**Deployment procedure:**
1. Issue PKI certificates for all services; configure mTLS on all gRPC connections
2. Deploy AuthService, AuthzService, AIGovernanceService
3. Rolling restart all existing services with auth middleware wired in
4. Verify: unauthenticated request to any API → 401
5. Verify: AIGovernanceService wired into ConversationEngine pipeline

**Health checks:**
- All 3 new services: `GET /health/live` and `GET /health/ready` → 200
- `governance_verdicts_by_outcome{outcome="APPROVE"}` counter incrementing on test calls
- Auth middleware: test API call without token → 401; with valid token → 200

**Integration validation:**
- Red-team: inject LLM response with invented fact via test hook → BLOCK verdict from AIGovernanceService
- RBAC: AUDITOR JWT + DELETE request → 403
- mTLS: any service-to-service gRPC call without valid cert → connection rejected
- Tenant isolation: cross-tenant API request → `TenantIsolationViolationError` + 403

**Rollback procedure:**
- Auth middleware rollback: redeploy previous service images without auth wiring
- mTLS certificates: PKI rotation does not affect running connections immediately
- AIGovernanceService: remove from ConversationEngine pipeline (ConversationEngine config flag)

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged. AIGovernanceService is a CPU service that evaluates LLM text output.

### Infrastructure Validation

**CPU Validation:**
- Auth middleware enforced on all 20+ deployed endpoints (spot-check with curl)
- AIGovernanceService: BLOCK verdict issued for invented fact in validation call
- mTLS: verify all inter-service gRPC connections have TLS certificates
- `law_of_authority_violations` gauge = 0 at steady state (only increments on violation)

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- mTLS: all service-to-service gRPC calls use mutual TLS (verify with `openssl s_client`)
- AuthService latency: JWT validation < 5ms p99 (cached key verification)

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — passes with auth middleware (test uses valid JWT)
- PolicyEngineService: `pytest tests/integration/services/test_policy_engine_integration.py` — auth now required, test tokens used
- All prior unit tests: `pytest tests/unit/` — all pass (mocked auth context in tests)

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] AuthService (JWT/API-key/mTLS) implemented
- [ ] AuthzService (RBAC/ABAC/TenantIsolation) implemented
- [ ] AIGovernanceService (GovernanceLayer, LawOfAuthorityChecker) implemented
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Red-team: zero invented facts pass through GovernanceLayer
- [ ] Tenant isolation: 100% blocked
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] AuthService, AuthzService, AIGovernanceService deployed and healthy
- [ ] Auth middleware active on all service endpoints
- [ ] mTLS enforced for all inter-service communication
- [ ] AIGovernanceService wired into ConversationEngine (mandatory gate)
- [ ] BLOCK verdict verified for red-team invented fact test on live system
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-019

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `AuthService`, `AuthzService`, `AIGovernanceService` to Services table (§8.1)
- Update ALL services in §8.1: note mTLS now required for all inter-service gRPC communication
- Add mTLS configuration to §9 Network Configuration: certificate paths, CA cert location
- Add environment variables: `MTLS_CA_CERT_PATH`, `MTLS_CLIENT_CERT_PATH`, `MTLS_CLIENT_KEY_PATH`, `JWT_SECRET` (§11)
- Add §10 Volumes entry for `/opt/voiceos/certs/` (mTLS certificate storage)
- Add health check commands for AuthService, AuthzService, AIGovernanceService (§14)
- Update Service Dependencies (§8.2): all services now require mTLS; AIGovernanceService is mandatory gate in ConversationEngine

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-012.
Note: GPU services will require mTLS certificates when rolling restart is applied — add certificate paths to §12 Environment Variables.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add AuthService, AuthzService, AIGovernanceService to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add mTLS cert path variable descriptions |
| `deployment/cpu/restore.sh` | Add: create `/opt/voiceos/certs/` directory; copy certificates from Vault/secrets store |

### DR Validation

**mTLS certificate provisioning (critical for rebuild):**
```bash
# On fresh CPU server — certificates must be provisioned before services start
mkdir -p /opt/voiceos/certs
# Copy CA cert, client cert, client key from secure cert store
# Example: vault kv get -field=ca_cert secret/voiceos/mtls > /opt/voiceos/certs/ca.crt
```

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all inter-service communication uses mTLS; AIGovernanceService healthy
```

**AI Governance gate validation after rebuild:**
```bash
# Red-team test: inject invented fact
python3 scripts/validate/ai_governance_redteam.py
# Expected: BLOCK verdict; no invented fact reaches TTS
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
```
