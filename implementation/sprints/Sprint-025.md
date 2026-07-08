# Sprint-025 — Admin Portal, AI Configuration & Integration Platform

**Epic:** E6 — SaaS Platform  
**Status:** ⬜ Pending  
**Depends on:** Sprint-021, Sprint-023, Sprint-024  
**Blocks:** Sprint-026  
**Milestone:** SaaS Platform Complete — M-6  

---

## Objective

Complete the SaaS platform with the administration portal backend, AI configuration platform (prompt versioning, model config per tenant/campaign), and the integration/API platform (webhooks with signed delivery, public REST API spec-first, SDK generation stubs).

---

## Architecture References

- Volume 5: Ch13 (Administration Portal), Ch14 (AI Configuration Platform — prompt versioning, model config), Ch15 (Integration Platform — webhooks), Ch16 (API Platform — public REST API, OpenAPI 3.1)
- Volume 4: Ch5, Ch12 (API Security — API keys, rate limiting, signed webhooks)
- DocSuite-04: API Reference
- DocSuite-05: Configuration Reference

---

## Components to Implement

### `src/services/admin-portal/`

```
src/services/admin-portal/
├── __init__.py
├── api.py                  (AdminAPI: FastAPI router — all admin endpoints)
├── tenant_admin.py         (TenantAdminController: tenant config, lifecycle operations)
├── user_admin.py           (UserAdminController: user CRUD, role assignment, SSO config)
├── campaign_admin.py       (CampaignAdminController: campaign CRUD, approval workflow)
├── billing_admin.py        (BillingAdminController: subscription, invoice, usage)
├── audit_admin.py          (AuditAdminController: audit log search, compliance reports)
└── ai_config_admin.py      (AIConfigAdminController: prompt versions, model configs)
```

**AdminAPI (FastAPI):**
- Base path: `/admin/v1/`
- Auth: JWT with ADMIN or SUPERVISOR role required (enforced by AuthMiddleware)
- All endpoints are tenant-scoped (from JWT `tenant_id` claim)
- All admin mutations are audited

### `src/services/ai-config/`

```
src/services/ai-config/
├── __init__.py
├── service.py              (AIConfigService: manages AI configuration)
├── prompt_versioning.py    (PromptVersioningService: immutable prompt versions)
├── model_config.py         (ModelConfigService: adapter selection + inference params per tenant/campaign)
└── eval_runner.py          (EvaluationRunService: triggers AI eval run for a prompt version)
```

**PromptVersioningService:**
- Prompt templates are versioned and immutable once published
- `create_version(name, template, language) -> PromptVersion(id, hash, status=DRAFT)`
- `publish(version_id)` → status=PUBLISHED, cannot be edited
- `pin(campaign_id, version_id)` → campaign uses this exact prompt version
- SHA-256 hash of template stored alongside → RI-7 validation uses this hash
- History: all versions preserved; rollback = pin to older version

**ModelConfigService:**
- `configure(tenant_id, campaign_id, model_config: ModelConfig) -> None`
- `ModelConfig(stt_adapter, stt_model, llm_adapter, llm_model, llm_temperature, tts_adapter, tts_voice)`
- Inheritance: global default → tenant override → campaign override
- All configs stored in Postgres; Redis-cached per call

### `src/services/integration-platform/`

```
src/services/integration-platform/
├── __init__.py
├── service.py              (IntegrationPlatformService)
├── webhook.py              (WebhookService: endpoint registration, event fanout)
├── delivery.py             (WebhookDeliveryEngine: HTTP POST + retry + signature)
└── signature.py            (WebhookSigner: HMAC-SHA256 signature for payload verification)
```

**WebhookDeliveryEngine:**
- Events: `call.completed`, `ptp.created`, `ptp.broken`, `campaign.completed`, `transfer.initiated`
- Delivery: HTTP POST to tenant-configured endpoint with JSON payload
- Signature: `X-VoiceOS-Signature: sha256=HMAC(payload, webhook_secret)` header
- Retry: 3 attempts with exponential backoff (5s, 30s, 5min); then DLQ

### `src/services/api-platform/`

```
src/services/api-platform/
├── __init__.py
├── api.py                  (PublicAPI: FastAPI router — all public endpoints)
├── openapi.py              (OpenAPI schema generation — spec-first, all endpoints documented)
└── sdk_stubs/              (Generated SDK stubs: Python, Node.js — placeholder for Sprint-031)
```

**PublicAPI (spec-first, OpenAPI 3.1):**
- Base path: `/v1/`
- Authentication: API Key via `X-API-Key` header
- Rate limiting: per API key, per plan tier (from Billing entitlement)
- All endpoints documented in OpenAPI 3.1 spec
- Endpoints: `GET /v1/customers/{id}`, `GET /v1/calls/{id}`, `POST /v1/campaigns`, `GET /v1/campaigns/{id}/analytics`, `POST /v1/webhooks`, `GET /v1/invoices`

---

## Files Expected to Change

**New:** `src/services/admin-portal/`, `src/services/ai-config/`, `src/services/integration-platform/`, `src/services/api-platform/`  
**New:** `api-specs/voiceos-public-v1.yaml` (OpenAPI 3.1 spec file)  
**New:** `tests/unit/services/test_admin_portal.py`, `test_ai_config.py`, `test_webhooks.py`, `test_public_api.py`

---

## Acceptance Criteria

- [ ] OpenAPI 3.1 spec file exists and validates (`openapi-generator validate`)
- [ ] All public API endpoints documented in spec with correct request/response schemas
- [ ] Prompt version: create → publish (immutable, cannot be edited after publish)
- [ ] Prompt version: SHA-256 hash stored and matches template content
- [ ] Webhook delivery: endpoint registered → event → POST received → signature valid
- [ ] Webhook retry: POST fails → 3 retries then DLQ entry created
- [ ] Admin API: AGENT role cannot access admin endpoints (403)
- [ ] Model config inheritance: campaign override wins over tenant default

---

## Required Tests

**Unit:**
- `test_prompt_version_immutable_after_publish` — publish → edit → raises
- `test_prompt_version_hash_matches` — version hash = sha256(template)
- `test_webhook_signature_valid` — verify HMAC signature on delivery
- `test_webhook_retry_on_failure` — delivery fails 3 times → DLQ entry
- `test_model_config_inheritance` — campaign config overrides tenant default
- `test_admin_api_agent_role_forbidden` — AGENT JWT → admin endpoint → 403

**Integration:**
- `test_public_api_authenticated` — valid API key → 200; invalid → 401
- `test_openapi_spec_validates` — spec file passes openapi-generator validation

---

## Definition of Done

- [ ] All AC items checked
- [ ] OpenAPI 3.1 spec validates and covers all public endpoints
- [ ] Webhook signatures verified
- [ ] Prompt versioning immutability enforced
- [ ] CI green
- [ ] **Milestone M-6 (SaaS Platform Complete) criteria verified**
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-026

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Admin API and public API use in-process FastAPI test client; webhook delivery uses an HTTP mock server; prompt versioning uses `TestPostgres`.

### Files Created

- `src/services/admin-portal/__init__.py`, `api.py`, `tenant_admin.py`, `user_admin.py`, `campaign_admin.py`, `billing_admin.py`, `audit_admin.py`, `ai_config_admin.py`
- `src/services/ai-config/__init__.py`, `service.py`, `prompt_versioning.py`, `model_config.py`, `eval_runner.py`
- `src/services/integration-platform/__init__.py`, `service.py`, `webhook.py`, `delivery.py`, `signature.py`
- `src/services/api-platform/__init__.py`, `api.py`, `openapi.py`, `sdk_stubs/`
- `api-specs/voiceos-public-v1.yaml`
- `tests/unit/services/test_admin_portal.py`, `test_ai_config.py`, `test_webhooks.py`, `test_public_api.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Postgres | `TestPostgres` Docker fixture | Prompt versions, webhook registrations, model configs |
| Redis | `FakeRedisClient` | Model config cache per-call |
| Webhook endpoint | `aiohttp.web` test server | HTTP mock receiving signed webhook POST |
| AuthMiddleware | Real implementation | JWT signed with test key |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| OpenAPI spec validation | `openapi-generator validate -i api-specs/voiceos-public-v1.yaml` | 0 validation errors |
| Unit tests | `pytest tests/unit/services/test_admin_portal.py tests/unit/services/test_ai_config.py tests/unit/services/test_webhooks.py tests/unit/services/test_public_api.py` | All pass |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `PromptVersioningService`: publish → edit → raises `PromptImmutableError`
- Hash: `sha256(template)` matches stored `version.hash`
- Webhook: registered endpoint receives POST with valid `X-VoiceOS-Signature` on test event
- Webhook retry: POST fails 3× → DLQ entry created
- Admin API: AGENT JWT → `GET /admin/v1/users` → 403
- Model config: campaign override takes precedence over tenant default

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| AdminPortalService | K8s Deployment — `voiceos-platform` | Tenant + user + campaign administration |
| AIConfigService | K8s Deployment — `voiceos-platform` | Prompt versioning + model config per tenant/campaign |
| IntegrationPlatformService | K8s Deployment — `voiceos-platform` | Webhook delivery with retry and signatures |
| APIPlatformService | K8s Deployment — `voiceos-platform` | Public REST API with API key auth + rate limiting |

**Previously deployed services that remain running:**
- All Sprint-004–024 services

**Deployment procedure:**
1. Deploy all 4 services; confirm Postgres, Redis, EventBus connectivity
2. Register test admin user; access admin portal: `GET /admin/v1/tenants` → list of tenants
3. Create prompt version: DRAFT → PUBLISHED; verify immutability via API
4. Register webhook endpoint; trigger `call.completed` event → verify signed POST received
5. Issue test API key; call `GET /v1/customers/{id}` → 200

**Health checks:**
- All 4 services: `GET /health/ready` → 200
- APIPlatformService: OpenAPI spec served at `GET /v1/openapi.json`
- WebhookDeliveryEngine: Prometheus `webhook_delivery_attempts` counter exposed

**Integration validation:**
- Admin portal: ADMIN can create tenant config; AGENT cannot access admin endpoints (403)
- Prompt versioning: prompt published on live API → cannot be modified
- Webhook: real event → signed delivery to registered endpoint
- Public API: invalid API key → 401; valid key over rate limit → 429

**Rollback procedure:**
- `kubectl rollout undo deployment/<service> -n voiceos-platform` per service
- Prompt versions and webhook registrations remain in Postgres; no data loss

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- OpenAPI spec: `GET /v1/openapi.json` → valid 3.1 spec matching all endpoint schemas
- Prompt immutability: `PATCH /ai-config/prompt-versions/{id}` after publish → 422
- Webhook signature: HMAC-SHA256 verify on received delivery → valid
- Admin RBAC: AGENT JWT → admin endpoint → 403 confirmed in access logs

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- APIPlatformService → BillingService (rate limit entitlement check) < 10ms
- WebhookDeliveryEngine → external endpoint: delivery attempt logged with latency

### Regression Validation

- Walking skeleton e2e test: passes (public API now wraps existing services)
- Billing + metering: usage events still collected after API platform deployment
- Campaign management: CampaignAdminController reads campaigns correctly

**Milestone M-6 Verification:**
- All 30+ services deployed across `voiceos-runtime`, `voiceos-data`, `voiceos-platform` namespaces and healthy
- End-to-end SaaS platform functional: tenant provisioning → campaign → call → invoice → analytics → webhook

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] AdminPortalService (all admin controllers) implemented
- [ ] AIConfigService (prompt versioning, model config inheritance) implemented
- [ ] IntegrationPlatformService (webhook delivery, HMAC signatures, retry) implemented
- [ ] APIPlatformService (public API, API key auth, rate limiting) implemented
- [ ] OpenAPI 3.1 spec validates
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Prompt immutability and webhook signature tests pass
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All 4 services deployed and healthy
- [ ] Admin portal accessible; RBAC enforced
- [ ] Prompt versioning immutability enforced on live API
- [ ] Webhook delivery signed and delivered correctly
- [ ] Public API operational with API key auth
- [ ] Milestone M-6 (SaaS Platform Complete) verified
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-026

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-025 completes the SaaS platform — this snapshot captures the full service inventory at Milestone M-6.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `AdminPortalService`, `AIConfigService`, `IntegrationPlatformService`, `APIPlatformService` to Services table (§8.1) — namespace: `voiceos-platform`
- Update §12 Database Schema: add `prompt_versions`, `model_configs`, `webhook_registrations`, `api_keys` tables
- Add environment variables: `ADMIN_PORTAL_URL`, `API_PLATFORM_URL`, `WEBHOOK_SECRET_TEMPLATE` (§11)
- Add health check commands for all 4 new services (§14)
- Add to §14: OpenAPI spec accessible check: `curl http://<api-platform>/v1/openapi.json`
- Update §8.1 service table count: 30+ services now deployed across all namespaces — add summary count

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-019.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add AdminPortal, AIConfig, IntegrationPlatform, APIPlatform to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add admin portal and API platform variable descriptions |

### DR Validation

**Full SaaS platform after rebuild:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all 30+ services healthy; complete SaaS platform functional
```

**Admin portal access:**
```bash
curl -sf -H "Authorization: Bearer $ADMIN_JWT" http://<admin-portal>/admin/v1/tenants
# Expected: 200 with tenant list (not 403)
```

**Public API after rebuild:**
```bash
curl -sf -H "X-API-Key: $TEST_API_KEY" http://<api-platform>/v1/customers/test-001
# Expected: 200 with customer data
```

**OpenAPI spec validation:**
```bash
curl -sf http://<api-platform>/v1/openapi.json | python3 -c "import sys,json; json.load(sys.stdin); print('OpenAPI: valid')"
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; Milestone M-6 criteria verified after rebuild
```
