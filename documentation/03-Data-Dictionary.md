# VoiceOS v2 — Documentation Suite

## Document 3 — Data Dictionary

**Type:** Canonical entity/field reference (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** Architecture Board (contracts) + domain owners
**Authority:** Volumes 1–7 are immutable + canonical. This dictionary **records** the entities + fields already defined; it does not introduce or redesign schemas. Field detail is reproduced from the source volume; the volume is authoritative. Citations: `V<n> Ch<c>`.

> **Per-field columns:** Name · Type · Constraints · Nullable · Default · Validation · Lifecycle · Authoritative Source · Relationships · Indexes. Conventions (from V6 Ch7 / DM rules): `snake_case` columns; PKs are ULID/UUID; money is `(amount_minor BIGINT, currency CHAR(3))` never float; timestamps are `timestamptz` UTC; every tenant-scoped entity has an indexed non-null `tenant_id` (AR-8 / DM-2). "Authoritative Source" names the system of record that owns the truth of the field (Law of Authority, V4 Ch3).

> **Legend:** *Lifecycle* = create/update/erase behavior. *Auth. Source* = owning store/context. Tenant-scoped entities carry `tenant_id` (omitted from each table for brevity but **mandatory + indexed** on all of them).

---

# Part A — Business entities (Volume 5, system of record)

## A.1 Customer  *(Auth. Source: CRM — V5 Ch4)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| customer_id | ULID | PK | no | gen | ULID | create→erase(crypto-shred) | → loans, calls | PK; (tenant_id) |
| name | text(enc) | — | no | — | non-empty | mutable | — | — |
| phone | text(token) | E.164 | no | — | phone fmt | mutable | — | (tenant_id, phone) |
| email | text(token) | — | yes | null | email fmt | mutable | — | — |
| language | enum | hi/en/hinglish | no | hi | in set | mutable | — | — |
| consent_state | enum | per DPDP | no | — | V4 Ch2 | mutable+audited | → consent log | — |
| pii_fields | encrypted | per-tenant key | — | — | V4 Ch8/10 | crypto-shred on erase | — | — |

*Notes:* PII encrypted/tokenized (V4 Ch8/10); erasure = crypto-shred + tombstone (V4 Ch9).

## A.2 Loan  *(Auth. Source: Collections — V5 Ch5)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| loan_id | ULID | PK | no | gen | ULID | create→close | → customer, EMIs, PTPs | PK; (tenant_id) |
| customer_id | ULID | FK | no | — | exists, same tenant (DM-3) | — | → Customer | (tenant_id, customer_id) |
| principal_minor | bigint | ≥0 | no | — | money | immutable | — | — |
| outstanding_minor | bigint | ≥0 | no | — | money | mutable (authoritative) | — | — |
| currency | char(3) | ISO 4217 | no | INR | in set | immutable | — | — |
| dpd | int | ≥0 | no | 0 | computed | mutable (authoritative) | — | (tenant_id, dpd) |
| dpd_bucket | enum | bucket set | no | — | from dpd | derived | → campaigns | (tenant_id, dpd_bucket) |
| status | enum | active/closed/… | no | active | in set | mutable | — | — |

*Notes:* `outstanding_minor`/`dpd` are **authoritative** facts the Law of Authority reads (never inferred by the model).

## A.3 EMI (Installment)  *(Auth. Source: Collections — V5 Ch5)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| emi_id | ULID | PK | no | gen | ULID | create→paid/overdue | → Loan | PK; (tenant_id) |
| loan_id | ULID | FK | no | — | exists, same tenant | — | → Loan | (tenant_id, loan_id) |
| due_date | date | — | no | — | valid date | immutable | — | (tenant_id, due_date) |
| amount_minor | bigint | ≥0 | no | — | money | immutable | — | — |
| paid | bool | — | no | false | — | mutable | — | — |
| paid_date | date | — | yes | null | ≥ due or null | mutable | — | — |

## A.4 PromiseToPay (PTP)  *(Auth. Source: Collections — V5 Ch5)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| ptp_id | ULID | PK | no | gen | ULID | create→kept/broken | → Loan, Call | PK; (tenant_id) |
| loan_id | ULID | FK | no | — | same tenant | — | → Loan | (tenant_id, loan_id) |
| call_id | ULID | FK | yes | null | same tenant | — | → Call | — |
| amount_minor | bigint | >0 | no | — | money; within envelope | immutable | — | — |
| promised_date | date | future | no | — | > today | immutable | — | (tenant_id, promised_date) |
| status | enum | open/kept/broken | no | open | in set | mutable | — | (tenant_id, status) |
| idempotency_key | text | unique/tenant | no | — | AR-15 | immutable | — | unique(tenant_id, idempotency_key) |

*Notes:* an **authoritative effect** — captured idempotently (AR-15), emitted commit-before-act (RI-4), audited (V4 Ch11).

## A.5 Settlement  *(Auth. Source: Collections — V5 Ch5)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| settlement_id | ULID | PK | no | gen | ULID | create→fulfilled | → Loan | PK; (tenant_id) |
| loan_id | ULID | FK | no | — | same tenant | — | → Loan | (tenant_id, loan_id) |
| amount_minor | bigint | >0 | no | — | within Negotiation Envelope (V2 Ch5) | immutable | — | — |
| approved_by | UserId | FK | maybe | — | approval (V4 Ch15) if over threshold | — | → User | — |
| status | enum | proposed/approved/fulfilled | no | proposed | in set | mutable+audited | — | — |
| idempotency_key | text | unique/tenant | no | — | AR-15 | immutable | — | unique(tenant_id, key) |

*Notes:* bounded by the **Negotiation `Envelope`** (V2 Ch5); over-threshold requires human approval (V4 Ch15).

## A.6 Campaign  *(Auth. Source: Campaigns — V5 Ch6)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| campaign_id | ULID | PK | no | gen | ULID | create→run→complete | → calls | PK; (tenant_id) |
| name | text | — | no | — | non-empty | mutable | — | — |
| segment | query | DPD/bucket filter | no | — | valid filter | mutable | → loans | — |
| window | timerange | RBI-compliant | no | — | V4 Ch2 | mutable | — | — |
| voice_profile | VoiceProfileId | FK | no | — | catalog (V5 Ch14) | mutable | → AI config | — |
| status | enum | draft/active/paused/complete | no | draft | in set | mutable | — | (tenant_id, status) |

## A.7 Call  *(Auth. Source: Contact Center/Collections — V5 Ch5/7; runtime V1)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| call_id | ULID | PK | no | gen | ULID | start→end | → customer, transcript, PTPs | PK; (tenant_id) |
| customer_id | ULID | FK | no | — | same tenant | — | → Customer | (tenant_id, customer_id) |
| campaign_id | ULID | FK | yes | null | same tenant | — | → Campaign | — |
| correlation_id | text | — | no | gen | propagated (EV-8) | immutable | → events, traces | (correlation_id) |
| started_at / ended_at | timestamptz | UTC | no/yes | — | — | set on start/end | — | (tenant_id, started_at) |
| outcome | enum | RPC/PTP/refused/… | yes | null | in set | set on end | → analytics | (tenant_id, outcome) |
| first_audio_ms | int | ≥0 | yes | — | budget (V1 Ch23) | measured | — | — |

## A.8 Transcript  *(Auth. Source: Collections/Contact Center — V5 Ch5/7; runtime V1 Ch9)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| transcript_id | ULID | PK | no | gen | ULID | create | → Call | PK; (tenant_id) |
| call_id | ULID | FK | no | — | same tenant | — | → Call | (tenant_id, call_id) |
| turns | jsonb | ordered | no | [] | per-turn schema | append-only | → DecisionEnvelopes | — |
| pii_redacted | bool | — | no | true | V4 Ch10 | — | — | — |
| retention_until | date | — | no | — | policy (V4 Ch9) | drives erasure | — | — |

---

# Part B — Runtime / intelligence objects (Volume 1 / Volume 2)

## B.1 ResponsePlan  *(Auth. Source: Conversation Engine — V2 Ch15; runtime contract V1 Ch10)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| plan_id | ULID | PK | no | gen | ULID | sealed per turn | → DecisionEnvelope | (in event log) |
| version | int | ≥1 | no | — | — | immutable once sealed | — | — |
| must_say | list[str] | from Policy (AR-7) | no | [] | policy-sourced | immutable | → Policy | — |
| must_not_say | list[str] | from Policy | no | [] | policy-sourced | immutable | → Policy | — |
| facts | list[Fact] | provenance-tagged | no | — | from system of record (RI-5) | immutable | → CRM/Collections | — |
| intent / strategy | refs | from engines | no | — | V2 Ch3/4 | immutable | → engines | — |
| lineage_ref | LineageRef | — | no | — | links DecisionEnvelope | immutable | → DecisionEnvelope | — |

*Notes:* **sealed, versioned** unit of output (AR-4); the governed/billable/analyzable/testable/deployable artifact. Facts carry provenance; the model never originates them (Law of Authority).

## B.2 DecisionEnvelope  *(Auth. Source: Conversation Engine — V2 Ch15; logged V3 Ch3)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| envelope_id | ULID | PK | no | gen | ULID | per decision | → ResponsePlan, events | (correlation_id) |
| decision | str/enum | — | no | — | — | immutable | — | — |
| confidence | float | 0–1 | no | — | range | immutable | — | — |
| reasoning | text | — | no | — | — | immutable | — | — |
| evidence | list[Ref] | provenance | no | — | — | immutable | → facts | — |
| source_engine | enum | which engine | no | — | V2 | immutable | → engine | — |
| version | int | ≥1 | no | — | — | immutable | — | — |
| timestamp | timestamptz | UTC | no | — | — | immutable | — | — |

*Notes:* **quintuple-duty** — reasoning (V2) / event-log (V3) / audit (V4) / analytics (V5) / ops-forensics (V7). Immutable once written. **Distinct from Negotiation `Envelope`.**

## B.3 CustomerContext  *(Auth. Source: assembled from CRM/Collections — V1 Ch11 / V2 Ch11)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| context_id | ULID | per call | no | gen | ULID | assembled at call start, re-read on recovery (RI-5) | → Customer, Loan | — |
| customer | CustomerRef | authoritative | no | — | from CRM | refreshed | → Customer | — |
| loans | list[LoanRef] | authoritative | no | — | from Collections | refreshed | → Loan | — |
| relationship | RelationshipMemory | — | yes | — | V2 Ch10 | — | → Memory | — |
| freshness | timestamp | — | no | — | TTL bound | re-read on boundary | — | — |

*Notes:* contains **authoritative facts**; the runtime never invents its contents (Law of Authority); re-read fresh on recovery (RI-5).

## B.4 ConversationState  *(Auth. Source: Conversation Engine — V1 Ch10; hot copy Redis V3 Ch4)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| call_id | ULID | PK | no | — | same as Call | live during call | → Call | (call_id) |
| turn_index | int | ≥0 | no | 0 | monotonic | mutable (single-writer RI-2) | — | — |
| phase | enum | greeting/verify/negotiate/… | no | — | in set | mutable | — | — |
| working_memory | WorkingMemory | bounded RI-3 | no | — | V2 Ch10 | mutable | → Memory | — |
| played_offset | offset | — | no | 0 | V1 Ch21 | mutable | → Playback | — |

*Notes:* hot state is **non-authoritative + rehydratable** (V3 Ch4/7); single-writer (RI-2); bounded (RI-3).

## B.5 RelationshipMemory  *(Auth. Source: system-of-record view — V2 Ch10)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| customer_id | ULID | PK | no | — | same tenant | durable | → Customer | (tenant_id, customer_id) |
| interaction_summary | jsonb | derived | no | {} | from history | updated post-call | → calls | — |
| preferences | jsonb | — | yes | {} | — | mutable | — | — |

*Notes:* a **view** of the system of record, not model memory; **carries no authority** (facts still re-read from source).

## B.6 WorkingMemory  *(Auth. Source: in-conversation — V2 Ch10)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Relationships | Indexes |
|---|---|---|---|---|---|---|---|---|
| call_id | ULID | key | no | — | — | live during call only | → Call | (call_id) |
| items | bounded list | RI-3 cap | no | [] | bounded | evicted at cap | — | — |

*Notes:* **bounded (RI-3)** — eviction at cap, never unbounded growth; non-authoritative.

---

# Part C — Eventing & audit objects (Volume 3 / Volume 4)

## C.1 EventEnvelope  *(Auth. Source: Event Log — V3 Ch3 / V6 Ch6)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Indexes |
|---|---|---|---|---|---|---|---|
| event_id | ULID | PK, dedup key | no | gen | unique (AR-15) | immutable | PK |
| event_type | str | `<aggregate>.<event>` | no | — | EV-2 past tense | immutable | (event_type) |
| schema_version | int | ≥1 | no | 1 | EV-4/5 | immutable | — |
| tenant_id | TenantId | non-null | no | — | AR-8 | immutable | (tenant_id) |
| occurred_at | timestamptz | UTC | no | — | — | immutable | (occurred_at) |
| correlation_id / causation_id / trace_id | text | propagated | no/yes/no | — | EV-8 | immutable | (correlation_id) |
| payload | typed | per type | no | — | schema (asyncapi) | immutable | — |
| lineage_ref | LineageRef | — | yes | null | → DecisionEnvelope | immutable | — |

*Notes:* append-only; additive-only versioning; old events readable forever (replay, V3 Ch7).

## C.2 AuditRecord  *(Auth. Source: Audit — V4 Ch11)*

| Field | Type | Constraints | Null | Default | Validation | Lifecycle | Indexes |
|---|---|---|---|---|---|---|---|
| audit_id | ULID | PK | no | gen | — | immutable | PK |
| actor | Subject | — | no | — | authn (V4 Ch5) | immutable | (tenant_id, actor) |
| action / resource | str | — | no | — | — | immutable | (tenant_id, resource) |
| before / after | jsonb(redacted) | PII-safe (V4 Ch10) | yes | — | redacted | immutable | — |
| hash / prev_hash | hash | chain | no | — | integrity (V4 Ch11) | immutable | — |
| at | timestamptz | UTC | no | — | — | immutable | (at) |

*Notes:* tamper-evident **hash chain**; reconciled with crypto-shred for erasure (V4 Ch9) — erase the data, keep the tombstone.

---

## Cross-cutting data rules (apply to all entities)
- **Tenant scoping (AR-8 / DM-2):** every tenant-scoped entity has a non-null, indexed `tenant_id`; no cross-tenant FK (DM-3).
- **Money & time:** money = `(amount_minor, currency)`; timestamps = `timestamptz` UTC.
- **Authoritative vs derived:** the Law of Authority (V4 Ch3) governs which store owns each fact; derived/hot copies (Redis, memory) are non-authoritative + rehydratable (V3 Ch4/7).
- **PII (V4 Ch8/10):** sensitive fields encrypted/tokenized; erasure = crypto-shred + tombstone (V4 Ch9); audit/logs redacted.
- **Migrations (DM-6):** schema changes are expand-contract + reversible.
- **Effects (AR-15 / RI-4):** authoritative effects (PTP/settlement/payment) carry idempotency keys + commit-before-act.
- **Protected schemas:** `ResponsePlan`, `DecisionEnvelope`, `EventEnvelope` change only via Architecture-Board review (V6 Ch11).

## Authoritative-source summary

| Entity | Authoritative source |
|---|---|
| Customer | CRM (V5 Ch4) |
| Loan / EMI / PTP / Settlement | Collections (V5 Ch5) |
| Campaign | Campaigns (V5 Ch6) |
| Call / Transcript | Contact Center / Collections (V5 Ch5/7) |
| ResponsePlan / DecisionEnvelope | Conversation Engine (V2 Ch15) |
| CustomerContext | assembled from CRM/Collections (V1 Ch11) |
| ConversationState / Working Memory | runtime (non-authoritative, V1 Ch10 / V3 Ch4) |
| RelationshipMemory | system-of-record view (V2 Ch10) |
| EventEnvelope | Event Log (V3 Ch3) |
| AuditRecord | Audit (V4 Ch11) |

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Data dictionary consolidated from Vols 1–7 | Documentation Engineering |

**Change-log policy:** any new/changed entity or field in a volume MUST be recorded here. The suite consistency audit (DocSuite-12) verifies every architecture entity has a dictionary entry.

*End of Document 3 — Data Dictionary.*
