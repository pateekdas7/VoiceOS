# ADR-005: Full-Stack Frontend + Platform Architecture (Admin Dashboard + Client Dashboard)

**Status:** PROPOSED — awaiting founder approval before any implementation begins
**Date:** 2026-07-25
**Author:** VoiceOS Engineering (drafted with Claude Code)
**Blocks:** New Sprint(s) — see §9 Sequencing. No sprint may start against this ADR until it is APPROVED.
**References:** Volume 2 (Conversation Intelligence), Volume 4 (Compliance & Security — RBAC), Volume 5 (SaaS Platform), Volume 6 (Developer Handbook), Sprint-022 (CRM), Sprint-023 (Campaign Management), Sprint-025 (Admin Portal / AI Config / API Platform), Sprint-026 (SaaS Ops)

---

## 0. How to read this document

This is an **architecture decision**, not code. Per `CLAUDE.md`'s Architecture Change Policy, nothing here gets built until this ADR is approved. It exists because the user's request — "a professional enterprise frontend with an Admin Dashboard and a Client Dashboard, wired to a real backend, end to end" — requires **new architectural components** (a browser-facing API layer, a platform-level admin role, a `Pipeline` entity) that do not exist in Volumes 1–7 today. Everything that *does* already exist is reused, not rebuilt.

Every module below is tagged:
- **REUSE** — an existing service/model/table from Sprint-004–028 is used as-is or with a small additive migration (new column/table), no redesign.
- **NEW** — a component that must be built from scratch because nothing in the current architecture covers it.

---

## 1. Problem

Sprints 022/023/025/026 built a complete backend for CRM, Campaign Management, Admin Portal, Billing, Tenant Management, AI Configuration, and more — but **entirely as Python library classes**. Confirmed by direct inspection of this repo:

- No `frontend/`, `web/`, or any browser-renderable UI exists anywhere in the repository.
- `src/services/api_platform/api.py` is a real Starlette ASGI app, but it is a **spec-first external/programmatic API** — authenticated only via `X-API-Key` (JWT/mTLS deliberately unconfigured, per its own docstring). It has no session/cookie login flow suitable for a human clicking around a browser.
- `src/services/authz/roles.py` defines exactly 5 roles (`ADMIN, SUPERVISOR, MANAGER, AGENT, AUDITOR`) — **all scoped inside a single tenant**. There is no role that spans *across* tenants. The user's "Admin Dashboard (VoiceOS Owner)" — who must see and manage every client/tenant — has no representation in the current RBAC model at all.
- "Campaign" (`src/libs/contracts/models/campaign.py`) is a flat model: one `audience_criteria`, one `retry_policy`, one `default_strategy` string. There is no sub-entity representing "one independent AI workflow with its own prompt/persona/STT/LLM/TTS/business-rules/compliance config" that a campaign could contain *multiple* of. The user's "Pipeline" concept does not exist.
- There is no `VoiceProfile` entity — TTS speaker selection today is a hardcoded `frozenset` allowlist (Sprint-028 PEN-005 fix), not a configurable, DB-backed record a UI could list/create/edit.

Building the requested frontend without addressing these four gaps would mean either (a) the frontend fakes data with no real backend, or (b) engineers improvise ad-hoc backend shortcuts under time pressure — both violate `CLAUDE.md`'s Law of Authority and "never redesign architecture silently" rules. This ADR proposes the minimal, coherent set of additions needed, explicitly, before implementation starts.

---

## 2. Scope & Principles

1. **Reuse first.** Every module is mapped to existing services/tables wherever one already fits. Only 5 genuinely new components are introduced (§4).
2. **Law of Authority holds in the browser too.** The frontend never invents or locally computes authoritative facts (balances, PTP status, compliance verdicts). Every number rendered comes from an API call backed by the existing repositories/services. No client-side business logic duplicates what `PolicyEngine`, `NegotiationEnvelope`, or `CRM` already own.
3. **One integrated system, two front doors.** There is one backend, one database, one auth system. The Admin Dashboard and Client Dashboard are two frontend applications (or two route-groups of one Next.js app — see §8) sitting on top of the same API, differentiated by **actor type** (Platform Owner vs. Tenant Client), not by separate infrastructure.
4. **No frontend feature without a backend owner.** Every nav item in §5 has a named backend service in §6. Where none exists yet, it is explicitly marked NEW with a proposed owner service — nothing is left implicit.
5. **No backend capability silently exposed.** Internal-only services (`gpu_scheduler`, `cost_optimizer`, `incident_response` internals) are surfaced to the UI only through the specific read-only endpoints listed in §6 — not by giving the frontend direct access to internal service objects.

---

## 3. Actor Model (NEW — the foundational gap)

Today's RBAC (`Role` enum) answers "what can this user do *inside their tenant*." It does not answer "does this user get to see *other tenants at all*." We need a layer above it:

```
PlatformActor
├── kind: "PLATFORM_OWNER"          NEW — VoiceOS's own staff (you). Not tied to any tenant.
│   └── platform_role: PlatformRole  NEW enum: PLATFORM_ADMIN | PLATFORM_SUPPORT | PLATFORM_BILLING_OPS
│
└── kind: "TENANT_USER"             REUSE — existing User + Role model, unchanged
    ├── tenant_id: TenantId          REUSE (src/libs/contracts/models/tenant.py)
    └── role: Role                   REUSE (ADMIN | SUPERVISOR | MANAGER | AGENT | AUDITOR)
```

- **NEW table** `platform_users` (mirrors `users` table structure but has no `tenant_id` FK — a platform owner is not a member of any tenant).
- **NEW table** `platform_roles` is not needed as a table — `PlatformRole` is a fixed 3-value enum like `Role` is today, following the exact pattern of `src/services/authz/roles.py`.
- **JWT claim change (additive):** existing JWT payloads gain one new claim, `actor_kind: "platform" | "tenant"`. `AuthMiddleware` (`src/services/auth/middleware.py`) already validates JWTs — this is a new claim it reads, not a new auth mechanism.
- **Client dashboard users are `TENANT_USER` with `role=ADMIN|SUPERVISOR|MANAGER|AGENT|AUDITOR`** — exactly the 5 existing roles. No new tenant-side roles are needed; the "Team Members" page in the client dashboard just becomes the first real UI for `user_management` + `authz`, which today only has API/library access.

This is the single most important structural decision in this ADR: **the Admin Dashboard is not "ADMIN role, tenant=null"** — it is a completely different actor kind with its own login, its own JWT claim, and (per Volume 4 defense-in-depth) its own credential store, so a compromised tenant admin account can never escalate into platform-owner access by construction.

---

## 4. New Components Required (full list)

| # | Component | Why it's needed | Where it lives |
|---|---|---|---|
| 1 | **Web BFF (Backend-for-Frontend) API** | `api_platform.api` is API-key-only and spec-first for external integrators; browsers need cookie/JWT session auth, CSRF protection, and endpoints shaped for UI screens (paginated lists, aggregated dashboard cards), not the external SDK contract. | NEW `src/services/web_api/` — a second Starlette ASGI app, sibling to `api_platform`, reusing `AuthService`/`AuthMiddleware` configured with JWT instead of API-key, and calling the *same* underlying services (`crm`, `campaign_management`, `billing`, etc.) — no business logic duplicated. |
| 2 | **PlatformActor / PlatformRole** | No cross-tenant identity exists today (§3). | NEW `src/services/platform_admin/` (parallel to `authz`), NEW migration `002X_platform_users.py`. |
| 3 | **Pipeline entity** | Campaign has no sub-entity for an independent AI workflow config (§1). | NEW `Pipeline` model in `src/libs/contracts/models/campaign.py` (or new `pipeline.py`), NEW repository, NEW migration. Reuses `ModelConfig`/`PromptVersion` by re-keying their override tier from `campaign_id` to `pipeline_id` (additive column, backward compatible — `campaign_id`-level override becomes "default pipeline for this campaign"). |
| 4 | **VoiceProfile entity** | TTS speaker is a hardcoded allowlist, not a manageable record. | NEW `VoiceProfile` model + table (`voice_profile_id, tenant_id, speaker_name, language, sample_url, status`), owned by `src/services/tts/`. The existing `_ALLOWED_SPEAKERS` frozenset becomes a query against this table. |
| 5 | **Notification service** | No module today sends in-app/email/SMS notifications to dashboard users (distinct from `integration_platform`'s tenant-configured *outbound webhooks*). | NEW `src/services/notifications/` — thin, reuses `integration_platform.delivery` for the actual send mechanics. |

Everything else in the user's module list (§6) maps to **existing** services.

---

## 5. Information Architecture

### 5.1 Admin Dashboard (Platform Owner — actor kind `PLATFORM_OWNER`)

```
Admin
├── Dashboard              -- platform-wide KPI overview
├── Clients                -- list/create/edit/suspend/delete tenants; click-through to that tenant's workspace (impersonation-safe read view, §6.1)
├── Revenue                -- MRR/ARR, per-tenant revenue breakdown
├── Billing                -- subscriptions, invoices, dunning, plan changes
├── Infrastructure          -- GPU/CPU fleet, node health, VRAM budget
├── Monitoring              -- active calls (platform-wide), queues/workers, alerts
├── Analytics                -- cross-tenant usage, adoption, model performance
├── Users & Roles            -- platform_users (VoiceOS staff) management
├── Audit Logs                -- platform-wide audit trail (including cross-tenant admin actions)
└── Platform Settings          -- feature flags, global model defaults, entitlement tiers
```

### 5.2 Client Dashboard (Tenant User — actor kind `TENANT_USER`, scoped to their own `tenant_id`)

```
Client
├── Dashboard          -- this tenant's KPI overview only
├── Campaigns          -- list/create; each campaign contains N Pipelines
│   └── [Campaign]
│       └── Pipelines
│           └── [Pipeline]   -- the full config surface: Details / Prompt / Persona / Language / Region /
│                                Time Zone / STT / LLM / TTS / Business Rules / Compliance Rules /
│                                CRM Mapping / Lead Assignment / Retry Rules / Calling Schedule /
│                                Working Hours / Analytics / Testing / Version History
├── Leads               -- CampaignAudienceMember + Customer records not yet converted
├── Live Calls           -- real-time active call monitor (this tenant only)
├── Call History          -- CampaignResult + call transcripts/recordings
├── Analytics              -- this tenant's call/campaign analytics
├── CRM                     -- Customer/Party/LoanAccount records
├── Collections               -- PTP, settlements, escalations, EMI schedule
├── Reports                     -- scheduled/exported reports
├── Team Members                  -- this tenant's Users + Role assignment (ADMIN/SUPERVISOR/MANAGER/AGENT/AUDITOR)
└── Settings                        -- tenant profile, integrations, notification preferences, voice profiles
```

**Boundary enforcement:** every Client Dashboard API call is scoped by `tenant_id` extracted from the JWT — never from a request parameter — exactly as `tenant_isolation.py` already enforces for the backend today. The Admin Dashboard is the only surface allowed to pass an explicit `tenant_id` parameter, and only for `PLATFORM_ADMIN`/`PLATFORM_SUPPORT` roles.

---

## 6. Module Architecture (per-module specification)

Format per the user's own example. `Backend Service` is tagged **REUSE** (file path given) or **NEW**.

### 6.1 Client (Tenant) — Admin Dashboard

| Dimension | Spec |
|---|---|
| Frontend | Admin → Clients (list, detail, create modal) |
| Backend Service | **REUSE** `src/services/tenant_management/` (`lifecycle.py`, `suspension.py`, `deletion.py`, `provisioner.py`) + **NEW** `platform_admin/` for the auth boundary |
| API | `POST /admin/clients`, `GET /admin/clients`, `GET /admin/clients/{id}`, `PATCH /admin/clients/{id}`, `POST /admin/clients/{id}/suspend`, `DELETE /admin/clients/{id}` |
| DB | **REUSE** `tenants` (migration `0001_tenants.py`), `0018_tenant_lifecycle_and_org_management.py` |
| Relationships | `tenant_id` is the root FK for nearly every other table in the system |
| Validation | Tenant name uniqueness, plan tier must exist in `billing` rate card |
| RBAC | `PLATFORM_ADMIN` only for create/suspend/delete; `PLATFORM_SUPPORT` read-only |
| Business Logic | **REUSE** `TenantLifecycleService`, `TenantSuspensionService`, `TenantProvisioner` |
| Events | `tenant.created`, `tenant.suspended`, `tenant.deleted` (already emitted per Sprint-018) |
| Audit | **REUSE** `audit_log` table (`0010_audit_log.py`) via existing `AuditRepository` |
| Analytics | Client count/growth feeds Admin → Dashboard KPIs via **REUSE** `saas_ops` |

### 6.2 Campaigns

| Dimension | Spec |
|---|---|
| Frontend | Client → Campaigns |
| Backend Service | **REUSE** `src/services/campaign_management/service.py` |
| API | `POST/GET/PATCH /campaigns`, `POST /campaigns/{id}/start\|pause\|complete` |
| DB | **REUSE** `campaigns` (`0011_campaigns.py`, extended `0020_campaign_lifecycle_contact_center_hitl.py`) |
| Relationships | `campaign.tenant_id → tenants`; **NEW** `campaign` 1—N `pipelines` (currently campaign is a leaf; becomes a container) |
| Validation | **REUSE** `AudienceCriteria`/`RetryPolicy` pydantic validation already in `campaign.py` |
| RBAC | `write:campaigns` permission (**REUSE** `PERM_WRITE_CAMPAIGNS`, held by ADMIN/SUPERVISOR/MANAGER) |
| Business Logic | **REUSE** `lifecycle.py`, `scheduler.py`, `call_dispatcher.py`, `retry_policy.py`, `ab_testing.py` |
| Events | Campaign state transitions (existing lifecycle events) |
| Audit | **REUSE** audit_log |
| Analytics | **REUSE** `campaign_analytics.py` |

### 6.3 Pipelines (NEW entity, per §4.3)

| Dimension | Spec |
|---|---|
| Frontend | Client → Campaigns → [Campaign] → Pipelines → [Pipeline] (18-tab config page: Details, Prompt, Persona, Language, Region, Time Zone, STT, LLM, TTS, Business Rules, Compliance Rules, CRM Mapping, Lead Assignment, Retry Rules, Calling Schedule, Working Hours, Analytics, Testing, Version History) |
| Backend Service | **NEW** `src/services/pipeline_config/` — thin orchestrator that composes **REUSE** `ai_config.ModelConfigService` (STT/LLM/TTS tabs), **REUSE** `ai_config.PromptVersioningService` (Prompt + Version History tabs), **REUSE** `ai_config.eval_runner` (Testing tab), **REUSE** `policy_engine` (Compliance Rules tab), **REUSE** `campaign_management.retry_policy`/`scheduler` (Retry Rules, Calling Schedule, Working Hours tabs — re-scoped from campaign-level to pipeline-level override), **REUSE** `crm.context_assembler` (CRM Mapping tab) |
| API | `POST/GET/PATCH /campaigns/{id}/pipelines`, `GET/PUT /pipelines/{id}/config/{tab}`, `POST /pipelines/{id}/test-call`, `GET /pipelines/{id}/versions` |
| DB | **NEW** `pipelines` table (`pipeline_id, campaign_id, tenant_id, name, status, persona, language, region, timezone, created_at, updated_at, created_by`). **REUSE** `ai_config_model_configs`/prompt-version tables, **additive column** `pipeline_id` (nullable, replaces the resolution key's finest tier) |
| Relationships | `pipeline.campaign_id → campaigns`; `pipeline.tenant_id → tenants`; `ModelConfig.pipeline_id → pipelines` (new tier below tenant/campaign in the existing inheritance chain) |
| Validation | Adapter names must be in `KNOWN_STT_ADAPTERS`/`KNOWN_LLM_ADAPTERS`/`KNOWN_TTS_ADAPTERS` (**REUSE** existing guard in `model_config.py`) |
| RBAC | Same as Campaigns (`write:campaigns`) — a pipeline is a sub-resource of its campaign |
| Business Logic | Config inheritance: global → tenant → campaign → **pipeline** (one new tier appended to the existing chain, not a new mechanism) |
| Events | `pipeline.published`, `pipeline.version_pinned` |
| Audit | **REUSE** audit_log — every tab save is a write event |
| Analytics | Per-pipeline call outcome breakdown, feeds Client → Analytics |

### 6.4 Leads

| Dimension | Spec |
|---|---|
| Frontend | Client → Leads |
| Backend Service | **REUSE** `src/services/campaign_management/audience.py` (`CampaignAudienceMember`) + **REUSE** `crm.service` (`Customer`/`Party`) |
| API | `GET /leads`, `POST /leads/import`, `PATCH /leads/{id}` (status, assignment) |
| DB | **REUSE** `campaign_audience` table + `customers`; **additive column** `lead_status` on `campaign_audience` if pre-qualification campaigns need a status distinct from collections DPD-based criteria |
| Relationships | `campaign_audience.customer_id → customers`, `.campaign_id → campaigns` |
| Validation | **REUSE** `AudienceCriteria` exclusion rules (DNC, active PTP) |
| RBAC | `write:campaigns` for import/assign |
| Business Logic | **REUSE** `CampaignAudienceService` |
| Events | `lead.imported`, `lead.assigned` |
| Audit | **REUSE** audit_log |
| Analytics | Conversion funnel via **REUSE** `campaign_analytics` |

### 6.5 Contacts / CRM

| Dimension | Spec |
|---|---|
| Frontend | Client → CRM |
| Backend Service | **REUSE** `src/services/crm/` (`service.py`, `party.py`, `context_assembler.py`, `importer.py`) |
| API | `GET/POST/PATCH /customers`, `GET /customers/{id}/context` |
| DB | **REUSE** `customers` (`0004_customers.py`) |
| Relationships | `customer` 1—N `loan_accounts`, 1—N `contacts` (`CustomerContact`), N `party` roles |
| Validation | **REUSE** existing `Party`/`Customer` pydantic models (immutable, `Address` frozen) |
| RBAC | `read:all` to view, `write:all`/tenant-ADMIN to edit (CRM data is sensitive PII — Volume 4) |
| Business Logic | **REUSE** `CustomerContextAssembler` — this is the *only* source of "facts" the LLM may use (Law of Authority) |
| Events | `customer.updated` |
| Audit | **REUSE** `pii_audit_hash_chain` (`0017_pii_audit_hash_chain.py`) |
| Analytics | Customer segment breakdown |

### 6.6 Calls / Live Calls / Call History

| Dimension | Spec |
|---|---|
| Frontend | Client → Live Calls, Client → Call History; Admin → Monitoring (platform-wide live view) |
| Backend Service | **REUSE** `src/services/contact_center/` (`router.py`, `live_transfer.py`, `supervisor.py`, `agent_screen.py`) for live; **REUSE** `campaign_management` `CampaignResult` for history |
| API | `GET /calls/live` (WebSocket/SSE for real-time), `GET /calls/history`, `GET /calls/{id}/transcript`, `POST /calls/{id}/barge-in` |
| DB | **REUSE** `campaign_results`, call session state (Redis, per Volume 3) |
| Relationships | `call_result.campaign_id → campaigns`, `.customer_id → customers` |
| Validation | n/a (read-mostly) |
| RBAC | `monitor:calls`/`barge_in:calls` (**REUSE** `PERM_MONITOR_CALLS`, `PERM_BARGE_IN` — SUPERVISOR only) |
| Business Logic | **REUSE** `ContactCenterService` |
| Events | Real-time call state events (existing event bus) |
| Audit | **REUSE** `read:transcripts` permission gate (AUDITOR) |
| Analytics | **REUSE** `call_analytics.py` |

### 6.7 Collections (PTP, Settlements, Escalations, EMI)

| Dimension | Spec |
|---|---|
| Frontend | Client → Collections |
| Backend Service | **REUSE** `src/services/collections/` (`promise_to_pay.py`, `settlement.py`, `escalation.py`, `emi_schedule.py`, `callback.py`) |
| API | `GET/POST /ptp`, `GET/POST /settlements`, `GET /escalations`, `GET /emi-schedule/{loan_id}` |
| DB | **REUSE** `promises_to_pay` (`0007`), `0019_settlement_authorization.py` |
| Relationships | `ptp.loan_account_id → loan_accounts`, `.customer_id → customers` |
| Validation | **REUSE** `NegotiationEnvelope` floor/ceiling checks (Volume 2) |
| RBAC | `write:campaigns`-equivalent tenant role for creating PTP manually; agent view for own escalations |
| Business Logic | **REUSE** — this is Volume 2's Negotiation Engine surfaced, not reimplemented |
| Events | `ptp.created`, `settlement.authorized`, `escalation.raised` |
| Audit | **REUSE** audit_log |
| Analytics | PTP honor rate, settlement rate |

### 6.8 Team Members / Roles / Permissions

| Dimension | Spec |
|---|---|
| Frontend | Client → Team Members; Admin → Users & Roles (platform staff) |
| Backend Service | **REUSE** `src/services/user_management/` (`invitation.py`) + **REUSE** `src/services/authz/` (`rbac_engine.py`, `roles.py`) for tenant side; **NEW** `platform_admin/` for platform staff |
| API | `GET/POST /team`, `POST /team/invite`, `PATCH /team/{id}/role`, `DELETE /team/{id}` |
| DB | **REUSE** `users` (`0003_users.py`) |
| Relationships | `user.tenant_id → tenants`, `user.role` enum column |
| Validation | Role must be one of the 5 existing `Role` values; last-ADMIN-cannot-be-removed guard |
| RBAC | Only `PERM_WRITE_USERS` holders (ADMIN/SUPERVISOR) can invite/change roles |
| Business Logic | **REUSE** `UserManagementService`, `RBACEngine` |
| Events | `user.invited`, `user.role_changed` |
| Audit | **REUSE** audit_log — role changes are always audited (Volume 4) |
| Analytics | Seat utilization vs. plan entitlement (**REUSE** `billing.entitlement`) |

### 6.9 Reports / Analytics

| Dimension | Spec |
|---|---|
| Frontend | Client → Reports, Client → Analytics; Admin → Analytics (cross-tenant) |
| Backend Service | **REUSE** `src/services/reporting/` (`exporter.py`, `scheduler.py`) + **REUSE** `src/services/analytics/` (`aggregation.py`, `realtime.py`) + **REUSE** `bi_platform` for Admin cross-tenant rollups |
| API | `GET /reports`, `POST /reports/schedule`, `GET /reports/{id}/export`, `GET /analytics/*` |
| DB | **REUSE** `0022_analytics_daily.py`, `0023_bi_facts_schema.py` |
| Relationships | Aggregates over campaigns/calls/customers — read-only, no new FKs |
| Validation | n/a |
| RBAC | `view:analytics` (MANAGER+) |
| Business Logic | **REUSE** — no new computation, only new presentation |
| Events | n/a |
| Audit | Report export events logged |
| Analytics | (is itself the analytics layer) |

### 6.10 Voice Profiles (NEW entity, per §4.4)

| Dimension | Spec |
|---|---|
| Frontend | Client → Settings → Voice Profiles; referenced from Pipeline → TTS tab |
| Backend Service | **NEW** field on **REUSE** `src/services/tts/service.py` |
| API | `GET/POST /voice-profiles` |
| DB | **NEW** `voice_profiles` table |
| Relationships | `pipeline.tts_config.voice_profile_id → voice_profiles` |
| Validation | Speaker must pass the existing `_ALLOWED_SPEAKERS`-equivalent registration check (Sprint-028 PEN-005) — now a DB lookup instead of a hardcoded frozenset |
| RBAC | `write:campaigns`-tier tenant role |
| Business Logic | Thin CRUD; actual synthesis unchanged (**REUSE** `tts/adapters`) |
| Events | `voice_profile.created` |
| Audit | **REUSE** audit_log |
| Analytics | MOS score per voice profile (**REUSE** existing MOS scoring pipeline, Sprint-029) |

### 6.11 AI Configuration / STT / LLM / TTS

| Dimension | Spec |
|---|---|
| Frontend | Nested inside Pipeline config tabs (§6.3) — **not** separate top-level pages, since config is always pipeline-scoped |
| Backend Service | **REUSE** `src/services/ai_config/` (`model_config.py`), `src/services/stt/`, `src/services/llm_runtime/`, `src/services/tts/` |
| API | `GET/PUT /pipelines/{id}/config/stt\|llm\|tts` |
| DB | **REUSE** `0024_admin_ai_config_integration_api.py` model_config tables, extended with `pipeline_id` (§6.3) |
| Relationships | Inheritance chain global→tenant→campaign→pipeline |
| Validation | **REUSE** `InvalidModelConfigError` guard against unregistered adapters |
| RBAC | `write:campaigns` |
| Business Logic | **REUSE** `ModelConfigService.resolve()` |
| Events | `model_config.updated` |
| Audit | **REUSE** audit_log |
| Analytics | Latency/quality per adapter combination (**REUSE** `cost_optimizer`, `ops_analytics`) |

### 6.12 Dialer / Scheduler / Calling Schedule / Working Hours / Retry Rules

| Dimension | Spec |
|---|---|
| Frontend | Nested inside Pipeline config tabs (Calling Schedule, Working Hours, Retry Rules) |
| Backend Service | **REUSE** `campaign_management/scheduler.py`, `call_dispatcher.py`, `retry_policy.py` |
| API | `PUT /pipelines/{id}/config/schedule\|retry` |
| DB | **REUSE** `campaigns.daily_start_hour/daily_end_hour/timezone`, `RetryPolicy` — re-scoped to pipeline as an override tier |
| Relationships | n/a beyond pipeline |
| Validation | **REUSE** existing `RetryPolicy` field constraints (`max_attempts ≤ 10`, etc.) |
| RBAC | `write:campaigns` |
| Business Logic | **REUSE** — this is literally RBI calling-hours compliance (Volume 4), not new logic |
| Events | n/a |
| Audit | **REUSE** |
| Analytics | Retry effectiveness (already in `campaign_analytics`) |

### 6.13 Integrations

| Dimension | Spec |
|---|---|
| Frontend | Client → Settings → Integrations |
| Backend Service | **REUSE** `src/services/integration_platform/` (`webhook.py`, `delivery.py`, `signature.py`) |
| API | `GET/POST /integrations/webhooks`, `POST /integrations/webhooks/{id}/test` |
| DB | **REUSE** `0016`/webhook registration tables |
| Relationships | `webhook.tenant_id → tenants` |
| Validation | **REUSE** `WEBHOOK_EVENT_TYPES` allowlist |
| RBAC | tenant ADMIN only |
| Business Logic | **REUSE** — signed delivery already implemented |
| Events | `webhook.delivered`, `webhook.failed` |
| Audit | **REUSE** |
| Analytics | Delivery success rate |

### 6.14 Notifications (NEW, per §4.5)

| Dimension | Spec |
|---|---|
| Frontend | Bell icon / notification center in both dashboards |
| Backend Service | **NEW** `src/services/notifications/`, thin wrapper reusing **REUSE** `integration_platform.delivery` for the transport |
| API | `GET /notifications`, `PATCH /notifications/{id}/read` |
| DB | **NEW** `notifications` table (`notification_id, actor_id, actor_kind, type, payload, read_at, created_at`) |
| Relationships | `actor_id` polymorphic to either `users.user_id` or `platform_users.user_id` (discriminated by `actor_kind`) |
| Validation | n/a |
| RBAC | User sees only their own notifications |
| Business Logic | Triggered by existing domain events (`campaign.completed`, `ptp.created`, etc.) — no new business rules, just new event subscribers |
| Events | Consumes existing events, does not add new ones |
| Audit | Not independently audited (notifications are derived, not authoritative) |
| Analytics | n/a |

### 6.15 Monitoring / Infrastructure (Admin only)

| Dimension | Spec |
|---|---|
| Frontend | Admin → Infrastructure, Admin → Monitoring |
| Backend Service | **REUSE** `src/services/gpu_scheduler/` (`vram_ledger.py`, `priority_queue.py`, `admission.py`, `failover.py`), **REUSE** `monitoring/` (Prometheus/Grafana, Sprint-027), **REUSE** `incident_response/`, `cost_optimizer/`, `ops_analytics/` |
| API | `GET /admin/infra/gpu`, `GET /admin/infra/queues`, `GET /admin/infra/nodes` — thin read proxies onto existing Prometheus/service internals, no new telemetry pipeline |
| DB | n/a — reads from existing Prometheus/Redis state |
| Relationships | n/a |
| Validation | n/a |
| RBAC | `PLATFORM_ADMIN`/`PLATFORM_SUPPORT` only — **never** exposed to tenant clients |
| Business Logic | **REUSE** entirely |
| Events | Existing Alertmanager routing (Sprint-027) — surfaced as read-only feed |
| Audit | Admin access to infra views is itself audited |
| Analytics | **REUSE** `ops_analytics` |

### 6.16 Billing / Subscription / Revenue

| Dimension | Spec |
|---|---|
| Frontend | Client → Settings → Billing (own invoices); Admin → Billing, Admin → Revenue (platform-wide) |
| Backend Service | **REUSE** `src/services/billing/` (`subscription.py`, `invoice.py`, `payment.py`, `rate_card.py`, `entitlement.py`) |
| API | `GET /billing/invoices`, `GET /admin/billing/revenue`, `POST /admin/billing/plan-change` |
| DB | **REUSE** `0012_billing_subscriptions.py`, `0021_billing_metering_extensions.py` |
| Relationships | `subscription.tenant_id → tenants` |
| Validation | **REUSE** existing rate-card/entitlement checks |
| RBAC | Client: tenant ADMIN, own tenant only. Admin: `PLATFORM_BILLING_OPS` |
| Business Logic | **REUSE** entirely |
| Events | `invoice.generated`, `payment.received` |
| Audit | **REUSE** |
| Analytics | MRR/ARR (**REUSE** `saas_ops`) |

### 6.17 Settings (both dashboards)

| Dimension | Spec |
|---|---|
| Frontend | Client → Settings (tenant profile, notification prefs, voice profiles, integrations); Admin → Platform Settings (feature flags, global defaults, entitlement tiers) |
| Backend Service | **REUSE** `src/services/tenant_management/`, **REUSE** `src/services/saas_ops/feature_flags.py` |
| API | `GET/PATCH /settings/tenant`, `GET/PATCH /admin/settings/platform` |
| DB | **REUSE** `tenants` extended fields |
| Relationships | n/a |
| Validation | n/a beyond existing tenant field validation |
| RBAC | tenant ADMIN / `PLATFORM_ADMIN` respectively |
| Business Logic | **REUSE** |
| Events | `tenant.settings_updated` |
| Audit | **REUSE** |
| Analytics | n/a |

---

## 7. Database — new tables and relationships (additive only)

No existing table is altered destructively. All changes are new tables or new nullable columns.

```
tenants (existing) ──────────────────────────┐
  │ 1                                          │
  │ N                                          │
  ▼                                            │
campaigns (existing)                           │
  │ 1                                          │
  │ N  (NEW FK)                                │
  ▼                                            │
pipelines (NEW)                                │
  │ 1                                          │
  ├──N── ai_config_model_configs (existing, +pipeline_id column)
  ├──N── prompt_versions (existing, pinned per pipeline via +pipeline_id)
  └──N── voice_profiles (NEW, referenced by tts_config)

platform_users (NEW) ── no tenant FK — root-level, mirrors `users` shape
notifications (NEW) ── actor_id polymorphic → users.user_id | platform_users.user_id
```

Proposed migrations (next available numbers after `0027_performance_baselines.py`):
- `0028_pipelines.py` — `pipelines` table + `pipeline_id` FK columns on model_config/prompt_version tables
- `0029_voice_profiles.py`
- `0030_platform_users.py`
- `0031_notifications.py`

---

## 8. API & Frontend Architecture

- **Frontend:** one Next.js (App Router, TypeScript) application with two route groups — `app/(admin)/...` and `app/(client)/...` — sharing a component/design-system library but with **separate layouts, separate login pages, and middleware that rejects cross-actor-kind access** (a platform JWT can never render `(client)` routes and vice versa).
- **Auth:** NextAuth.js (or equivalent) with two providers: Credentials (id/password against **REUSE** `AuthService`) and Google OAuth for sign-up — both issue the **same JWT shape** `AuthService` already validates, just with the new `actor_kind` claim (§3) added.
- **BFF API:** NEW `src/services/web_api/` (Starlette, sibling to `api_platform`) exposes the endpoints listed throughout §6. It is a **thin routing/serialization layer** — every handler delegates to an existing service method; no business logic lives in this layer (per Law of Authority: "business logic never lives inside prompts" extends here to "never lives inside the BFF" either).
- **Real-time:** Live Calls / Monitoring use Server-Sent Events or WebSocket, backed by the existing Redis pub/sub used internally for call state (Volume 3) — not a new message bus.

---

## 9. Sequencing (per CLAUDE.md — one sprint at a time)

This ADR is too large for one sprint. Recommended breakdown for a follow-up `implementation/sprints/Sprint-030.md` (and beyond), **to be written and approved separately** once this ADR itself is approved:

1. **Sprint-030 — Foundation:** `web_api` BFF skeleton, PlatformActor/PlatformRole + `platform_users` migration, Next.js app scaffold, both login pages (credentials + Google OAuth), both dashboard shells with real navigation (empty states behind each nav item).
2. **Sprint-031 — Admin core:** Clients CRUD (§6.1), Admin Dashboard KPIs, Billing/Revenue read views.
3. **Sprint-032 — Client core:** Team Members (§6.8), CRM (§6.5), Campaigns list/create (§6.2, using existing flat Campaign — no Pipeline yet).
4. **Sprint-033 — Pipeline entity:** the `pipelines` migration, `pipeline_config` service, and the full 18-tab config UI (§6.3) — this is the biggest single sprint given the tab count.
5. **Sprint-034 — Calls & Collections:** Live Calls, Call History, Collections (§6.6, §6.7).
6. **Sprint-035 — Reports/Analytics/Voice Profiles/Notifications/Integrations/Settings:** the remaining REUSE-heavy, lower-risk modules.
7. **Sprint-036 — Infrastructure/Monitoring (Admin):** GPU/CPU fleet views — deferred last since it depends on GPU node access, which is already a known constraint this session (per `CURRENT_SPRINT.md`).

Each sprint follows the existing Phase 1 (local/mock) → Phase 2 (real infrastructure) pattern already used throughout this project.

---

## 10. Additional Production & Operational Modules (identified, not user-specified)

The modules in §6 cover the business-facing product. A real enterprise platform also needs the **operational tooling behind it** — queues, event bus, telephony, secrets, DB/cache health, security/compliance posture, cost. Direct inspection of this repo shows **almost all of the underlying machinery already exists** (Sprints 003–028 built serious reliability/security infrastructure) — it has simply never been surfaced to a screen. The gap here is UI + a handful of thin read APIs, not new backend engines, with a few explicitly-marked exceptions.

Format: **Why** / **Frontend?** / **Depends on** (services, DB, Redis, event bus) / **Who uses it, how**.

### 10.1 Queueing, Workers & Async Execution

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **GPU Priority Queue Monitor** | Operators must see why a call is waiting on inference (CRITICAL/HIGH/NORMAL/LOW starvation) before it becomes a latency incident. | Admin → Infrastructure → GPU Queue (NEW page, REUSE data) | **REUSE** `gpu_scheduler/priority_queue.py`, `vram_ledger.py`, `admission.py`. Reads in-memory scheduler state via a new thin `GET /admin/infra/gpu/queue` proxy. | Platform admin watches queue depth per priority tier live during a load spike; escalates via Incident Response (below) if CRITICAL items wait >budget. |
| **Model Pool / Worker Manager** | STT/LLM/TTS run as pooled model instances per node — admins need to see which are warm, draining, or failed over. | Admin → Infrastructure → Model Pool (NEW page) | **REUSE** `gpu_scheduler/model_pool.py`, `failover.py`, `scheduler.py`. | Confirms a model finished warming before routing traffic to a newly-restored GPU node (mirrors the manual `restore.sh` checks already done by hand each sprint). |
| **Event Bus Monitor** | `src/libs/event_bus/` (Redis Streams: `bus.py`, `consumer.py`, `router.py`, `dedup.py`) is the backbone every domain event (campaign, PTP, tenant, audit) flows through. Silent consumer-group lag is invisible today. | Admin → Infrastructure → Event Bus (NEW page) | **REUSE** `event_bus.bus`/`consumer` internals (consumer-group lag, stream length) exposed via NEW `GET /admin/infra/eventbus`. | Diagnose "why didn't the notification fire" by checking consumer lag before assuming application-layer bug. |
| **Dead Letter Queue (DLQ) Viewer** | `event_bus/dlq.py` already routes poison messages to `dlq:{stream}` after `DEFAULT_MAX_RETRIES=3` — but nothing today lets a human inspect or replay them. This is a real gap: DLQ'd events are silent data-loss risk until surfaced. | Admin → Infrastructure → DLQ (NEW page) | **REUSE** `DLQHandler`; NEW `GET /admin/infra/dlq`, `POST /admin/infra/dlq/{id}/replay`. | Platform admin reviews failed events weekly, replays transient failures, escalates persistent poison messages to engineering. |
| **Retry Queue / Failed Jobs** | Campaign call retries (`retry_policy.py`) and report scheduling (`reporting/scheduler.py`) both have retry semantics but no unified "what's pending retry, what gave up" view. | Client → Campaigns → [Pipeline] → Retry Rules tab already shows config (§6.12); this adds a **read-only execution view** of actual retry state. | **REUSE** `campaign_management/retry_policy.py` + `CampaignResult.outcome_code`. | Client sees "14 calls pending retry, 3 exhausted retries" per pipeline — distinguishes config (rules) from runtime (queue state). |
| **Pipeline Execution Monitor** | With Pipelines introduced (§6.3), operators need a funnel view: queued → dispatched → in-call → completed/failed, per pipeline, in real time. | Client → Campaigns → [Pipeline] → Analytics tab (extends §6.3, NOT a separate top-level page) | **REUSE** `call_dispatcher.py` + `gpu_scheduler` + `event_bus` — aggregation only, NEW read endpoint, no new state machine. | Client watches a new pipeline's first 50 calls funnel through in real time before trusting it at full volume. |

### 10.2 AI Runtime & Telephony Monitoring

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **AI Inference Monitor (STT/LLM/TTS)** | Per-adapter latency/error rate is exactly what ADR-001/ADR-004 were fought over via manual log-reading. Should be a live dashboard, not a one-off investigation. | Admin → Monitoring → AI Inference (NEW page) | **REUSE** `observability/tracer.py` + Sprint-027's Jaeger/Prometheus stack (already deployed) — this page is a curated Grafana-dashboard embed/proxy, not new telemetry. | Confirms TTS TTFA stays ≤750ms (ADR-004 budget) post-deploy without re-running a manual latency script. |
| **WebSocket / Live Session Monitor** | `media_gateway/twilio_ws_entrypoint.py` and `session_gate.py` manage live audio WebSocket sessions — no visibility today into active session count/health. | Admin → Monitoring → Live Sessions (NEW page); surfaces into Client → Live Calls (§6.6) filtered to tenant | **REUSE** `media_gateway.session_gate` session registry via NEW `GET /admin/infra/ws-sessions`. | Confirms a customer's "my calls just dropped" report by checking whether their WS sessions terminated abnormally. |
| **Telephony / SIP-Provider Status** | `media_gateway/adapters/` integrates Twilio (per `twilio_ws_entrypoint.py`) — provider outages must be visible before they're mistaken for a VoiceOS bug. | Admin → Infrastructure → Telephony (NEW page) | **REUSE** `media_gateway/service.py`, `call_recorder.py`; NEW provider health-check endpoint (poll Twilio status API). | First place an on-call engineer checks when call volume drops — rules out "Twilio is down" before debugging the pipeline. |
| **API Gateway Dashboard** | `api_platform` already tracks per-tenant rate limits (`rate_limits.py`) and API-key lifecycle (`api_key_lifecycle.py`) but exposes no view of consumption. | Admin → Infrastructure → API Platform (NEW page); Client → Settings → API Usage (own tenant only) | **REUSE** `api_platform/metrics.py`, `rate_limits.py`, `api_key_lifecycle.py`. | Client checks "why am I getting 429s" against their tier's `TIER_RPS_LIMITS`; admin spots a tenant about to need a tier upgrade. |
| **HITL Escalation Queue** *(notable gap in the original module list — this backend already assumes a UI exists)* | `src/services/hitl/dashboard.py`'s own docstring says "read-only queue-depth/SLA-status view **for the supervisor escalation dashboard**" — this was built Sprint-0xx explicitly anticipating a screen that was never added. | Client → Live Calls → Escalations tab (tenant-scoped); Admin → Monitoring → HITL SLA (cross-tenant rollup) | **REUSE** `hitl/dashboard.py`, `queue.py`, `review_api.py`, `sla_enforcer.py`, `override_logger.py` — fully built, zero new backend. | Supervisor (tenant SUPERVISOR role, `PERM_DECIDE_HITL`) works the escalation queue and records approve/reject/override decisions — this is Volume 4 Ch15's core human-oversight mechanism, currently unreachable by any human. |

### 10.3 Platform Infrastructure Health

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **Service Health Dashboard** | `src/libs/health/aggregator.py` + `probe.py` already implement a generic health-check aggregator; `service_discovery/registry.py` tracks live service instances. Nothing renders it. | Admin → Infrastructure → Service Health (NEW page) | **REUSE** `health.aggregator`, `service_discovery.registry` | Single-glance "is everything up" view before starting a deploy or investigating an incident. |
| **Cache (Redis) Monitor** | `redis_client/` underlies rate limiting, TTL-guarded config cache, distributed locks — no visibility into hit rate/memory/lock contention today. | Admin → Infrastructure → Cache (NEW page) | **REUSE** `redis_client/client.py`, `ttl_guard.py`, `lock.py`; Redis `INFO` command | Diagnoses "config changes aren't taking effect" (stale TTL-guarded cache) without SSH-ing into the node. |
| **Database Health** | Connection pool saturation and replication lag precede almost every production incident class. | Admin → Infrastructure → Database (NEW page) | **REUSE** `health.aggregator` DB probe; Postgres `pg_stat_activity` | Confirms connection pool isn't the bottleneck before blaming application code during a slowdown. |
| **Backup & Recovery / DR Status** | `0014_snapshots_and_recovery_log.py` + `src/libs/recovery/` (`recovery_manager.py`, `strategies/`) + `infra/dr/` runbooks already exist (Sprint-027's real DR drill hit 4s RTO) — but the result lives only in a markdown report today. | Admin → Infrastructure → Backup & DR (NEW page) | **REUSE** `recovery.recovery_manager`, `infra/dr/` scripts/reports | Confirms last successful snapshot + last DR drill result before a founder/compliance audit, without grepping markdown files. |
| **Logs Viewer** | Sprint-027 deployed the full Loki/FluentBit/Grafana stack with verified `call_id`-keyed search — but only reachable via Grafana's own UI today, not embedded in either dashboard. | Admin → Infrastructure → Logs (NEW page, embeds/proxies Grafana Loki); Client → Call History → [Call] → Logs (own tenant + own `call_id` only) | **REUSE** `monitoring/logging/` (Loki), `StructuredLogger` | Support engineer pastes a `call_id` and gets the full structured log trail without a separate Grafana login. |
| **Alerts Dashboard** | Alertmanager (Sprint-027) already routes to `pagerduty-critical` — no in-app view of active/recent alerts. | Admin → Monitoring → Alerts (NEW page) | **REUSE** `monitoring/prometheus` Alertmanager, `incident_response/notifier.py`, `playbooks.py` | Admin sees active alerts + which incident-response playbook auto-fired, without leaving the dashboard. |
| **Rate Limiting Dashboard** | Overlaps API Gateway Dashboard above — called out separately because it's tenant-facing self-service, not just an admin/ops view. | Client → Settings → API Usage (§10.2 above, shared implementation) | **REUSE** `redis_client/rate_limiter.py`, `api_platform/rate_limits.py` | — |
| **Feature Flags / Rollout Control** | `saas_ops/feature_flags.py` + `fleet_rollout.py` (Sprint-026, ring-based) already implement flagged rollout — no UI to toggle a flag without a DB write today. | Admin → Platform Settings → Feature Flags (elevates from §6.17's buried mention to its own page — operationally too important to bury) | **REUSE** `saas_ops.feature_flags`, `fleet_rollout` | Admin ships a risky change behind a flag, rolls out ring-by-ring, rolls back instantly on regression — without an engineer running a script. |

### 10.4 Security, Compliance & Governance

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **Secrets & Credentials Management** | `src/libs/secrets/manager.py` explicitly enforces "no secret ever read from env/config — runtime injection only." A UI must **never** violate that by rendering values. | Admin → Security → Secrets (NEW page, **metadata only** — name, provider, last rotated, expiry — never the value) | **REUSE** `secrets/manager.py`, `rotation.py`, `revocation.py` — read-metadata + trigger-rotation actions only | `PLATFORM_ADMIN` confirms a credential was rotated on schedule, or triggers emergency revocation (`revocation.py`) during an incident — never sees the secret itself, closing the loop this library was built to prevent. |
| **Security Dashboard** | Sprint-028 already produced `docs/security/threat-model.md` (6 STRIDE categories) and a 32-entry `threat-registry.md`, plus tracked PEN-001…009 findings — all currently markdown-only. | Admin → Security (NEW page) | **REUSE** `src/libs/runtime_security/`, `ai_safety/`, `api_security/`; reads `docs/security/threat-model.md`/`threat-registry.md` | Founder/engineering lead reviews open PEN findings and STRIDE coverage during quarterly security review instead of opening markdown files. |
| **Compliance Dashboard** | `compliance_monitoring/` (`alerter.py`, `correlator.py`, `rules.py`) already runs live RBI/DPDP rule checks — no view of current compliance posture exists. | Admin → Compliance (cross-tenant); Client → Settings → Compliance (own tenant RBI/DPDP score only) | **REUSE** `compliance_monitoring.service`, `policy_engine` | Client-side collections manager confirms their campaigns are RBI-clean before a regulator audit; admin spots a tenant trending toward violations. |
| **AI Governance / Explainability** | `ai_governance/law_of_authority.py` + `explainability.py` + `verdict.py` directly implement this project's own "LLM never invents facts" constitution rule — this is Sprint-029's `LawOfAuthorityChecker` made visible, not a new concept. | Admin → Compliance → AI Governance (cross-tenant violation trend); Client → Analytics → AI Governance (own tenant, ties into Sprint-029 founder-validation methodology) | **REUSE** `ai_governance.governance_layer`, `verdict.py` | Directly answers "did the AI invent any facts this week" without re-running the Sprint-029 evaluation suite by hand. |
| **Audit Log Explorer** | Already listed per-module throughout §6 as REUSE `audit_log`; called out once here because it deserves one unified, filterable, cross-cutting view rather than being scattered. | Admin → Audit Logs (cross-tenant, §5.1); Client has no separate page — audit entries surface inline on the entity they describe (e.g., a PTP's own history) | **REUSE** `0010_audit_log.py`, `0017_pii_audit_hash_chain.py`, `AuditRepository` | Compliance officer filters by actor/tenant/date range for a regulator request. |

### 10.5 Cost & Revenue Operations

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **Cost & Metering Dashboard** | `cost_optimizer/` (`gpu_efficiency.py`, `instance_mix.py`, `tracker.py`) and `metering/` (`collector.py`, `aggregator.py`, `enforcer.py`) already compute per-call/per-tenant cost — never surfaced. | Admin → Revenue → Cost & Metering (NEW page) | **REUSE** `cost_optimizer.service`, `metering.service`, `ops_analytics.unit_economics` | Founder checks gross margin per tenant/per call before a pricing decision — currently requires reading `ops_analytics` output by hand. |
| **Incident Response Dashboard** | `incident_response/` (`notifier.py`, `playbooks.py`) already exists as a service with no UI. | Admin → Monitoring → Incidents (NEW page) | **REUSE** `incident_response.service` | On-call sees active incidents and which automated playbook is running, in one place instead of Slack + PagerDuty + logs. |

### 10.6 What this section deliberately does NOT add

To keep this ADR's scope honest: no new database is introduced (Postgres + Redis, already in use, are sufficient for every module above); no new message broker replaces `event_bus`'s existing Redis Streams implementation; no new secrets store replaces `secrets/manager.py`'s provider abstraction. Every module in §10 is **UI + a thin read (occasionally read/write) API on top of infrastructure that Sprints 003–028 already paid for** — the honest new work is enumerated in §4, not duplicated here.

### 10.7 Sequencing impact

§9's sprint plan should absorb §10 as follows (no new sprint numbers needed — these are additive scope inside existing sprints, mostly Admin-side):
- **Sprint-031 (Admin core)** gains: Service Health, Cache, Database Health, Feature Flags, Secrets metadata view.
- **Sprint-034 (Calls & Collections)** gains: HITL Escalation Queue (this is arguably its most important addition — it activates an already-built Volume 4 Ch15 mechanism), WebSocket/Live Session Monitor, Telephony status.
- **Sprint-035 (Reports/Analytics/etc.)** gains: AI Governance, Compliance Dashboard, Cost & Metering, API Gateway Dashboard/Rate Limiting.
- **Sprint-036 (Infrastructure/Monitoring)** gains: GPU Priority Queue Monitor, Model Pool Manager, Event Bus Monitor, DLQ Viewer, Logs Viewer, Alerts Dashboard, Backup & DR, Incident Response Dashboard, Security Dashboard, AI Inference Monitor, Pipeline Execution Monitor.

---

## 11. Trade-offs

**Benefits:**
- Zero duplicated business logic — every dashboard number traces to one authoritative backend call.
- Platform-owner/tenant separation is structural (different actor kind, different JWT claim, different login), not just a UI-hidden role — closes an entire class of privilege-escalation risk before it's built.
- The Pipeline entity is additive to the existing config-inheritance chain, not a competing mechanism.

**Risks:**
- Re-keying `ModelConfig`/`PromptVersion` from campaign-scoped to pipeline-scoped override is a real (if small) migration on tables already in production use — needs a backfill step (existing campaign-level configs become each campaign's implicit "default pipeline").
- This is a large surface area (7 proposed sprints). Scope discipline (`CLAUDE.md`: "build only the requested sprint") will be tested — Sprint-033 (Pipeline, 18 tabs) is the largest single unit of work in the whole roadmap and may itself need splitting.
- No GPU/CPU infrastructure access this session (per `CURRENT_SPRINT.md`) means Sprint-030's Phase 2 (real deployment) is blocked until infra is available — Phase 1 (local, mocked) can proceed regardless.

---

## 12. Approval Required

Per `CLAUDE.md` Architecture Change Policy, this ADR must be reviewed and approved before `implementation/sprints/Sprint-030.md` is written or any code is implemented.

**Sign-off:** _Pending — founder review required._

Once approved: write `Sprint-030.md` per §9 item 1, update `CURRENT_SPRINT.md`, and begin Phase 1 (local/mock) implementation only.
