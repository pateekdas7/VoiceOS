# ADR-005: Full-Stack Frontend + Platform Architecture (Admin Dashboard + Client Dashboard)

**Status:** APPROVED — signed off by founder 2026-07-25, conditional on §16 repository-wide consistency audit (passed, zero redesign-forcing findings)
**Date:** 2026-07-25
**Author:** VoiceOS Engineering (drafted with Claude Code)
**Blocks:** New Sprint(s) — see §11 Sequencing. Sprint-030 may now be written and started against this approved baseline.
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
└── Platform Settings          -- feature flags, global model defaults, entitlement tiers, knowledge base
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
| Backend Service | **REUSE** `src/services/campaign_management/service.py` + **REUSE** `admin_portal/campaign_admin.py` (`CampaignAdminController`) for the approval step below |
| API | `POST/GET/PATCH /campaigns`, `POST /campaigns/{id}/start\|pause\|complete`, `POST /campaigns/{id}/submit-for-review`, `POST /campaigns/{id}/approve` |
| DB | **REUSE** `campaigns` (`0011_campaigns.py`, extended `0020_campaign_lifecycle_contact_center_hitl.py`) |
| Relationships | `campaign.tenant_id → tenants`; **NEW** `campaign` 1—N `pipelines` (currently campaign is a leaf; becomes a container) |
| Validation | **REUSE** `AudienceCriteria`/`RetryPolicy` pydantic validation already in `campaign.py` |
| RBAC | `write:campaigns` permission (**REUSE** `PERM_WRITE_CAMPAIGNS`, held by ADMIN/SUPERVISOR/MANAGER) |
| Business Logic | **REUSE** `lifecycle.py`, `scheduler.py`, `call_dispatcher.py`, `retry_policy.py`, `ab_testing.py` — **plus a REUSE finding from §16.3**: `admin_portal/campaign_admin.py` already implements a campaign approval workflow (`CampaignApprovalDeniedError`, `submit_for_review()`) — a MANAGER-created campaign can require tenant-ADMIN sign-off before going live. Surfaced as a "Submit for Review"/"Approve" action pair on the Campaign detail page. |
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

### 6.3.1 Pipeline Execution Graph (Visualization)

Configuration (§6.3's 18 tabs) tells you *what a pipeline is set up to do*. This subsection adds *what it is actually doing right now* — a visual execution graph, one node per stage:

```
Lead Upload → Validation → Normalization → Deduplication → Qualification → Assignment
   → Queue → Dialer → STT → LLM → TTS → Customer → CRM Update → Analytics
```

Each node renders: **status** (idle/running/degraded/failed), **queue depth**, **avg latency**, **error count**, **retry count**, **last execution timestamp**, **execution duration**, and a **logs** link (deep-links into the Logs Viewer, §10.3, pre-filtered to that stage + `pipeline_id`).

| Node | Backend owner | Notes |
|---|---|---|
| Lead Upload | **REUSE** `crm/importer.py` (`CustomerImporter`) | Already dedupes on `crm_id` at this stage (RI-5) |
| Validation | **REUSE** `AudienceCriteria` pydantic validation + `policy_engine` | |
| Normalization | **NEW** — thin phone/address normalization step | Genuine small gap: no existing service normalizes phone/address formats before qualification. Proposed as a pure function in `crm/`, not a new service — no state, no DB table. |
| Deduplication | **REUSE** `campaign_management/audience.py` | Docstring confirms: "do-not-disturb exclusion, consent validation, and deduplication against" existing audience |
| Qualification | **REUSE** `AudienceCriteria` (DPD range, product type, exclude-PTP-active, exclude-DNC) | |
| Assignment | **REUSE** `campaign_management/audience.py` audience-membership write | |
| Queue | **REUSE** `gpu_scheduler/priority_queue.py` + `event_bus` (Redis Streams) | |
| Dialer | **REUSE** `campaign_management/call_dispatcher.py` | |
| STT | **REUSE** `src/services/stt/` | Per-pipeline adapter from `ModelConfig` (§6.3) |
| LLM | **REUSE** `src/services/llm_runtime/` | |
| TTS | **REUSE** `src/services/tts/` | Voice from `VoiceProfile` (§6.10) |
| Customer | **REUSE** `media_gateway/` (live audio session) | |
| CRM Update | **REUSE** `crm/service.py` | |
| Analytics | **REUSE** `analytics/call_analytics.py`, `campaign_analytics.py` | |

**Backend:** one **NEW** read-only aggregation endpoint, `GET /pipelines/{id}/execution-graph`, that stitches together per-stage metrics already emitted by each owning service's own `metrics.py` module plus `event_bus` stream depth and `OTelTracer` span data (§10, new). It computes nothing new — it is a fan-out read across existing metrics sources, cached briefly (TTLGuard, same pattern as `ModelConfigService`). No new business logic, per this ADR's §2 principle 5.

---

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
| Backend Service | **REUSE** `src/services/user_management/` (`invitation.py`) + **REUSE** `src/services/authz/` (`rbac_engine.py`, `roles.py`) + **REUSE** `admin_portal/user_admin.py` (`UserAdminController` — already the tenant-admin-facing invite/deactivate/SSO-config surface) for tenant side; **NEW** `platform_admin/` for platform staff |
| API | `GET/POST /team`, `POST /team/invite`, `PATCH /team/{id}/role`, `DELETE /team/{id}` |
| DB | **REUSE** `users` (`0003_users.py`) + **REUSE** `org_management` (`Organization`, `OrgScope` — no new table, existing V5 Ch2.4/2.5 model) |
| Relationships | `user.tenant_id → tenants`, `user.role` enum column; **REUSE finding, §16.3**: `admin_portal/user_admin.py`'s `invite()` already threads an `OrgScope(scope_type, scope_id)` — role assignment can be scoped to `TENANT\|ORG\|BUSINESS_UNIT\|BRANCH` (`org_management/hierarchy.py`), not just tenant-wide. `OrgHierarchy.resolve_scope()` governs whether one scope can see a resource in another. |
| Validation | Role must be one of the 5 existing `Role` values; last-ADMIN-cannot-be-removed guard; `scope_id` must resolve to a real org node the inviting user's own scope covers |
| RBAC | Only `PERM_WRITE_USERS` holders (ADMIN/SUPERVISOR) can invite/change roles |
| Business Logic | **REUSE** `UserManagementService`, `RBACEngine` |
| Events | `user.invited`, `user.role_changed` |
| Audit | **REUSE** audit_log — role changes are always audited (Volume 4) |
| Analytics | Seat utilization vs. plan entitlement (**REUSE** `billing.entitlement`) |

### 6.9 Reports / Analytics

| Dimension | Spec |
|---|---|
| Frontend | Client → Reports, Client → Analytics; Admin → Analytics (cross-tenant) |
| Backend Service | **REUSE** `src/services/reporting/` (`exporter.py`, `scheduler.py`) + **REUSE** `src/services/analytics/` (`aggregation.py`, `realtime.py`) + **REUSE** `bi_platform` for Admin cross-tenant rollups + **REUSE** `conversation_quality/dashboard.py` (`QualityDashboard`) for Tone/Empathy/Language-Naturalness trend data — its own docstring names it "for analytics and monitoring" (§16.3 audit finding), the mechanism behind Sprint-029's founder-validation rubric scores |
| API | `GET /reports`, `POST /reports/schedule`, `GET /reports/{id}/export`, `GET /analytics/*`, `GET /analytics/conversation-quality` |
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

### 6.18 Knowledge Base (Admin only — §16.3 audit finding)

| Dimension | Spec |
|---|---|
| Frontend | Admin → Platform Settings → Knowledge Base (NEW page) |
| Backend Service | **REUSE** `src/services/knowledge_retrieval/` (`store.py`, `embedder.py`, `retriever.py`) |
| API | `GET/POST/PATCH /admin/knowledge-base/snippets` |
| DB | **REUSE** existing `VectorStore` persistence (no new migration) |
| Relationships | Snippets are platform-global, not tenant-scoped — RBI guidelines/FAQs/procedures are the same regulatory text for every tenant |
| Validation | Snippets remain evidence-only context per RI-5 — the admin UI must not present them as authoritative facts, matching the existing invariant that the LLM "must not quote amounts from them" |
| RBAC | `PLATFORM_ADMIN` only |
| Business Logic | **REUSE** `DocumentEmbedder`, `RelevanceRetriever` — this page only manages source content, not retrieval logic |
| Events | `knowledge_base.snippet_updated` |
| Audit | **REUSE** audit_log — regulatory content changes must be auditable |
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
- **Auth (corrected during implementation — see erratum below): IdP-only, no password path.** `AuthService.authenticate()` accepts exactly three credential types (mTLS, Bearer JWT, API key) and `JWTValidator` only *validates* RS256 tokens from a public key — it never mints one. The only thing that issues a token is `OIDCProvider.exchange_code()`, trading an authorization code from **the tenant's own external IdP** for a token. No entity in this codebase (`User`, nor the new `PlatformUser`) has ever had a password-hash column. Sign-in is therefore OIDC-only for both actor kinds: Google as the IdP for platform staff, and each tenant configuring its own IdP (Google, Okta, Azure AD, etc.) via the same `OIDCProvider` mechanism — **REUSE**, zero new auth mechanism, zero new dependency. The BFF's `/auth/{provider}/start` + `/auth/{provider}/callback` routes wrap `OIDCProvider.exchange_code()` and set the session cookie; there is no credentials-form code path anywhere in the frontend.

  **Erratum (found during implementation, corrected by founder decision):** this section originally proposed a NextAuth.js "Credentials (id/password)" provider "against REUSE `AuthService`" — that was incorrect; no such credential path exists in `AuthService` to reuse. Building one would have meant inventing first-party password hashing/storage/reset from scratch (new dependency, new attack surface), not reuse. Corrected to IdP-only per the founder's explicit choice between the two options presented at implementation time.
- **BFF API:** NEW `src/services/web_api/` (Starlette, sibling to `api_platform`) exposes the endpoints listed throughout §6. It is a **thin routing/serialization layer** — every handler delegates to an existing service method; no business logic lives in this layer (per Law of Authority: "business logic never lives inside prompts" extends here to "never lives inside the BFF" either).
- **Real-time:** Live Calls / Monitoring use Server-Sent Events or WebSocket, backed by the existing Redis pub/sub used internally for call state (Volume 3) — not a new message bus.

---

## 9. Real-Time System Health & Observability

Every dashboard — Admin and Client alike — carries a persistent health strip, not just the dedicated Infrastructure pages in §10. This section defines the one mechanism both draw from.

**Monitored components:** PostgreSQL, Redis, GPU Scheduler, STT, LLM, TTS, Event Bus, Workers (model pool), Twilio/SIP, CPU Nodes, GPU Nodes.

**Mechanism (fully REUSE, zero new monitoring logic):**
- `src/libs/health/aggregator.py` + `probe.py` already implement a generic multi-target health-check aggregator — each component above gets one `HealthProbe` registered (most already exist implicitly via each service's own `/health`-style check; the aggregator is the piece that was never wired to a UI).
- `service_discovery/registry.py` supplies live instance membership (which GPU/CPU nodes currently exist) so the health strip doesn't hardcode node lists.
- Sprint-027's deployed Prometheus/Grafana/Alertmanager stack is the source of truth for anything the in-process aggregator can't see directly (e.g., Twilio's own status), reused via a thin proxy — no second monitoring pipeline is stood up.
- **§16.3 audit addition:** `src/libs/circuit_breaker/` state feeds directly into badge computation — a tripped breaker on any dependency is treated as an automatic `warning`/`critical` signal for that component, not just probe success/failure.

**One new component:** `GET /system/health` on the Web BFF (§4.1) — calls `HealthAggregator.check_all()` and returns a normalized `{component, status, latency_ms, last_checked}[]`. This is the single endpoint every health badge on every page reads from (polled or SSE-pushed) — one contract, not one per page.

**Badge semantics** (shared component, `<HealthBadge status="healthy|warning|critical" />`):
- **Healthy** — probe succeeded within SLA.
- **Warning** — probe succeeded but a threshold is breached (e.g., GPU queue depth elevated, Redis memory >80%) — sourced from the same thresholds Prometheus alert rules already define (Sprint-027's 34 rules), not a new threshold system.
- **Critical** — probe failed, or an Alertmanager `critical` severity alert is active for that component.

Client Dashboard users see a **reduced set**: only the components that affect their own tenant's calls (STT/LLM/TTS/Twilio/Event-Bus-for-their-tenant), never raw node-level GPU/CPU health (that stays Admin-only, consistent with §5.2's tenant-isolation boundary — a client should never learn about VoiceOS's own infrastructure topology).

---

## 10. End-to-End Traceability

Every KPI on every dashboard must be clickable down to the call that produced it:

```
Dashboard KPI → Campaign → Pipeline → Call → Transcript → STT Output → LLM Output → TTS Output → CRM Update → Audit Logs
```

**Mechanism (fully REUSE):** Sprint-027 already verified, end-to-end, a real OTel trace pushed through the real OTel Collector and retrieved by trace ID with resource attributes intact (`OTelTracer`, `src/libs/observability/tracer.py`, W3C Trace Context propagation). The gap is not tracing — it's that no UI reads a trace by ID. Every call already carries one `trace_id` that spans its STT → LLM → TTS → CRM-write steps as child spans; this ADR's traceability requirement is "expose the `trace_id` VoiceOS already generates as a clickable link at every layer," not "add tracing."

- Each **dashboard KPI** (e.g., "Calls Today: 342") is computed by `analytics`/`bi_platform` from underlying `campaign_results` rows — the aggregation query already carries the `campaign_id`/`pipeline_id` group-by keys needed to drill down one level.
- Each **call row** (Call History, §6.6) carries its `call_id` and `trace_id`.
- **Transcript** — REUSE existing transcript storage (call recording/transcript pipeline, Volume 1).
- **STT / LLM / TTS Output** — individual spans under the same `trace_id`, queried from Jaeger (already deployed, Sprint-027) via a thin `GET /calls/{id}/trace` proxy.
- **CRM Update** — the `customer.updated` event (§6.5) correlated by `call_id`.
- **Audit Logs** — `AuditRepository` entries already carry enough context (`actor`, `resource_id`, `timestamp`) to correlate back to the same `call_id`/`trace_id` chain.
- **§16.3 audit addition:** `conversation_engine`'s `DecisionEnvelope` publish event (RI-4, published before TTS starts) is one more span under the same `trace_id` — the trace timeline should show it explicitly between LLM Output and TTS Output, since it's the auditable "commit point" of the turn.

**One new component:** `GET /calls/{id}/trace` on the Web BFF — fetches the Jaeger trace for that call's `trace_id` and returns it pre-shaped for the frontend timeline widget. No new instrumentation, no new storage — a read proxy onto infrastructure Sprint-027 already stood up and verified.

---

## 11. Sequencing (per CLAUDE.md — one sprint at a time)

This ADR is too large for one sprint. Recommended breakdown for a follow-up `implementation/sprints/Sprint-030.md` (and beyond), **to be written and approved separately** once this ADR itself is approved:

1. **Sprint-030 — Foundation:** `web_api` BFF skeleton, PlatformActor/PlatformRole + `platform_users` migration, Next.js app scaffold, both login pages (credentials + Google OAuth), both dashboard shells with real navigation (empty states behind each nav item), the shared `GET /system/health` endpoint + `<HealthBadge>` component (§9) wired into both shells from day one.
2. **Sprint-031 — Admin core:** Clients CRUD (§6.1), Admin Dashboard KPIs, Billing/Revenue read views.
3. **Sprint-032 — Client core:** Team Members (§6.8), CRM (§6.5), Campaigns list/create (§6.2, using existing flat Campaign — no Pipeline yet).
4. **Sprint-033 — Pipeline entity:** the `pipelines` migration, `pipeline_config` service, the full 18-tab config UI (§6.3), and the Pipeline Execution Graph (§6.3.1, including `GET /pipelines/{id}/execution-graph`) — this is the biggest single sprint given the tab count plus the graph visualization; consider splitting config tabs from the execution graph into two sub-phases if it proves too large.
5. **Sprint-034 — Calls & Collections:** Live Calls, Call History, Collections (§6.6, §6.7), plus `GET /calls/{id}/trace` (§10) so Call History gets end-to-end traceability from day one.
6. **Sprint-035 — Reports/Analytics/Voice Profiles/Notifications/Integrations/Settings:** the remaining REUSE-heavy, lower-risk modules.
7. **Sprint-036 — Infrastructure/Monitoring (Admin):** GPU/CPU fleet views — deferred last since it depends on GPU node access, which is already a known constraint this session (per `CURRENT_SPRINT.md`).

Each sprint follows the existing Phase 1 (local/mock) → Phase 2 (real infrastructure) pattern already used throughout this project.

---

## 12. Additional Production & Operational Modules (identified, not user-specified)

The modules in §6 cover the business-facing product. A real enterprise platform also needs the **operational tooling behind it** — queues, event bus, telephony, secrets, DB/cache health, security/compliance posture, cost. Direct inspection of this repo shows **almost all of the underlying machinery already exists** (Sprints 003–028 built serious reliability/security infrastructure) — it has simply never been surfaced to a screen. The gap here is UI + a handful of thin read APIs, not new backend engines, with a few explicitly-marked exceptions.

Format: **Why** / **Frontend?** / **Depends on** (services, DB, Redis, event bus) / **Who uses it, how**.

### 12.1 Queueing, Workers & Async Execution

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **GPU Priority Queue Monitor** | Operators must see why a call is waiting on inference (CRITICAL/HIGH/NORMAL/LOW starvation) before it becomes a latency incident. | Admin → Infrastructure → GPU Queue (NEW page, REUSE data) | **REUSE** `gpu_scheduler/priority_queue.py`, `vram_ledger.py`, `admission.py`. Reads in-memory scheduler state via a new thin `GET /admin/infra/gpu/queue` proxy. | Platform admin watches queue depth per priority tier live during a load spike; escalates via Incident Response (below) if CRITICAL items wait >budget. |
| **Model Pool / Worker Manager** | STT/LLM/TTS run as pooled model instances per node — admins need to see which are warm, draining, or failed over. | Admin → Infrastructure → Model Pool (NEW page) | **REUSE** `gpu_scheduler/model_pool.py`, `failover.py`, `scheduler.py`. | Confirms a model finished warming before routing traffic to a newly-restored GPU node (mirrors the manual `restore.sh` checks already done by hand each sprint). |
| **Event Bus Monitor** | `src/libs/event_bus/` (Redis Streams: `bus.py`, `consumer.py`, `router.py`, `dedup.py`) is the backbone every domain event (campaign, PTP, tenant, audit) flows through. Silent consumer-group lag is invisible today. | Admin → Infrastructure → Event Bus (NEW page) | **REUSE** `event_bus.bus`/`consumer` internals (consumer-group lag, stream length) exposed via NEW `GET /admin/infra/eventbus`. | Diagnose "why didn't the notification fire" by checking consumer lag before assuming application-layer bug. |
| **Dead Letter Queue (DLQ) Viewer** | `event_bus/dlq.py` already routes poison messages to `dlq:{stream}` after `DEFAULT_MAX_RETRIES=3` — but nothing today lets a human inspect or replay them. This is a real gap: DLQ'd events are silent data-loss risk until surfaced. | Admin → Infrastructure → DLQ (NEW page) | **REUSE** `DLQHandler`; NEW `GET /admin/infra/dlq`, `POST /admin/infra/dlq/{id}/replay`. | Platform admin reviews failed events weekly, replays transient failures, escalates persistent poison messages to engineering. |
| **Retry Queue / Failed Jobs** | Campaign call retries (`retry_policy.py`) and report scheduling (`reporting/scheduler.py`) both have retry semantics but no unified "what's pending retry, what gave up" view. | Client → Campaigns → [Pipeline] → Retry Rules tab already shows config (§6.12); this adds a **read-only execution view** of actual retry state. | **REUSE** `campaign_management/retry_policy.py` + `CampaignResult.outcome_code`. | Client sees "14 calls pending retry, 3 exhausted retries" per pipeline — distinguishes config (rules) from runtime (queue state). |
| **Pipeline Execution Monitor** | With Pipelines introduced (§6.3), operators need a funnel view: queued → dispatched → in-call → completed/failed, per pipeline, in real time. | Client → Campaigns → [Pipeline] → Analytics tab (extends §6.3, NOT a separate top-level page) | **REUSE** `call_dispatcher.py` + `gpu_scheduler` + `event_bus` — aggregation only, NEW read endpoint, no new state machine. | Client watches a new pipeline's first 50 calls funnel through in real time before trusting it at full volume. |

### 12.2 AI Runtime & Telephony Monitoring

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **AI Inference Monitor (STT/LLM/TTS)** | Per-adapter latency/error rate is exactly what ADR-001/ADR-004 were fought over via manual log-reading. Should be a live dashboard, not a one-off investigation. | Admin → Monitoring → AI Inference (NEW page) | **REUSE** `observability/tracer.py` + Sprint-027's Jaeger/Prometheus stack (already deployed) + **REUSE** `performance_engineering/benchmarks.py`/`regression_gate.py` (§16.3 audit addition — the actual mechanism behind ADR-004's TTS budget) as the budget-vs-actual data source — this page is a curated dashboard over existing metrics, not new telemetry. | Confirms TTS TTFA stays ≤750ms (ADR-004 budget) post-deploy without re-running a manual latency script. |
| **WebSocket / Live Session Monitor** | `media_gateway/twilio_ws_entrypoint.py` and `session_gate.py` manage live audio WebSocket sessions — no visibility today into active session count/health. | Admin → Monitoring → Live Sessions (NEW page); surfaces into Client → Live Calls (§6.6) filtered to tenant | **REUSE** `media_gateway.session_gate` session registry via NEW `GET /admin/infra/ws-sessions`. | Confirms a customer's "my calls just dropped" report by checking whether their WS sessions terminated abnormally. |
| **Telephony / SIP-Provider Status** | `media_gateway/adapters/` integrates Twilio (per `twilio_ws_entrypoint.py`) — provider outages must be visible before they're mistaken for a VoiceOS bug. | Admin → Infrastructure → Telephony (NEW page) | **REUSE** `media_gateway/service.py`, `call_recorder.py`; NEW provider health-check endpoint (poll Twilio status API). | First place an on-call engineer checks when call volume drops — rules out "Twilio is down" before debugging the pipeline. |
| **API Gateway Dashboard** | `api_platform` already tracks per-tenant rate limits (`rate_limits.py`) and API-key lifecycle (`api_key_lifecycle.py`) but exposes no view of consumption. | Admin → Infrastructure → API Platform (NEW page); Client → Settings → API Usage (own tenant only) | **REUSE** `api_platform/metrics.py`, `rate_limits.py`, `api_key_lifecycle.py`. | Client checks "why am I getting 429s" against their tier's `TIER_RPS_LIMITS`; admin spots a tenant about to need a tier upgrade. |
| **HITL Escalation Queue** *(notable gap in the original module list — this backend already assumes a UI exists)* | `src/services/hitl/dashboard.py`'s own docstring says "read-only queue-depth/SLA-status view **for the supervisor escalation dashboard**" — this was built Sprint-0xx explicitly anticipating a screen that was never added. | Client → Live Calls → Escalations tab (tenant-scoped); Admin → Monitoring → HITL SLA (cross-tenant rollup) | **REUSE** `hitl/dashboard.py`, `queue.py`, `review_api.py`, `sla_enforcer.py`, `override_logger.py` — fully built, zero new backend. | Supervisor (tenant SUPERVISOR role, `PERM_DECIDE_HITL`) works the escalation queue and records approve/reject/override decisions — this is Volume 4 Ch15's core human-oversight mechanism, currently unreachable by any human. |

### 12.3 Platform Infrastructure Health

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **Service Health Dashboard** | `src/libs/health/aggregator.py` + `probe.py` already implement a generic health-check aggregator; `service_discovery/registry.py` tracks live service instances. Nothing renders it. Underlies §9's health-strip mechanism. | Admin → Infrastructure → Service Health (NEW page) | **REUSE** `health.aggregator`, `service_discovery.registry` | Single-glance "is everything up" view before starting a deploy or investigating an incident. |
| **Service Discovery Monitor** | `service_discovery/registry.py`/`resolver.py`/`client.py` track which service instances are currently registered/resolvable — distinct from *health* (a registered instance can still be unhealthy). No view of registry membership exists today. | Admin → Infrastructure → Service Registry (NEW page) | **REUSE** `service_discovery.registry`, `.resolver` | Confirms a newly-deployed instance actually registered itself before assuming a restart succeeded — catches "process is up but never joined the registry" failures the Service Health page alone wouldn't distinguish. |
| **Cache (Redis) Monitor** | `redis_client/` underlies rate limiting, TTL-guarded config cache, distributed locks — no visibility into hit rate/memory/lock contention today. | Admin → Infrastructure → Cache (NEW page) | **REUSE** `redis_client/client.py`, `ttl_guard.py`, `lock.py`; Redis `INFO` command | Diagnoses "config changes aren't taking effect" (stale TTL-guarded cache) without SSH-ing into the node. |
| **Database Health** | Connection pool saturation and replication lag precede almost every production incident class. | Admin → Infrastructure → Database (NEW page) | **REUSE** `health.aggregator` DB probe; Postgres `pg_stat_activity` | Confirms connection pool isn't the bottleneck before blaming application code during a slowdown. |
| **Backup & Recovery / DR Status** | `0014_snapshots_and_recovery_log.py` + `src/libs/recovery/` (`recovery_manager.py`, `strategies/`) + `infra/dr/` runbooks already exist (Sprint-027's real DR drill hit 4s RTO) — but the result lives only in a markdown report today. | Admin → Infrastructure → Backup & DR (NEW page) | **REUSE** `recovery.recovery_manager`, `infra/dr/` scripts/reports | Confirms last successful snapshot + last DR drill result before a founder/compliance audit, without grepping markdown files. |
| **Logs Viewer** | Sprint-027 deployed the full Loki/FluentBit/Grafana stack with verified `call_id`-keyed search — but only reachable via Grafana's own UI today, not embedded in either dashboard. | Admin → Infrastructure → Logs (NEW page, embeds/proxies Grafana Loki); Client → Call History → [Call] → Logs (own tenant + own `call_id` only) | **REUSE** `monitoring/logging/` (Loki), `StructuredLogger` | Support engineer pastes a `call_id` and gets the full structured log trail without a separate Grafana login. |
| **Alerts Dashboard** | Alertmanager (Sprint-027) already routes to `pagerduty-critical` — no in-app view of active/recent alerts. | Admin → Monitoring → Alerts (NEW page) | **REUSE** `monitoring/prometheus` Alertmanager, `incident_response/notifier.py`, `playbooks.py` | Admin sees active alerts + which incident-response playbook auto-fired, without leaving the dashboard. |
| **Rate Limiting Dashboard** | Overlaps API Gateway Dashboard above — called out separately because it's tenant-facing self-service, not just an admin/ops view. | Client → Settings → API Usage (§12.2 above, shared implementation) | **REUSE** `redis_client/rate_limiter.py`, `api_platform/rate_limits.py` | — |
| **Feature Flags / Rollout Control** | `saas_ops/feature_flags.py` + `fleet_rollout.py` (Sprint-026, ring-based) already implement flagged rollout — no UI to toggle a flag without a DB write today. | Admin → Platform Settings → Feature Flags (elevates from §6.17's buried mention to its own page — operationally too important to bury) | **REUSE** `saas_ops.feature_flags`, `fleet_rollout` | Admin ships a risky change behind a flag, rolls out ring-by-ring, rolls back instantly on regression — without an engineer running a script. |

### 12.4 Security, Compliance & Governance

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **Secrets & Credentials Management** | `src/libs/secrets/manager.py` explicitly enforces "no secret ever read from env/config — runtime injection only." A UI must **never** violate that by rendering values. | Admin → Security → Secrets (NEW page, **metadata only** — name, provider, last rotated, expiry — never the value) | **REUSE** `secrets/manager.py`, `rotation.py`, `revocation.py` — read-metadata + trigger-rotation actions only | `PLATFORM_ADMIN` confirms a credential was rotated on schedule, or triggers emergency revocation (`revocation.py`) during an incident — never sees the secret itself, closing the loop this library was built to prevent. |
| **Security Dashboard** | Sprint-028 already produced `docs/security/threat-model.md` (6 STRIDE categories) and a 32-entry `threat-registry.md`, plus tracked PEN-001…009 findings — all currently markdown-only. | Admin → Security (NEW page) | **REUSE** `src/libs/runtime_security/`, `ai_safety/`, `api_security/`; reads `docs/security/threat-model.md`/`threat-registry.md`; **REUSE** `src/libs/encryption/` (§16.3 audit addition) for one posture line item — algorithm + key-rotation status only, **never** key material, consistent with `secrets/manager.py`'s own "no secret ever leaves runtime injection" invariant | Founder/engineering lead reviews open PEN findings, STRIDE coverage, and encryption posture during quarterly security review instead of opening markdown files. |
| **Compliance Dashboard** | `compliance_monitoring/` (`alerter.py`, `correlator.py`, `rules.py`) already runs live RBI/DPDP rule checks — no view of current compliance posture exists. | Admin → Compliance (cross-tenant); Client → Settings → Compliance (own tenant RBI/DPDP score only) | **REUSE** `compliance_monitoring.service`, `policy_engine` | Client-side collections manager confirms their campaigns are RBI-clean before a regulator audit; admin spots a tenant trending toward violations. |
| **AI Governance / Explainability** | `ai_governance/law_of_authority.py` + `explainability.py` + `verdict.py` directly implement this project's own "LLM never invents facts" constitution rule — this is Sprint-029's `LawOfAuthorityChecker` made visible, not a new concept. | Admin → Compliance → AI Governance (cross-tenant violation trend); Client → Analytics → AI Governance (own tenant, ties into Sprint-029 founder-validation methodology) | **REUSE** `ai_governance.governance_layer`, `verdict.py` | Directly answers "did the AI invent any facts this week" without re-running the Sprint-029 evaluation suite by hand. |
| **Audit Log Explorer** | Already listed per-module throughout §6 as REUSE `audit_log`; called out once here because it deserves one unified, filterable, cross-cutting view rather than being scattered. | Admin → Audit Logs (cross-tenant, §5.1); Client has no separate page — audit entries surface inline on the entity they describe (e.g., a PTP's own history) | **REUSE** `0010_audit_log.py`, `0017_pii_audit_hash_chain.py`, `AuditRepository` | Compliance officer filters by actor/tenant/date range for a regulator request. |

### 12.5 Cost & Revenue Operations

| Module | Why needed | Frontend? | Depends on | Usage |
|---|---|---|---|---|
| **Cost & Metering Dashboard** | `cost_optimizer/` (`gpu_efficiency.py`, `instance_mix.py`, `tracker.py`) and `metering/` (`collector.py`, `aggregator.py`, `enforcer.py`) already compute per-call/per-tenant cost — never surfaced. | Admin → Revenue → Cost & Metering (NEW page) | **REUSE** `cost_optimizer.service`, `metering.service`, `ops_analytics.unit_economics` | Founder checks gross margin per tenant/per call before a pricing decision — currently requires reading `ops_analytics` output by hand. |
| **Incident Response Dashboard** | `incident_response/` (`notifier.py`, `playbooks.py`) already exists as a service with no UI. | Admin → Monitoring → Incidents (NEW page) | **REUSE** `incident_response.service` | On-call sees active incidents and which automated playbook is running, in one place instead of Slack + PagerDuty + logs. |

### 12.6 What this section deliberately does NOT add

To keep this ADR's scope honest: no new database is introduced (Postgres + Redis, already in use, are sufficient for every module above); no new message broker replaces `event_bus`'s existing Redis Streams implementation; no new secrets store replaces `secrets/manager.py`'s provider abstraction. Every module in §12 is **UI + a thin read (occasionally read/write) API on top of infrastructure that Sprints 003–028 already paid for** — the honest new work is enumerated in §4, not duplicated here.

### 12.7 Sequencing impact

§11's sprint plan should absorb §12 as follows (no new sprint numbers needed — these are additive scope inside existing sprints, mostly Admin-side):
- **Sprint-031 (Admin core)** gains: Service Health, Service Discovery Monitor, Cache, Database Health, Feature Flags, Secrets metadata view.
- **Sprint-034 (Calls & Collections)** gains: HITL Escalation Queue (this is arguably its most important addition — it activates an already-built Volume 4 Ch15 mechanism), WebSocket/Live Session Monitor, Telephony status.
- **Sprint-035 (Reports/Analytics/etc.)** gains: AI Governance, Compliance Dashboard, Cost & Metering, API Gateway Dashboard/Rate Limiting.
- **Sprint-036 (Infrastructure/Monitoring)** gains: GPU Priority Queue Monitor, Model Pool Manager, Event Bus Monitor, DLQ Viewer, Logs Viewer, Alerts Dashboard, Backup & DR, Incident Response Dashboard, Security Dashboard, AI Inference Monitor. (Pipeline Execution Monitor itself moved into Sprint-033 — it's a Pipeline-page feature per §6.3.1, not a standalone infra page.)

---

## 13. Backend ↔ Frontend Contract Review

This section is the audit pass: for every frontend page named anywhere in this ADR, confirm the full contract (service, API, DB, Redis, event bus, workers, RBAC, analytics, audit) is either already specified, or explicitly flagged as missing rather than silently assumed. Nothing in this table is new information — it condenses §6/§9/§10/§12 into one scannable pass and surfaces gaps in one place, per the requirement not to implement a missing capability quietly.

### 13.1 Master Contract Table

| Frontend Page | Backend Service(s) | API | DB | Redis | Event Bus | Workers | RBAC | Analytics | Audit | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| Admin → Dashboard | `saas_ops`, `ops_analytics` | NEW BFF route | `tenants`, aggregates | — | — | — | `PLATFORM_ADMIN/SUPPORT` | `ops_analytics` | read-only | COVERED |
| Admin → Clients (§6.1) | `tenant_management` | NEW BFF route | `tenants` | — | `tenant.*` events | — | `PLATFORM_ADMIN` | `saas_ops` | REUSE `audit_log` | COVERED |
| Admin → Revenue / Billing (§6.16) | `billing`, `saas_ops` | NEW BFF route | `billing_subscriptions` | — | `invoice.*`, `payment.*` | — | `PLATFORM_BILLING_OPS` | `saas_ops` MRR/ARR | REUSE | COVERED |
| Admin → Infrastructure (§12.1–12.3) | `gpu_scheduler`, `health`, `service_discovery`, `redis_client` | NEW read proxies (multiple) | — | direct `INFO`/lock reads | queue depth reads | model pool reads | `PLATFORM_ADMIN` | `ops_analytics`, `cost_optimizer` | admin access itself audited | COVERED |
| Admin → Monitoring (§6.6, §9, §12.2, §12.5) | `contact_center`, `event_bus`, `incident_response` | NEW read proxies + SSE/WS for live calls | — | pub/sub for live state | consumer-lag reads, DLQ reads | — | `PLATFORM_ADMIN/SUPPORT` | `call_analytics` | REUSE | COVERED |
| Admin → Analytics (§6.9) | `bi_platform`, `analytics` | NEW BFF route | `bi_facts`, `analytics_daily` | — | — | — | `PLATFORM_ADMIN` | is itself analytics | export events logged | COVERED |
| Admin → Users & Roles (§6.8) | **NEW** `platform_admin` | NEW BFF route | **NEW** `platform_users` | — | — | — | `PLATFORM_ADMIN` | — | REUSE `audit_log` | **GAP → NEW** (component #2, §4) |
| Admin → Audit Logs (§12.4) | `audit` repository | NEW BFF route | `audit_log`, `pii_audit_hash_chain` | — | — | — | `PLATFORM_ADMIN/SUPPORT` | — | is itself audit | COVERED |
| Admin → Platform Settings (§6.17) | `saas_ops.feature_flags`, `ai_config` | NEW BFF route | tenant/global config | — | — | — | `PLATFORM_ADMIN` | — | REUSE | COVERED |
| Admin → Security (§12.4) | `runtime_security`, `ai_safety`, `secrets` (metadata only) | NEW read-only proxies | — | — | — | — | `PLATFORM_ADMIN` | — | rotation/revocation actions audited | COVERED |
| Client → Dashboard | `analytics`, `campaign_management` | NEW BFF route | tenant-scoped aggregates | — | — | — | any tenant role | REUSE | — | COVERED |
| Client → Campaigns (§6.2) | `campaign_management` | NEW BFF route (REUSE service) | `campaigns` | — | lifecycle events | scheduler | `write:campaigns` | `campaign_analytics` | REUSE | COVERED |
| Client → Pipelines (§6.3, §6.3.1) | `ai_config`, `policy_engine`, `campaign_management`, **NEW** `pipeline_config` | NEW BFF routes + **NEW** execution-graph endpoint | **NEW** `pipelines` table + additive `pipeline_id` columns | queue depth per node | dispatcher/queue events | dispatcher, GPU scheduler | `write:campaigns` | per-pipeline breakdown | REUSE | **GAP → NEW** (component #3, §4; execution graph endpoint) |
| Client → Leads (§6.4) | `campaign_management.audience`, `crm.importer` | NEW BFF route | `campaign_audience`, `customers` | — | `lead.*` (new event names, existing bus) | — | `write:campaigns` | funnel via `campaign_analytics` | REUSE | COVERED (Normalization step flagged separately, §6.3.1) |
| Client → Live Calls (§6.6, incl. HITL §12.2) | `contact_center`, `hitl` | NEW BFF route + WS/SSE | Redis call-state | live session state | call state events | — | `monitor:calls`, `PERM_DECIDE_HITL` | `call_analytics` | REUSE | COVERED — **HITL was previously unreachable by any UI; this closes that** |
| Client → Call History (§6.6, §10) | `campaign_management` (`CampaignResult`), **NEW** trace proxy | NEW BFF route + `GET /calls/{id}/trace` | `campaign_results` | — | — | — | `read:transcripts` for AUDITOR | `call_analytics` | REUSE | COVERED (trace endpoint is NEW but zero new instrumentation, §10) |
| Client → Analytics (§6.9, §12.4 AI Governance) | `analytics`, `ai_governance` | NEW BFF route | `analytics_daily` | — | — | — | `view:analytics` | is itself analytics | REUSE | COVERED |
| Client → CRM (§6.5) | `crm` | NEW BFF route (REUSE service) | `customers` | — | `customer.updated` | — | `read:all`/`write:all` | segment breakdown | REUSE `pii_audit_hash_chain` | COVERED |
| Client → Collections (§6.7) | `collections` | NEW BFF route (REUSE service) | `promises_to_pay`, settlement tables | — | `ptp.*`, `settlement.*` | — | tenant role per action | PTP/settlement rate | REUSE | COVERED |
| Client → Reports (§6.9) | `reporting` | NEW BFF route | — | — | — | `reporting.scheduler` | `view:analytics` | — | export events | COVERED |
| Client → Team Members (§6.8) | `user_management`, `authz` | NEW BFF route | `users` | — | `user.*` | — | `write:users` | seat utilization | REUSE | COVERED |
| Client → Settings (§6.17, incl. Voice Profiles §6.10, Integrations §6.13, Notifications §6.14) | `tenant_management`, **NEW** `tts`-extension, `integration_platform`, **NEW** `notifications` | NEW BFF routes | tenant fields, **NEW** `voice_profiles`, **NEW** `notifications` | — | webhook delivery events | webhook delivery worker | tenant ADMIN | delivery success rate, MOS | REUSE | **GAP → NEW** (components #4, #5, §4) |

### 13.2 Explicit Gap Register

Every capability this review found missing, consolidated in one place (no silent implementation):

| Gap | Severity | Owner section |
|---|---|---|
| No browser-session (cookie/JWT) API layer exists — only API-key external API | Blocking (everything depends on it) | §4.1, Web BFF |
| No cross-tenant actor/role exists — Admin Dashboard has no identity model | Blocking | §3, §4.2 |
| No `Pipeline` sub-entity under Campaign | Blocking for §6.3/§6.3.1 | §4.3, §6.3 |
| No `VoiceProfile` entity — TTS speaker is a hardcoded frozenset | Medium (blocks TTS tab + Settings) | §4.4, §6.10 |
| No in-app Notification service | Low (nice-to-have, not blocking core flows) | §4.5, §6.14 |
| No phone/address Normalization step in the lead pipeline | Low (small pure-function gap) | §6.3.1 |
| No UI ever reads `DLQHandler`'s dead-letter streams — silent data-loss risk today, independent of this ADR | Medium — **pre-existing risk, not introduced by this ADR** | §12.1 |
| `HITLDashboard` (`hitl/dashboard.py`) was built with no UI ever wired to it — a Volume 4 Ch15 human-oversight control has been unreachable since it was written | High — **pre-existing gap this ADR closes** | §12.2 |
| Health aggregator (`health/aggregator.py`) and service registry (`service_discovery/`) exist with no UI | Medium — **pre-existing gap this ADR closes** | §9, §12.3 |

No other gaps were found. Every other module in §6/§12 maps to a fully-existing backend service via a thin new API route — the review found **5 blocking-to-medium new components (§4)** plus, as of the §16 repository-wide audit, **4 pre-existing backend capabilities that already existed but were never wired to any UI** (DLQ, HITL, Health aggregator, Conversation Quality dashboard) — the latter are arguably the most valuable finding of this whole review, since they're zero-new-backend-code wins.

---

## 14. Trade-offs

**Benefits:**
- Zero duplicated business logic — every dashboard number traces to one authoritative backend call.
- Platform-owner/tenant separation is structural (different actor kind, different JWT claim, different login), not just a UI-hidden role — closes an entire class of privilege-escalation risk before it's built.
- The Pipeline entity is additive to the existing config-inheritance chain, not a competing mechanism.

**Risks:**
- Re-keying `ModelConfig`/`PromptVersion` from campaign-scoped to pipeline-scoped override is a real (if small) migration on tables already in production use — needs a backfill step (existing campaign-level configs become each campaign's implicit "default pipeline").
- This is a large surface area (7 proposed sprints). Scope discipline (`CLAUDE.md`: "build only the requested sprint") will be tested — Sprint-033 (Pipeline, 18 tabs) is the largest single unit of work in the whole roadmap and may itself need splitting.
- No GPU/CPU infrastructure access this session (per `CURRENT_SPRINT.md`) means Sprint-030's Phase 2 (real deployment) is blocked until infra is available — Phase 1 (local, mocked) can proceed regardless.

---

## 15. Architecture Validation Report

**Scope of this verdict:** whether the *architecture as specified* is enterprise-ready — not whether it is built. As of this ADR, **zero frontend or BFF code exists.** Every "COVERED" below means "has a coherent, traceable design," not "has shipped."

| Category | Verdict | Basis |
|---|---|---|
| **Backend completeness** | READY WITH NOTED GAPS | 20+ existing services cover the full module list (§6, §12) with only 5 net-new components (§4) — a small, well-isolated gap set relative to the scope, not a redesign. |
| **Frontend completeness** | SPEC-COMPLETE, ZERO IMPLEMENTATION | Every nav item (§5) has a page, and every page has a contract (§13.1). Nothing has been coded — this is the entire content of Sprints 030–036. |
| **API completeness** | SPEC-COMPLETE, ZERO IMPLEMENTATION | Every page's endpoint is named in §13.1; the BFF app itself (§4.1) that would host them does not exist yet. |
| **RBAC correctness** | READY | The cross-tenant gap (§1) is closed structurally by the Actor Model (§3) — platform and tenant identities are separate by construction, not by a flag, which is the correct answer to "how do we stop a tenant admin from becoming a platform admin." Contingent on `platform_users`/JWT claim being implemented exactly as specced — no shortcuts (e.g., no "just add ADMIN+tenant=null"). |
| **Database coverage** | READY | 4 proposed migrations (`0028`–`0031`, §7), 100% additive — no existing table is altered destructively, no existing row's meaning changes. The one genuinely delicate piece is re-keying `ModelConfig`/`PromptVersion` to a `pipeline_id` tier (§6.3, §11 trade-offs) — flagged, not hidden. |
| **Redis coverage** | READY | Rate limiting, TTL-guarded config cache, distributed locks, Streams-based event bus — all reused as-is (§12.1, §9). No new Redis usage pattern introduced. |
| **Event Bus coverage** | READY (closes a pre-existing gap) | `event_bus`/`dlq.py` already implements DLQ routing; this ADR is the first plan to ever put a screen in front of it (§12.1, §13.2). |
| **Monitoring coverage** | READY (closes a pre-existing gap) | Sprint-027's full Prometheus/Grafana/Loki/Jaeger/Alertmanager stack is reused wholesale for §9 (health) and §10 (traceability) — no second monitoring pipeline. |
| **Operational tooling coverage** | READY, LARGEST REMAINING SURFACE | §12 catalogs 20+ ops modules across queueing, AI/telephony runtime, infra health, security/compliance, and cost — all REUSE-backed, but represents the single largest implementation surface (Sprint-036) of the whole roadmap. |
| **Security coverage** | READY, CONTINGENT | Secrets page is metadata-only by design (§12.4, never renders values — matches `secrets/manager.py`'s own stated invariant); platform/tenant separation prevents privilege escalation by construction (§3); every write path stays behind existing RBAC/audit. Contingent on the Actor Model being implemented with a genuinely separate credential store, not a shared one with a flag. |

### Overall Verdict

**The architecture is enterprise-ready as a specification.** It reuses the overwhelming majority of an already-substantial backend (Sprints 003–028), introduces the minimum new surface required to make a real Admin/Client frontend possible (5 components, §4), and — as a direct byproduct of the review requested in message 5 and the repository-wide audit in §16 — surfaces four backend capabilities (DLQ visibility, HITL escalation queue, service health aggregation, conversation quality trends) that already existed but were architecturally stranded with no UI. Closing those is pure upside at near-zero new backend cost.

**What stands between this document and "production-ready" is entirely execution, not design:** 7 sprints (§11), disciplined one-sprint-at-a-time delivery per `CLAUDE.md`, and — per §14's flagged risk — care during the `ModelConfig`/`PromptVersion` re-keying migration since it touches tables already carrying production configuration. No remaining architectural gap blocks starting Sprint-030 once this ADR is approved.

---

## 16. Repository-Wide Architectural Consistency Audit

Requested before final approval. Method: every directory under `src/services/` (39) and `src/libs/` (22) — the full backend inventory — checked against this ADR. Four earlier passes (§6, §12, §13) sampled the codebase; this pass verifies **every** directory, not a sample, and specifically hunted for services referenced nowhere in the document.

### 16.1 Full Inventory — services not yet accounted for

Everything already cited in §6/§9/§10/§12 is unchanged and not repeated here. This table covers what a full sweep of `src/services/` and `src/libs/` found **outside** those sections:

| Component | Finding | Disposition |
|---|---|---|
| `src/services/admin_portal/` | **Naming collision, not a gap.** `tenant_admin.py`'s own docstring: *"'list tenants' returns the caller's own tenant record as a single-element list, not a cross-tenant platform-superadmin listing... every query in this codebase is mechanically tenant-scoped, AR-8."* Despite the name, `admin_portal` is **tenant-scoped self-service admin** (V5 Ch13) — the correct backend for a *tenant's own* ADMIN role, not this ADR's Platform Owner Admin Dashboard. Confirms §3's Actor Model is the right call, not a duplicate of something that already exists. | **Correction applied** (§16.3): §6.2, §6.8, §6.16 now cite `admin_portal`'s controllers (`campaign_admin.py`, `user_admin.py`, `billing_admin.py`) as their REUSE backend instead of routing straight to the raw domain services — this layer already exists precisely for these actions. |
| `src/services/admin_portal/campaign_admin.py` | Has `CampaignApprovalDeniedError` + `submit_for_review()` — an existing **campaign approval workflow** (tenant ADMIN approves a MANAGER-submitted campaign) not captured in §6.2. | **Correction applied** — §6.2 gains an approval-workflow note. |
| `src/services/admin_portal/user_admin.py` | `invite()` builds an `OrgScope(scope_type, scope_id)` — confirms Team Members invites are **already org-hierarchy-scope-aware** in the backend, even though §6.8 as drafted only modeled flat tenant-wide roles. | **Correction applied** — see `org_management` finding below. |
| `src/services/org_management/hierarchy.py` | Full `Organization → BusinessUnit → Branch` hierarchy with scope-based RBAC resolution (V5 Ch2.4/2.5, V4 Ch6) — **entirely unrepresented** in the original §6.8. This is real, already-built functionality: a role can be assigned at ORG/BUSINESS_UNIT/BRANCH granularity, not just tenant-wide. | **Correction applied** (§16.3) — §6.8 updated. |
| `src/services/knowledge_retrieval/` | Pre-seeded RAG knowledge base (RBI guidelines, FAQs, settlement/dispute/hardship procedures) feeding the LLM as **evidence-only context, never authoritative** (RI-5). Platform-managed content (regulatory text is the same across tenants), not tenant-editable — no Client page needed, but nothing lets an admin update it when RBI guidance changes. | **Gap → added to Admin Dashboard** (§16.3): Admin → Platform Settings → Knowledge Base (NEW page, REUSE `knowledge_retrieval.store`/`embedder`). |
| `src/services/conversation_quality/` | `dashboard.py`'s own docstring: *"QualityDashboard — quality trend API **for analytics and monitoring**"* (V5 Ch11) — a fourth instance (after HITL, DLQ, health aggregator) of a backend module **built with an explicit UI destination that was never connected.** Directly the mechanism behind Sprint-029's Tone/Empathy/Language-Naturalness rubric scores. | **Correction applied** — added to §6.9 Analytics. |
| `src/services/conversation_engine/` | The CIL orchestrator (`engine.py`) — publishes the `DecisionEnvelope` before TTS (RI-4 commit-before-act) and retrieves knowledge snippets. Correctly backend-only (it's the runtime, not a page) but its `DecisionEnvelope` publish event belongs in the §10 traceability chain. | **Backend-only, justified.** Cross-referenced in §10 as a trace span source. |
| `src/libs/performance_engineering/` | `benchmarks.py`/`regression_gate.py` are the actual mechanism behind ADR-004's TTS latency budget — no citation in this ADR despite §12.2's AI Inference Monitor needing exactly this data (budget vs. actual). | **Correction applied** — cross-referenced in §12.2. |
| `src/services/audio_preprocessing/`, `audio_session_manager/`, `dialogue_manager/`, `playback/`, `vad_endpointing/` | Volume 1 runtime internals — sub-steps of the STT/TTS/LLM pipeline stages already represented in §6.3.1's execution graph (they execute *inside* the STT/LLM/TTS nodes, not as separate nodes). | **Backend-only, justified** — no separate UI; already implicitly covered by the STT/LLM/TTS nodes' status. |
| `src/libs/circuit_breaker/` | Reliability primitive (V3) — a tripped breaker is exactly a "critical" signal. | **Backend-only, justified — but wired in**: added as an input to §9's Health Badge status computation, not a page of its own. |
| `src/libs/invariants/` | Runtime invariant checks (RI-5, RI-7, RI-4, etc.) referenced throughout the codebase's own docstrings — this is the enforcement layer *behind* every Law-of-Authority/AI-Governance verdict. | **Backend-only, justified** — cross-referenced as the mechanism behind §12.4's AI Governance dashboard, not a page of its own. |
| `src/libs/privacy/`, `src/libs/pii/` | DPDP/PII enforcement (V4) — feeds the Compliance Dashboard (§12.4) and CRM's `pii_audit_hash_chain` (§6.5) already cited. | **Backend-only, justified** — already an implicit dependency, now explicitly cross-referenced. |
| `src/libs/encryption/`, `idempotency/`, `concurrency/`, `state/` | Foundational runtime primitives (V3/V4) with no independent business meaning to surface — encryption posture could arguably appear as a Security Dashboard indicator. | **Backend-only, justified.** Encryption posture (algorithm, key-rotation status — never key material) added as one line item on §12.4's Security Dashboard. |
| `src/libs/contracts/`, `src/libs/repositories/` | Data model / data access layers, not engines. | **Foundational, no UI — not applicable**, not a gap. |

### 16.2 Duplicate-Responsibility Check

Explicitly checked for overlapping ownership between similarly-named services:

| Pair | Verdict |
|---|---|
| `admin_portal` vs. this ADR's new `platform_admin` (§4.2) | **No duplication.** `admin_portal` = tenant-scoped self-service (V5 Ch13); `platform_admin` = cross-tenant platform ownership (this ADR, §3). Different actor kind entirely (§16.1 correction). |
| `api_platform` vs. new `web_api` BFF (§4.1) | **No duplication**, boundary already explicit in §4.1: `api_platform` is the external API-key-authenticated SDK surface; `web_api` is the internal browser-session-authenticated surface. Both call the same underlying domain services — no business logic exists in either routing layer. |
| `analytics` vs. `bi_platform` vs. `ops_analytics` | **No duplication**, three distinct scopes: `analytics` = per-tenant call/campaign metrics (§6.9, tenant-facing); `bi_platform` = cross-tenant BI facts/rollups (`0023_bi_facts_schema.py`, Admin-facing, §6.9); `ops_analytics` = technical KPIs/unit economics (§6.1, §12.5, Admin-facing). |
| `cost_optimizer` vs. `metering` | **No duplication** — `cost_optimizer` computes GPU efficiency/instance-mix cost; `metering` collects/aggregates/enforces usage against entitlements. Complementary (§12.5 already cites both). |
| `tenant_management` vs. `org_management` | **No duplication** — `tenant_management` owns the tenant *lifecycle* (create/suspend/delete, §6.1); `org_management` owns the *internal structure* of an existing tenant (org/BU/branch hierarchy, §6.8 correction). |

### 16.3 Corrections Applied to Earlier Sections

As a direct result of this audit, the following edits were made to §6 (not restated here, applied in place):

1. **§6.2 Campaigns** — added the campaign approval workflow (`admin_portal.campaign_admin.CampaignApprovalDeniedError`/`submit_for_review`) as an existing REUSE dependency.
2. **§6.8 Team Members** — added `org_management.hierarchy` (`Organization → BusinessUnit → Branch`, scope-based role assignment) as a REUSE dependency; Team Members now supports scoped invites (`admin_portal.user_admin.invite()` already builds an `OrgScope`), not just flat tenant-wide roles.
3. **§6.9 Reports/Analytics** — added `conversation_quality.dashboard` (QualityDashboard) as a REUSE dependency — a fourth "built for a UI that never arrived" module, joining HITL, DLQ, and the health aggregator.
4. **New Admin page**: Admin → Platform Settings → Knowledge Base, REUSE `knowledge_retrieval/`.
5. **§10 Traceability** — added `conversation_engine`'s `DecisionEnvelope` publish event as a trace span source.
6. **§12.2 AI Inference Monitor** — added `performance_engineering/benchmarks.py`/`regression_gate.py` as the budget-vs-actual data source.
7. **§9 Health Badge** — added `circuit_breaker` state as a health-signal input.
8. **§12.4 Security Dashboard** — added an encryption-posture line item (algorithm/rotation status, never key material, per `src/libs/encryption/` + `secrets/manager.py`'s existing invariant).

No correction required a new migration, a new service, or a new actor/role — every finding was either a misrouting (item 1–2, fixed by citing the correct existing controller) or a wiring gap (items 3–8, UI/cross-reference only).

### 16.4 Orphaned-Component Check

**Result: zero.** Every one of the 39 `src/services/` and 22 `src/libs/` directories is now either (a) represented in the frontend architecture with a §-reference, (b) explicitly justified as backend-only with a named cross-reference to what it feeds, or (c) already in the §13.2 Gap Register. None are unowned.

### 16.5 Frontend-Depends-on-Nonexistent-Backend Check

Re-verified §13.1's Master Contract Table against this fuller inventory: no additional frontend capability was found to reference a backend that doesn't exist. The Gap Register (§13.2) is unchanged — still exactly 5 blocking/medium new components (§4) plus the pre-existing-but-unwired items, now four of them (HITL, DLQ, health aggregator, **+ conversation quality dashboard**, added by this audit).

### 16.6 RBAC Consistency — Platform Actors ↔ Tenant Roles ↔ APIs ↔ UI Nav

| Layer | Values | Consistency check |
|---|---|---|
| Platform Actor (§3) | `PLATFORM_ADMIN`, `PLATFORM_SUPPORT`, `PLATFORM_BILLING_OPS` | Every Admin Dashboard nav item (§5.1) has an explicit role gate in §6/§12/§16 (e.g., Clients=`PLATFORM_ADMIN`, Infrastructure=`PLATFORM_ADMIN`, Billing=`PLATFORM_BILLING_OPS`). No nav item lacks a role. ✅ |
| Tenant Role (existing, `authz/roles.py`) | `ADMIN, SUPERVISOR, MANAGER, AGENT, AUDITOR` | Every Client Dashboard nav item (§5.2) maps to an existing `PERM_*` constant (`write:campaigns`, `monitor:calls`, `view:analytics`, etc.) — confirmed against the actual `ROLE_PERMISSIONS` table in `authz/roles.py`, not invented. ✅ |
| Org Scope (§16.1 correction) | `TENANT, ORG, BUSINESS_UNIT, BRANCH` | Layers *underneath* Tenant Role, not a parallel system — `OrgHierarchy.resolve_scope()` narrows what a role can see within a tenant. Team Members (§6.8) must expose scope selection at invite time; every other page's RBAC gate is unaffected (they check role/permission first, scope narrows the result set, not the gate itself). ✅ Consistent. |
| API (`web_api` BFF, §4.1/§13.1) | Every route in §13.1 | Every route's auth requirement traces to exactly one of the two role systems above — no route is reachable by both a Platform Actor and a Tenant User (§3's structural separation holds at the API layer, not just the UI layer). ✅ |

**No inconsistency found.** The four-layer stack (Platform Actor → Tenant Role → Org Scope → API/UI gate) composes without contradiction.

### 16.7 Event-Driven / Redis / Postgres / Tracing / Analytics Consistency

- **One event bus**: every domain event (`tenant.*`, `campaign.*`, `ptp.*`, `pipeline.*`, `webhook.*`, `user.*`) flows through the single `src/libs/event_bus/` Redis Streams implementation — no module introduces a second bus. Verified across §6/§12/§16.
- **One cache/lock/rate-limit layer**: `redis_client/` is the only Redis client library cited anywhere in this ADR (§9, §12.1, §12.3) — no page or service reaches Redis directly.
- **One Postgres schema**: all 4 proposed migrations (§7) are additive to the existing 27-migration schema; no new database, no schema fork.
- **One tracer**: `OTelTracer` (§10) is the only tracing mechanism cited — the DecisionEnvelope span (§16.3 item 5) and every pipeline-stage span (§6.3.1) live under the same `trace_id` per call, not separate trace trees.
- **Three, not one, analytics scopes** — deliberately not unified, per §16.2: tenant-level (`analytics`), cross-tenant BI (`bi_platform`), technical/ops (`ops_analytics`) — each has a distinct audience and this ADR keeps that boundary rather than collapsing it into one over-general "analytics" service.

**No inconsistency found.**

### 16.8 Scalability Check — does anything here require major redesign later?

- **Org-scoped RBAC was already designed for growth** (§16.1/§16.3): because `admin_portal.user_admin.invite()` already threads an `OrgScope` through, Team Members (§6.8) can ship Sprint-032 with tenant-wide-only UI and add Branch/BU-level assignment in a later sprint as an **additive UI feature**, not a backend redesign — the scope model is already there.
- **Pipeline `pipeline_id` re-keying remains the one flagged delicate spot** (§6.3, §14) — a real migration touching production-configured tables, already called out, not newly discovered.
- **Platform Actor model is deliberately structural, not a flag** (§3) — adding a fourth `PlatformRole` later (e.g., `PLATFORM_SECURITY_OFFICER` for the Security Dashboard, §12.4) is a one-line enum addition, not a redesign, following the exact precedent `Role`/`ROLE_PERMISSIONS` already set.
- **Three-scope analytics boundary (§16.7)** means a future fourth consumer (e.g., a partner-facing analytics API) has an obvious home (extend `bi_platform`) instead of forcing a retrofit of `analytics`.

**No redesign-forcing issue found.** The one known migration risk (`pipeline_id`) was already disclosed in §14, not newly discovered by this audit.

### 16.9 Audit Verdict

**No architectural inconsistencies were found that require a different design.** Eight wiring/citation corrections were applied (§16.3) — all additive documentation/cross-reference fixes, not redesigns, and all *strengthen* the "reuse existing backend" principle this ADR is built on (four separate modules — HITL, DLQ, health aggregator, conversation quality — turned out to already be built with a UI destination in mind and simply never connected). Zero orphaned components, zero duplicate responsibilities, zero frontend pages depending on non-existent backends beyond the already-disclosed Gap Register, RBAC is internally consistent across all four layers, and the one identified scalability risk (`pipeline_id` migration) was already flagged, not newly discovered.

**This ADR is confirmed as the architectural baseline for Sprint-030.**

---

## 17. Approval Required

Per `CLAUDE.md` Architecture Change Policy, this ADR must be reviewed and approved before `implementation/sprints/Sprint-030.md` is written or any code is implemented.

**Sign-off:** **APPROVED** — Founder, 2026-07-25.

Approval was conditional on the §16 repository-wide consistency audit finding no architectural inconsistencies requiring redesign. The audit (§16.9) confirmed this: eight wiring/citation corrections were applied (§16.3) — all additive documentation fixes, not redesigns — and zero orphaned components, zero duplicate responsibilities, and zero unsupported frontend dependencies were found. **This ADR is the approved architectural baseline for Sprint-030 onward.**

Per the founder's direction: implementation now follows this ADR as written — architecture is not to be redesigned mid-sprint. Any future change to the decisions in §3 (Actor Model), §4 (New Components), §6 (Module Architecture), or §7 (Database) requires a new ADR, not an in-sprint deviation.

Next step: write `Sprint-030.md` per §11 item 1, update `CURRENT_SPRINT.md`, and begin Phase 1 (local/mock) implementation only.
