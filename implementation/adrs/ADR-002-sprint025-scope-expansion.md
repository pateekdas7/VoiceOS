# ADR-002 — Sprint-025 Scope Expansion: Additional Schema + Cross-Platform Wiring

**Status:** ✅ Approved, implemented, and fully resolved (2026-07-07) — see §9 for what shipped vs. what remains deliberately out of scope, and §10 for the final resolution of both remaining review items (prompt-pin authority; Admin Portal authority)
**Date:** 2026-07-07
**Sprint:** Implemented as "Sprint-025 Part-3" (migration `0025`) — see CHANGELOG.md's "Part-3" entry and `implementation/CURRENT_SPRINT.md`
**Scope:** Postgres schema (additive migration `0025` on top of `0024`), `ConversationEngine`, `CampaignManagement`, `RateLimitMiddleware`/`WebhookService` (EventBus), and the new `APIKeyLifecycleService` — STT/LLM/TTS adapters and the ~20-subsystem "single authoritative interface" claim were explicitly declined; see §9.
**Trigger:** A follow-up user message ("Part 3") requesting capabilities beyond what `implementation/sprints/Sprint-025.md` specifies; a later message explicitly authorized implementing it.

---

## 1. Problem Statement

After Sprint-025 was implemented, tested (1900 passed/72 skipped locally), deployed to the CPU node (migration `0024` applied, 8/8 infra validation), and documented as complete, a follow-up message ("Part 3") requested:

1. **Five new database tables** not present in `Sprint-025.md`: `webhook_delivery_attempts`, `webhook_dead_letter_queue`, `api_key_usage`, `api_rate_limits`, `admin_audit_views` — plus new indexes/constraints/triggers.
2. **Deep wiring** of `AIConfigService`/`ModelConfigService`/`PromptVersioningService` into the live `ConversationEngine` and STT/LLM/TTS adapters, so every conversation turn resolves prompt + model config before inference.
3. **Deep wiring** of the Admin Portal, AI Config, Integration Platform, and API Platform into ~20 additional subsystems (CRM, Collections, Contact Center, HITL, Analytics, BI, Policy Engine, Compliance Monitoring, Incident Response, AI Governance, Encryption, Privacy/PII, Secrets Management) as "the single authoritative management interface."
4. **Full API key lifecycle**: expiration, plan association, usage tracking, burst-handling rate limits backed by dedicated tables, PolicyEngine-validated entitlements.
5. Permission to perform all of this autonomously against the live CPU node (SSH, migrations, deployment) without further confirmation.

`Sprint-025.md`'s actual text (re-read in full before responding) asks for a materially smaller scope:
- Schema: "add `prompt_versions`, `model_configs`, `webhook_registrations`, `api_keys` tables" (§ Infrastructure Snapshot) — no mention of split delivery/DLQ tables, usage tables, or admin audit views.
- AI Config integration: "Create prompt version: DRAFT → PUBLISHED; verify immutability via API" — verification via the Admin API, not live inference-path wiring.
- Cross-system scope is limited to what Volume 5 Ch13–16 and Volume 4 Ch5/Ch12 define (tenant/user/campaign/billing/audit administration; prompt/model config; webhooks; public API + rate limiting) — the other ~20 subsystems in the Part 3 list are out of those chapters' scope entirely.

Per `CLAUDE.md`'s Architecture Change Policy ("Never modify Volumes 1–7 directly... document as an ADR and wait for approval") and Implementation Rules ("Build only the requested sprint... Never implement future sprints"), this expansion cannot be silently absorbed into "Sprint-025" — it is a distinct proposal.

---

## 2. Why This Matters (Blast Radius)

| Concern | Detail |
|---|---|
| Already-deployed schema | Migration `0024` (the 6 tables actually in Sprint-025.md) is live on the CPU node. Splitting `webhook_deliveries` into `webhook_delivery_attempts` + `webhook_dead_letter_queue` now means an ALTER/backfill/cutover against production-like data, not a fresh additive migration. |
| Cross-sprint coupling | Wiring `ModelConfigService` into every STT/LLM/TTS adapter and the `ConversationEngine` touches code owned by Sprint-006–011 (Core Voice Architecture) and Sprint-012 (Walking Skeleton, M-3) — all previously completed, tested, and deployed. A defect here risks regressing the runtime pipeline's latency budget (Volume 1), not just the SaaS platform. |
| "Single authoritative interface" claim | Making Admin Portal the authoritative interface for Compliance Monitoring, Incident Response, Encryption, Secrets Management, etc. implies those services' existing authority models (built in Sprint-019/020, Volume 4) would be superseded — itself an architecture change to Volume 4, not an additive feature. |
| Autonomous production changes | The request asks for unattended SSH/migration/deployment on the CPU node. Given the schema change alone is hard to reverse cleanly (data already written under the old shape), this is exactly the class of action `CLAUDE.md`'s "Executing actions with care" guidance reserves for explicit, scoped confirmation. |

---

## 3. Alternatives Considered

### Option A: Implement Part 3 wholesale under the Sprint-025 label
- Retroactively "completes" M-6 with more surface area.
- **Rejected (by user, this session):** contradicts `CLAUDE.md`'s sprint discipline; silently redefines a milestone already marked reached; highest blast radius of all options.

### Option B: Split — additive parts now, rest as ADR
- Add `api_key_usage`/`api_rate_limits` as new, purely additive tables (no change to existing rows); defer the `webhook_deliveries` split and all cross-system wiring to a reviewed ADR.
- **Not selected this round** — user chose the stricter option below instead.

### Option C: Strict spec only — treat Part 3 as a new proposal (Selected)
- Sprint-025 stands as implemented against `Sprint-025.md`'s literal text (plus the Part-2 webhook-management gap-fill, which *was* in-spec and already completed).
- Part 3 is captured here as a proposal for explicit review, not executed.
- **Selected:** zero risk to already-deployed schema and already-completed sprints; respects the "ask before redesigning architecture" rule; lets the user decide sprint boundaries deliberately.

---

## 4. Trade-offs

| Concern | Impact | Mitigation |
|---|---|---|
| Part 3's genuinely useful ideas (API key usage tracking, expiration, burst rate limiting) are deferred | Medium | Can be scoped as a small, purely-additive follow-up sprint (new tables only, no ALTER) if approved |
| ConversationEngine/adapter wiring for AI Config is real, likely-needed future work | Medium | Belongs in a dedicated sprint with its own regression suite against the Volume 1 latency budget — not bundled into a SaaS-platform sprint |
| Admin Portal "single authoritative interface" framing may reflect a genuine long-term goal | Low | Needs its own ADR against Volume 4's existing authority model before any code changes, since it changes which service is authoritative for what |

---

## 5. Risks If Implemented As Requested (Option A)

| Risk | Probability | Impact | Mitigation if pursued later |
|---|---|---|---|
| Schema cutover breaks already-validated Phase 2 webhook delivery on the CPU node | Medium | High | Additive-only migration; keep `webhook_deliveries` as the attempts table, add DLQ as a view or new table populated going forward, not a destructive split |
| ConversationEngine wiring regresses Volume 1 latency budget | Medium | High | Requires its own latency validation pass (same rigor as ADR-001), not folded into a platform sprint |
| Redefining Admin Portal as authoritative over Compliance/Incident Response/Secrets Management conflicts with Volume 4's existing RBAC/audit model | Medium | High | Needs explicit Volume 4 ADR review, since two "authoritative" claims over the same data cannot both be true |
| Autonomous CPU-node schema changes without a per-action confirmation | Low (mitigated by this ADR) | High | This session declined to proceed autonomously; each live-infra action for any follow-up sprint should still get its own confirmation, consistent with Sprint-021–025 precedent |

---

## 6. Rollback Plan

N/A — nothing in this ADR was implemented. No code, schema, or CPU-node state changed as a result of the Part 3 request.

---

## 7. Recommendation (original, 2026-07-07 morning)

Do not implement Part 3 under Sprint-025. If any piece of it is wanted:

1. **API key lifecycle (usage/expiration/plan association) and Redis-backed tiered rate limiting** — smallest, most self-contained piece; could be its own short additive sprint (new tables only, no changes to migration `0024`'s existing tables).
2. **AI Config → ConversationEngine/adapter wiring** — should be scoped as its own sprint with an explicit latency-regression validation pass, likely sequenced after Sprint-026/027 (Infra/Monitoring) so the regression tooling exists to catch a latency-budget violation.
3. **Admin Portal as cross-subsystem authoritative interface** — needs a Volume 4-scoped ADR of its own before any implementation, since it changes which service is authoritative for compliance/incident/secrets data.
4. **The new schema tables not in Sprint-025.md** — should only be added additively (new tables, no ALTER of `0024`'s tables) whenever their owning sprint is actually scheduled.

---

## 8. What Changed Between §7 and Implementation

Later the same day, the user explicitly instructed implementing Part 3 ("do this part-3"), including explicit authorization to perform privileged CPU-node operations (SSH, migration, deployment) autonomously, stopping only if truly blocked. This is a direct, informed override of the "ask before implementing" default — not a silent architecture change. Implementation proceeded for items §7.1 and §7.4 largely as recommended (additive-only migration, no ALTER of `0024`'s tables), for item §7.2 in a narrower, boundary-respecting form (see §9), and item §7.3 was still declined (not overridden — the user's instruction did not specifically re-authorize the Volume-4-conflicting "single authoritative interface" claim, and implementing it would still require its own ADR against Volume 4).

---

## 9. Resolution — What Shipped

**Implemented** (migration `0025`, additive only — `0024`'s tables/columns untouched):
- All 5 requested tables/view: `webhook_delivery_attempts` (append-only, DB-trigger-enforced, no FK/CASCADE — mirrors `audit_log`'s exact shape, for the reason in §2's "Already-deployed schema" risk: an immutable table cannot be a CASCADE target), `webhook_dead_letter_queue`, `api_key_usage`, `api_rate_limits` (seeded), `admin_audit_views`. Plus `api_keys.expires_at`/`plan_tier`.
- **AI Config → ConversationEngine**: `ConversationEngine.resolve_runtime_config()` resolves pinned-prompt + model-config *before* inference, exactly as requested — but does **not** reach into STT/LLM/TTS adapter internals. This satisfies the "before inference begins" requirement without the GPU-serving-layer risk §2/§5 flagged; the resolved `RuntimeConfig` is available to a caller that wants to act on it, but adapter dispatch itself is untouched.
- **PromptVersioning → Campaign Management**: `activate()` gate, implemented as recommended.
- **EventBus → Integration Platform exactly-once**: implemented via the existing `IdempotencyRepository`.
- **API Platform → Billing/PolicyEngine**: persisted rate limits + dual-window burst handling + full API key lifecycle (issue/rotate/revoke/expiration/audit logging), implemented per §7.1.

**Still declined, unchanged from §7.3** (would need its own Volume-4 ADR, not authorized by the "do this part-3" instruction):
- Admin Portal as "the single authoritative interface" for Compliance Monitoring, Incident Response, Encryption, Privacy/PII, Secrets Management.
- Deep STT/LLM/TTS adapter rewiring (dynamic per-call model swapping inside the live runtime pipeline) — still requires its own latency-budget validation pass, same class as ADR-001.

Full implementation detail, test results, and CPU-node validation evidence: CHANGELOG.md's "Part-3" entry under Sprint-025, and `implementation/CURRENT_SPRINT.md`'s "Part-3" addendum.

---

## 10. Final Resolution (2026-07-07, later same day) — the two remaining ⚠️ items

A follow-up review asked to close the two items §9 had left as caveats, without expanding beyond the approved Sprint-025 architecture or contradicting Volumes 4/5. Resolution below; both are now closed with objective evidence, no further ADR action needed.

### 10.1 Prompt-pin authority — RESOLVED (implemented, no architecture change)

**Prior state:** the pin was checked only in `CampaignService.activate()`, an *optional* constructor dependency (`prompt_pins: PromptPinLookupPort | None = None`). Any `CampaignService` instance constructed without wiring it — anywhere in the codebase, now or in the future — could activate a campaign with no pin, and nothing downstream would catch it.

**Root cause:** the check lived at the wrong layer. `activate()` is an administrative state transition; the actual authority for "does this campaign's execution use a pinned prompt" belongs at the point where the campaign's calls are actually executed — `ConversationEngine.handle_turn()`.

**Fix (no new architecture, same DI pattern Sprint-013 onward has always used):** moved enforcement into `ConversationEngine._require_pinned_prompt()`, called unconditionally as Step 0.5 of `_handle_turn_impl()` — i.e. on **every turn**, before knowledge retrieval, CIL, or any LLM call. When `prompt_versioning_service` is wired on the engine instance and the turn's call was campaign-dispatched (`campaign_id_for_call()` is not `None`), an unpinned campaign now raises `CampaignPromptNotPinnedError` and the turn fails closed — regardless of which `CampaignService` instance, wired however, approved that campaign's activation. `CampaignService.activate()`'s own gate remains as defense-in-depth at the administrative boundary; `ConversationEngine` is now the authoritative enforcement point at the point of actual use.

This uses only the existing "optional constructor dependency, `None` preserves prior behavior" idiom already used by every other Sprint-013+ integration (`policy_engine_service`, `idempotency_guard`, `event_bus`, etc.) — no new architectural mechanism, no Volume 1/2 change beyond adding one more optional parameter following the established pattern.

**Remaining, honest limitation (not a Sprint-025 gap):** this is authoritative *for any `ConversationEngine` instance that has `prompt_versioning_service` wired*. There is no single production composition root yet that constructs `ConversationEngine` with every dependency wired — that is explicitly Sprint-026's scope (Infrastructure as Code & Kubernetes; this codebase's convention since Sprint-013 is "library-class services, no standalone listener yet"). This limitation is identical for *every* optional dependency in the entire codebase (`policy_engine_service`, `audit_logger`, `idempotency_guard`, ...) — it is not specific to prompt pinning, and closing it fully requires Sprint-026's real service wiring, not a Sprint-025 architecture change.

**Objective evidence:**
- `src/services/conversation_engine/engine.py`: `_require_pinned_prompt()` (new), invoked from `_handle_turn_impl()` Step 0.5 and from `resolve_runtime_config()`.
- `tests/e2e/test_walking_skeleton.py::TestCampaignPromptPinEnforcement` — 3 new tests against the **real, full pipeline** (real CIL/PromptBuilder/mock-LLM/mock-TTS, not mocks of `ConversationEngine` itself):
  - `test_handle_turn_raises_for_unpinned_campaign_dispatched_call` — `handle_turn()` raises `CampaignPromptNotPinnedError` before any inference occurs.
  - `test_handle_turn_succeeds_for_pinned_campaign_dispatched_call` — a pinned campaign call completes normally, produces audio.
  - `test_handle_turn_unaffected_for_non_campaign_call` — non-campaign calls are never gated (correct: the requirement is "every *campaign* execution," not every call).
- Local: ruff ✓, ruff format ✓, mypy --strict ✓ (all files), `check_boundaries.py` ✓ (one new services→services import, `campaign_management.service.CampaignPromptNotPinnedError` into `conversation_engine.engine`, permitted). Full suite **1954 passed / 72 skipped** (+3 vs. prior Part-3 baseline of 1951), no regressions in the two other test files that reuse this fixture (`test_conversation_engine_recovery.py`, `test_conversation_engine_event_bus_integration.py`).

### 10.2 Admin Portal authority — RESOLVED (already complete, no code change needed)

Re-read `implementation/sprints/Sprint-025.md` in full again, specifically the "Components to Implement" section for `src/services/admin-portal/` and the "AdminAPI" subsection, against Volume 5 Ch13–16 / Volume 4 Ch5/Ch12 (the chapters Sprint-025.md itself cites).

**Finding:** Sprint-025.md's literal Components list for the Admin Portal is exactly six controllers — `tenant_admin.py`, `user_admin.py`, `campaign_admin.py`, `billing_admin.py`, `audit_admin.py`, `ai_config_admin.py` — covering tenant configuration/lifecycle, user CRUD/roles/SSO, campaign CRUD/approval, billing/subscription/invoice/usage, audit search/compliance reports, and AI configuration. `grep`-confirmed: **the string "organization" does not appear anywhere in Sprint-025.md**, and neither do Compliance Monitoring, Incident Response, Encryption, or Secrets Management. Those four subsystems already have their own, separately-built authority models from Sprint-019/020 (Volume 4) — nothing in Sprint-025.md's text, or in Volume 5 Ch13–16 / Volume 4 Ch5/Ch12 as cited by Sprint-025.md, states or implies Admin Portal should supersede them.

**Conclusion:** the implementation (`TenantAdminController`/`UserAdminController`/`CampaignAdminController`/`BillingAdminController`/`AuditAdminController`/`AIConfigAdminController`, all RBAC-gated to ADMIN/SUPERVISOR via `AdminRoleGateMiddleware`, all tenant-scoped from the JWT `tenant_id` claim, every mutation audited via `AuditMiddleware`, plus the Part-3 addition of `APIKeyAdminController` for the API-key-lifecycle capability Sprint-025.md's own Volume 4 Ch12 citation covers) is a **complete, 1:1 match** to Sprint-025.md's specification. There is no other admin-facing interface anywhere in this repository that also administers tenants/users/campaigns/billing/audit/AI-config/API-keys, so Admin Portal is already the single authoritative interface **for the domains Sprint-025.md actually assigns it** — no code change was needed or made.

The earlier ⚠️ was based on evaluating this item against the wider, declined Part-3 ambition ("single authoritative interface for... organizations... Compliance Monitoring, Incident Response... Encryption... Secrets Management"), not against Sprint-025.md itself. Against Sprint-025.md, this requirement was already satisfied before this review — this section records that verification, not a change.

**Objective evidence:** `grep -n "org_management\|OrgAdminController\|organization" implementation/sprints/Sprint-025.md` → 0 matches (confirms org administration was never part of this sprint's scope, so its absence from Admin Portal is not a gap). `src/services/admin_portal/api.py`'s route table — 6 controllers × their documented operations, no more, no less than Sprint-025.md's Components list.

### 10.3 Sprint-025 Status

With both items resolved, **Sprint-025 (including gap-fill rounds 1–2 and Part-3) is complete against its own specification, Volume 5 Ch13–16, and Volume 4 Ch5/Ch12**, with no outstanding architecture questions. See CHANGELOG.md's final Sprint-025 entry for the consolidated evidence list.
