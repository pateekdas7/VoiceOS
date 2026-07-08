# Sprint-021 — Multi-Tenancy, Tenant Lifecycle & User Management

**Epic:** E6 — SaaS Platform  
**Status:** ✅ **Complete** (2026-07-06)  
**Depends on:** Sprint-018, Sprint-020  
**Blocks:** Sprint-022, Sprint-024, Sprint-025  

---

## Objective

Implement the multi-tenant foundation: isolation profiles, org hierarchy, the tenant lifecycle state machine, and user/organization management with org-scoped RBAC. This is the foundation the entire SaaS platform builds on.

---

## Architecture References

- Volume 5: Ch2 (Multi-Tenant Architecture — isolation profiles, hierarchy), Ch3 (Tenant Lifecycle — TRIAL→PRODUCTION→DELETED), Ch8 (User & Organization Management — roles, hierarchy-scoped RBAC)
- Volume 4: Ch6 (RBAC — roles within org hierarchy)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### `src/services/tenant-management/`

```
src/services/tenant-management/
├── __init__.py
├── service.py              (TenantService: CRUD + lifecycle operations)
├── lifecycle.py            (TenantLifecycle: state machine)
├── provisioner.py          (TenantProvisioner: create resources on tenant activation)
├── isolation.py            (IsolationProfileManager: row_level vs schema vs dedicated_db)
├── suspension.py           (TenantSuspender: drain calls + freeze data)
└── deletion.py             (TenantDeleter: async deletion + crypto-shred)
```

**Tenant lifecycle state machine:**
```
TRIAL → SANDBOX → PRODUCTION → SUSPENDED → CANCELLED → DELETING → DELETED
          ↑_________|              |              |
          (reactivate)        (suspend)     (cancel + delete)
```

**IsolationProfileManager:**
- `ROW_LEVEL`: shared schema, tenant_id column on every table (default, Sprint-014 migrations support this)
- `SCHEMA`: per-tenant Postgres schema (separate `customers_<tenant_id>` tables)
- `DEDICATED_DB`: separate Postgres database per tenant (enterprise tier)
- Isolation level determined at provisioning time; cannot change without migration

**TenantProvisioner (on TRIAL activation):**
- Creates tenant record in Postgres
- Creates tenant KEK in KMS
- Creates Redis namespace
- Seeds default policy pack (global inherits to tenant)
- Creates default admin user for tenant
- Emits `TenantProvisioned` event

### Org Hierarchy (`src/services/org-management/`)

```
src/services/org-management/
├── __init__.py
├── service.py              (OrgService: CRUD on Organization, BusinessUnit, Branch)
├── hierarchy.py            (OrgHierarchy: traversal, scope resolution)
└── models.py               (Organization, BusinessUnit, Branch — backed by Postgres orgs table)
```

**Org Hierarchy:**
- 3 levels: Organization → BusinessUnit → Branch
- RBAC assignments are scoped to an org node: user assigned MANAGER at BusinessUnit level can see all branches under that BU
- `resolve_scope(user: User, resource: Resource) -> bool` — determines if user's org scope covers the resource's org scope

### `src/services/user-management/`

```
src/services/user-management/
├── __init__.py
├── service.py              (UserService: CRUD, invitation, SSO)
├── invitation.py           (InvitationService: email invite workflow, token-based activation)
└── sso_stub.py             (SSOIntegration: stub for Sprint-025 SAML/OIDC SSO)
```

---

## Files Expected to Change

**New:** `src/services/tenant-management/`, `src/services/org-management/`, `src/services/user-management/`  
**New:** `tests/unit/services/test_tenant_management.py`, `test_org_management.py`, `test_user_management.py`  
**New:** `tests/integration/services/test_tenant_isolation.py`

---

## Acceptance Criteria

- [x] Tenant lifecycle: TRIAL → SANDBOX → PRODUCTION transitions work, invalid transitions raise
- [x] TenantProvisioner creates KEK in KMS on PRODUCTION activation
- [x] Tenant isolation test: data written as tenant-A is not readable by tenant-B queries
- [x] OrgHierarchy.resolve_scope(): user scoped to BU sees branch data; user scoped to Org sees all BUs
- [x] User invitation: invite sent → token activation → user created with correct role
- [x] TenantSuspender: suspension → existing calls drain (no new calls admitted), data frozen

---

## Required Tests

**Unit:**
- `test_tenant_lifecycle_valid_transitions` — TRIAL → SANDBOX → PRODUCTION each passes
- `test_tenant_lifecycle_invalid_transition` — DELETED → PRODUCTION raises
- `test_org_scope_bu_sees_branches` — user at BU level sees branch resources
- `test_org_scope_branch_cannot_see_other_bu` — user at Branch level cannot see sibling branch

**Integration:**
- `test_tenant_isolation_cross_tenant_query` — customer in tenant-A not in tenant-B result set
- `test_tenant_provisioner_creates_kek` — activation → KMS key created

---

## Definition of Done

- [x] All AC items checked
- [x] Cross-tenant isolation integration test passes
- [x] CI green
- [x] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [x] `CURRENT_SPRINT.md` updated to Sprint-022

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Tenant lifecycle tests are in-process; provisioner tests use `FakeKMSClient` for KEK creation; isolation tests use `TestPostgres`.

### Files Created

- `src/services/tenant-management/__init__.py`, `service.py`, `lifecycle.py`, `provisioner.py`, `isolation.py`, `suspension.py`, `deletion.py`
- `src/services/org-management/__init__.py`, `service.py`, `hierarchy.py`, `models.py`
- `src/services/user-management/__init__.py`, `service.py`, `invitation.py`, `sso_stub.py`
- `tests/unit/services/test_tenant_management.py`, `test_org_management.py`, `test_user_management.py`
- `tests/integration/services/test_tenant_isolation.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| KMS (tenant KEK) | `FakeKMSClient` | Returns test key on provisioning |
| Postgres | `TestPostgres` Docker fixture | Real cross-tenant isolation test |
| Redis (tenant namespace) | `FakeRedisClient` | Namespace creation without real Redis |
| EventBus | `FakeEventBus` | Captures `TenantProvisioned` event |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/services/test_tenant_management.py tests/unit/services/test_org_management.py tests/unit/services/test_user_management.py` | All pass |
| Isolation test | `pytest tests/integration/services/test_tenant_isolation.py` | tenant-A data not visible from tenant-B |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- Tenant lifecycle: TRIAL → SANDBOX → PRODUCTION (valid); DELETED → PRODUCTION (raises)
- `OrgHierarchy.resolve_scope()`: BU-scoped user sees branches; branch-scoped user cannot see sibling branch
- `TenantProvisioner`: PRODUCTION activation → KEK created in FakeKMS, `TenantProvisioned` event emitted
- Cross-tenant: customer in tenant-A → query with tenant-B filter → 0 results

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| TenantManagementService | K8s Deployment — `voiceos-platform` namespace | Tenant lifecycle + provisioning |
| OrgManagementService | K8s Deployment — `voiceos-platform` namespace | Org hierarchy + scope resolution |
| UserManagementService | K8s Deployment — `voiceos-platform` namespace | User CRUD + invitation workflow |

**Previously deployed services that remain running:**
- All Sprint-004–020 services

**Deployment procedure:**
1. Deploy three management services; confirm connection to Postgres, Redis, KMS, EventBus
2. Provision test tenant via API: `POST /tenants` → TRIAL state; `POST /tenants/{id}/activate` → PRODUCTION
3. Verify KEK created in KMS for test tenant
4. Cross-tenant isolation test on production Postgres

**Health checks:**
- All 3 services: `GET /health/ready` → 200
- `TenantProvisioner`: test activation → `TenantProvisioned` event in EventBus within 5s
- Postgres: `SELECT COUNT(*) FROM tenants WHERE status = 'PRODUCTION'` → ≥ 1

**Integration validation:**
- Cross-tenant API: create customer as tenant-A → query as tenant-B → 0 results (HTTP 200, empty list)
- Suspension: `POST /tenants/{id}/suspend` → new call admission blocked; existing calls drain
- Org scope: MANAGER at BU level can read branch data; AGENT at branch cannot read sibling branch

**Rollback procedure:**
- `kubectl rollout undo deployment/<service> -n voiceos-platform`
- Tenant data unaffected (Postgres); rollback only redeploys the service

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- Tenant isolation: API-level cross-tenant query returns empty list (not 403, not data)
- Provisioner: KMS key created (verify via `aws kms list-keys` or Vault path listing)
- Lifecycle transitions: invalid transition raises (logged as WARN; HTTP 422)

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- TenantManagementService → KMS: secret access < 100ms
- TenantManagementService → EventBus: `TenantProvisioned` delivered within 1s

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — passes (test tenant provisioned)
- PIIRedactor: tenant admin creation does not log raw user PII
- Auth middleware: tenant-scoped JWT still valid

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [x] TenantManagement, OrgManagement, UserManagement implemented
- [x] Tenant lifecycle state machine complete with invalid transition guard
- [x] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [x] Cross-tenant isolation integration test passes
- [x] Coverage ≥ 85%
- [x] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [x] All 3 management services deployed and healthy
- [x] Test tenant provisioned end-to-end (KEK created, event emitted)
- [x] Cross-tenant isolation verified on production Postgres
- [x] All regression tests pass
- [x] Deployment remains active as baseline for Sprint-022

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `TenantManagementService`, `OrgManagementService`, `UserManagementService` to Services table (§8.1) — namespace: `voiceos-platform`
- Update §9.1 Port Map with `voiceos-platform` namespace service ports
- Update §12 Database Schema: add `tenants`, `organizations`, `business_units`, `branches`, `users`, `invitations`, `sso_config` tables
- Add environment variables: `KMS_ENDPOINT`, `KMS_KEY_ARN_TEMPLATE` (one KEK per tenant, §11)
- Add health check commands for all 3 management services (§14)
- Update §8.3 Startup Order: tenant management services start before CRM/Collections (Sprint-022)

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-019.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add TenantManagementService, OrgManagementService, UserManagementService to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add `KMS_ENDPOINT`, `KMS_KEY_ARN_TEMPLATE` variable descriptions |

### DR Validation

**Cross-tenant isolation after rebuild:**
```bash
# Write customer as tenant-A; query as tenant-B
python3 scripts/validate/cross_tenant_isolation.py
# Expected: 0 results returned for tenant-B query
```

**Tenant provisioning after rebuild:**
```bash
curl -sf -X POST http://<tenant-mgmt>/tenants -d '{"name":"test-tenant"}' | jq .status
# Expected: "TRIAL"; then activate → "PRODUCTION"; verify KMS key created
```

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: voiceos-platform namespace services healthy
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
```
