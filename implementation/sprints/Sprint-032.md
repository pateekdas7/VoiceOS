# Sprint-032 — Enterprise Platform: SSO, SCIM & Multi-Region

**Epic:** E6-Extended — SaaS Platform (Enterprise Tier)
**Status:** ⬜ Pending
**Depends on:** Sprint-025, Sprint-021, Sprint-018, Sprint-019, Sprint-020, Sprint-026
**Blocks:** Sprint-031
**Milestone:** Enterprise Platform Complete

---

## Objective

Implement the full Enterprise Platform tier: SAML/OIDC federation for SSO, SCIM-compliant user provisioning/deprovisioning, tamper-evident audit log export pipeline for enterprise compliance, multi-region data residency enforcement, and dedicated-cluster provisioning automation. After this sprint, enterprise customers can onboard through their own identity providers, their users are automatically provisioned via SCIM, and their data can be isolated by region.

---

## Architecture References

- Volume 5: Ch22 (Enterprise Platform — SSO/SAML/OIDC, SCIM provisioning, audit exports, multi-region data residency, dedicated-cluster provisioning, enterprise-API SLA tiers)
- Volume 7: Ch22 (Enterprise Operations — dedicated cluster ops, premium SLAs, enterprise customer management)
- Volume 4: Ch3 (Auth — identity federation), Ch5 (RBAC — enterprise role mapping), Ch11 (Audit — tamper-evident export pipeline)
- DocSuite-05: Configuration Reference (enterprise configuration schema)

---

## Components to Implement

### `src/services/enterprise-platform/`

```
src/services/enterprise-platform/
├── __init__.py
├── sso/
│   ├── saml_provider.py        (SAMLIdentityProvider: SAML 2.0 SP-initiated + IdP-initiated SSO)
│   ├── oidc_provider.py        (OIDCProvider: OpenID Connect relying party with PKCE)
│   └── federation.py           (IdentityFederation: maps external IdP claims → VoiceOS roles)
├── scim/
│   ├── service.py              (SCIMService: SCIM 2.0 REST API for user lifecycle)
│   ├── provisioner.py          (UserProvisioner: creates/updates/deactivates users from SCIM events)
│   └── group_sync.py           (GroupSyncer: maps IdP groups → VoiceOS RBAC roles)
├── audit_export/
│   ├── pipeline.py             (AuditExportPipeline: streams audit events to customer S3/SFTP)
│   └── signer.py               (ExportSigner: HMAC-signs each exported record for tamper evidence)
├── data_residency/
│   ├── enforcer.py             (DataResidencyEnforcer: ensures data stays within configured region)
│   └── region_router.py        (RegionRouter: routes writes to the correct regional Postgres instance)
└── cluster_provisioning/
    ├── service.py              (ClusterProvisioningService: dedicated K8s cluster provisioning for ENTERPRISE tier)
    └── templates/              (Terraform templates for dedicated cluster topologies)
```

**SAMLIdentityProvider:**
- Implements SAML 2.0 Service Provider role
- SP-initiated flow: redirect user to customer IdP (Okta, Azure AD, PingFederate, etc.)
- IdP-initiated flow: accept SAML assertion at `/saml/acs` endpoint, validate signature, extract user attributes
- Attribute mapping: `email` → user identifier, `groups` → RBAC role set, `tenant_id` from SP entity ID

**OIDCProvider:**
- Implements OIDC Relying Party with PKCE (RFC 7636)
- Supports: Azure AD, Google Workspace, Okta, generic OIDC
- Token validation: signature check, `iss` / `aud` validation, expiry

**IdentityFederation:**
- Maps external IdP claims → VoiceOS roles using a configurable claim-mapping rule per tenant
- `map_claims(claims: dict, tenant_id: str) -> UserIdentity` — deterministic mapping
- Claim rules stored in Postgres `sso_config` table (admin-configurable via Sprint-025 Admin Portal)

**SCIMService (SCIM 2.0):**
- `POST /scim/v2/Users` — provision new user
- `PATCH /scim/v2/Users/{id}` — update user attributes
- `DELETE /scim/v2/Users/{id}` — deactivate user (soft-delete: suspended, not deleted)
- `GET /scim/v2/Groups` — list groups; PATCH to add/remove group members → syncs RBAC roles

**AuditExportPipeline:**
- Streams audit events from AuditLogger (Sprint-020) to customer-owned S3 bucket or SFTP endpoint
- Export format: NDJSON, one record per line, HMAC-SHA256 signed per record
- `ExportSigner.sign(record, tenant_signing_key) -> SignedRecord` — tamper-evident per-record signature
- Customers can verify export integrity independently

**DataResidencyEnforcer:**
- Enforces `tenant.data_region` (e.g., `ap-south-1` for India, `eu-west-1` for EU)
- All Postgres writes routed to regional instance via `RegionRouter`
- Reads can be served from replica; writes are strictly regional
- Middleware: rejects cross-region writes at the service layer

**ClusterProvisioningService:**
- `provision_dedicated_cluster(tenant_id, config: ClusterConfig) -> ClusterHandle` — creates isolated K8s namespace (or separate cluster) for ENTERPRISE customers requiring dedicated infrastructure
- Uses Sprint-026 Terraform modules with tenant-specific variable overrides

---

## Files Expected to Change

**New:** `src/services/enterprise-platform/` (all files)
**New:** `tests/unit/services/test_enterprise_sso.py`, `test_scim.py`, `test_audit_export.py`, `test_data_residency.py`
**New:** `tests/integration/services/test_sso_flow.py`, `test_scim_provisioning.py`
**Modified:** `src/services/auth/` — add SAML/OIDC federation as new auth method
**Modified:** `src/services/user-management/` — integrate SCIM provisioner
**Modified:** `infra/terraform/modules/` — add dedicated cluster template

---

## Acceptance Criteria

- [ ] SAML SP-initiated SSO flow: redirect to mock IdP → receive assertion → user authenticated in VoiceOS
- [ ] OIDC PKCE flow: authorization code → token exchange → user identity resolved
- [ ] IdentityFederation: IdP group "voiceos-supervisors" → SUPERVISOR role in VoiceOS (configurable mapping)
- [ ] SCIM: `POST /scim/v2/Users` creates user; `DELETE` deactivates user; user cannot log in after deactivation
- [ ] GroupSyncer: IdP group membership change → VoiceOS RBAC role updated within 60s
- [ ] AuditExportPipeline: exports signed NDJSON to test S3 bucket; `ExportSigner.verify()` passes on exported records
- [ ] DataResidencyEnforcer: write to tenant with `data_region=ap-south-1` → routed to regional instance; cross-region write → rejected
- [ ] SSO config is tenant-scoped (Tenant A's SAML config does not affect Tenant B)

---

## Required Tests

**Unit:**
- `test_saml_assertion_valid` — valid SAML assertion → UserIdentity resolved
- `test_saml_assertion_invalid_signature` — tampered assertion → AuthenticationError
- `test_oidc_pkce_flow` — mock OIDC provider → code exchange → identity resolved
- `test_claim_mapping` — IdP groups ["managers"] → MANAGER role
- `test_scim_user_provision` — POST Users → user created in Postgres
- `test_scim_user_deactivate` — DELETE Users → user suspended, login blocked
- `test_scim_group_sync` — group membership change → RBAC role updated
- `test_audit_export_signature_valid` — signed record → verify() returns True
- `test_audit_export_tampered` — modify signed record → verify() returns False
- `test_data_residency_cross_region_rejected` — cross-region write attempt → rejected

**Integration:**
- `test_sso_end_to_end` — full SAML redirect flow with mock IdP → authenticated session in VoiceOS
- `test_scim_provisioning_e2e` — SCIM provisioner creates user → user can log in with correct roles

---

## Definition of Done

- [ ] All AC items checked
- [ ] SAML + OIDC flows tested with mock IdP
- [ ] SCIM API passes SCIM compliance test suite (open-source validator)
- [ ] Audit export: exported file verified tamper-evident
- [ ] Data residency: cross-region write rejection verified
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-033

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** SSO flows use a mock IdP (SAML and OIDC); SCIM tests use `TestPostgres`; audit export uses `FakeObjectStore` (S3 mock); data residency tests use two `TestPostgres` instances representing different regions.

### Files Created

- `src/services/enterprise-platform/__init__.py`
- `src/services/enterprise-platform/sso/saml_provider.py`, `oidc_provider.py`, `federation.py`
- `src/services/enterprise-platform/scim/service.py`, `provisioner.py`, `group_sync.py`
- `src/services/enterprise-platform/audit_export/pipeline.py`, `signer.py`
- `src/services/enterprise-platform/data_residency/enforcer.py`, `region_router.py`
- `src/services/enterprise-platform/cluster_provisioning/service.py`, `templates/`
- `tests/unit/services/test_enterprise_sso.py`, `test_scim.py`, `test_audit_export.py`, `test_data_residency.py`
- `tests/integration/services/test_sso_flow.py`, `test_scim_provisioning.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| SAML IdP | Mock SAML assertion generator (test keypair) | Signs test assertions; tests SAMLIdentityProvider.parse() |
| OIDC Provider | Mock OIDC server (token stub) | Returns test ID token; tests OIDCProvider.validate() |
| Postgres | `TestPostgres` Docker fixture | User records, SSO config, SCIM state |
| S3 (audit export) | `FakeObjectStore` (moto or in-process mock) | Export destination without real S3 |
| Regional Postgres | Two separate `TestPostgres` instances | Data residency: ap-south-1 vs eu-west-1 |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/services/test_enterprise_sso.py tests/unit/services/test_scim.py tests/unit/services/test_audit_export.py tests/unit/services/test_data_residency.py` | All pass |
| SSO integration | `pytest tests/integration/services/test_sso_flow.py` | SAML + OIDC flows authenticated |
| SCIM integration | `pytest tests/integration/services/test_scim_provisioning.py` | User created → login works; deactivated → login blocked |
| Coverage | `pytest --cov=src/services/enterprise-platform --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `SAMLIdentityProvider`: valid test assertion → `UserIdentity` resolved; tampered signature → `AuthenticationError`
- `OIDCProvider` (PKCE): code exchange with mock server → identity resolved
- `IdentityFederation`: IdP group "voiceos-supervisors" → SUPERVISOR role (per configurable mapping)
- SCIM: `POST /scim/v2/Users` → user in `TestPostgres`; `DELETE` → suspended, login blocked
- `GroupSyncer`: group membership PATCH → RBAC role updated in `TestPostgres`
- `AuditExportPipeline`: NDJSON exported to `FakeObjectStore`; `ExportSigner.verify()` returns True
- `DataResidencyEnforcer`: cross-region write attempt → `DataResidencyViolationError`

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| EnterprisePlatformService | K8s Deployment — `voiceos-platform` | SSO federation, SCIM, audit export, data residency |

**Previously deployed services that remain running:**
- All Sprint-004–031 services

**Deployment procedure:**
1. Deploy EnterprisePlatformService; confirm Postgres, KMS, EventBus connectivity
2. Configure test tenant SSO: register mock SAML IdP against `/saml/acs` endpoint
3. End-to-end SAML flow: redirect → assertion received → authenticated session
4. SCIM: configure Okta SCIM integration (test mode) → provision test user → verify in VoiceOS
5. Data residency: write test record for `data_region=ap-south-1` tenant → verify in regional Postgres

**Health checks:**
- EnterprisePlatformService: `GET /health/ready` → 200
- SCIM endpoint: `GET /scim/v2/ServiceProviderConfig` → valid SCIM 2.0 config response

**Integration validation:**
- SAML SSO: test enterprise tenant authenticates via mock IdP → session valid with correct roles
- SCIM provisioning: user created/deactivated via SCIM → correctly reflected in VoiceOS
- Audit export: run export job → NDJSON in S3; verify signatures independently
- Data residency: tenant write routed to correct regional Postgres (query regional DB to confirm)

**Rollback procedure:**
- `kubectl rollout undo deployment/enterprise-platform-service -n voiceos-platform`
- SSO config stored in Postgres; survives rollback

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- SAML assertion with real keypair: `openssl` verify on assertion → valid
- SCIM compliance: run open-source SCIM validator against deployed endpoint
- Audit export tamper evidence: modify exported record → `ExportSigner.verify()` returns False
- Data residency: write under `data_region=ap-south-1` → only appears in ap-south-1 Postgres

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- `/saml/acs` endpoint: accessible to test IdP redirect
- `/scim/v2/` API: authenticated with SCIM bearer token

### Regression Validation

- Walking skeleton e2e test: passes (enterprise auth does not break existing flows)
- Tenant isolation: Tenant A SSO config does not affect Tenant B (verified via API)
- Auth middleware: existing JWT auth still functions alongside SAML/OIDC

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] SAML + OIDC federation implemented and unit-tested
- [ ] SCIM 2.0 API (Users + Groups) implemented and integration-tested
- [ ] AuditExportPipeline with HMAC signing implemented
- [ ] DataResidencyEnforcer implemented; cross-region rejection verified
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] SCIM compliance test suite passes
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] EnterprisePlatformService deployed and healthy
- [ ] SAML SSO end-to-end flow verified on deployed API
- [ ] SCIM user provisioning/deactivation verified
- [ ] Audit export tamper-evident signature verified
- [ ] Data residency enforcement verified on live Postgres
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-033

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `EnterprisePlatformService` to §8.1 Services table — namespace: `voiceos-platform`
- Update §12 Database Schema: add `sso_providers`, `scim_users`, `audit_exports`, `data_residency_policies`, `tenant_regions` tables
- Add environment variables: `SAML_IDP_METADATA_URL`, `OIDC_PROVIDER_URL`, `SCIM_TOKEN` (Vault-managed), `DATA_RESIDENCY_REGION`, `AUDIT_EXPORT_KMS_KEY` (§11)
- Add health check commands for EnterprisePlatformService (§14)
- Update §8.2 Startup Order: EnterprisePlatformService depends on AuthService, AuditService, KMSService

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-029.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add EnterprisePlatformService to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add SSO, SCIM, data residency variable descriptions |

### DR Validation

**SSO and SCIM after rebuild:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh

# SAML SSO flow (requires test IdP configured in .env)
python3 scripts/validate/sso_flow.py --provider saml
# Expected: SAML assertion accepted; JWT issued

# SCIM provisioning
python3 scripts/validate/scim_provision.py --user test-enterprise-user
# Expected: user created; GET /scim/v2/Users returns user
```

**Data residency enforcement:**
```bash
python3 scripts/validate/data_residency.py --tenant enterprise-test
# Expected: all writes go to correct Postgres region per tenant_regions config
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all existing tests pass; SSO and SCIM endpoints operational
```
