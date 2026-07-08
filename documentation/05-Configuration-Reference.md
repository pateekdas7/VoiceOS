# VoiceOS v2 — Documentation Suite

## Document 5 — Configuration Reference

**Type:** Canonical configuration reference (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** Platform/SRE + per-domain owners
**Authority:** Volumes 1–7 are immutable + canonical. This reference **documents** the configuration items already defined across the volumes (notably V1 App.D runtime config, V3 reliability, V4 security, V5 platform, V7 operations). It introduces no new settings; the volumes + the schema'd `config/` are authoritative. Citations: `V<n> Ch<c>`.

> **Per-item columns:** Purpose · Allowed values · Default · Example · Risk of changing · Dependencies. **Risk legend:** 🟥 high (safety/invariant/SLO impact — change only via review/EDR) · 🟧 medium (behavioral/cost impact) · 🟩 low (tuning). **Rules:** config is schema-validated (V1 App.D); secrets are **never** config values — they are vault refs (V4 Ch7 / AR-19); policy/compliance values are **not** config — they live in the Policy Engine (AR-7); config changes go via IaC/pipeline (V7 IaC-3), not manual edits.

---

## 1. Runtime & latency (V1)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `latency.first_audio_p95_ms` | first-audio SLO target | int ms | 1500 | 🟥 | SLO (V7 Ch1); changing redefines the contract (V1 Ch23) |
| `streaming.overlap` | clause overlap for early playback | 1–8 | 4–6 | 🟧 | latency vs quality (V1 Ch23) |
| `vad.silence_ms` | endpointing silence threshold | int ms | tuned | 🟧 | barge-in/turn-taking (V1 Ch6) |
| `prompt.deterministic` | enforce deterministic prompt | bool | true | 🟥 | RI-7 / AR-13 — **must stay true** |
| `buffers.*` | bounded buffer sizes | int (bounded) | tuned | 🟥 | RI-3 — must remain bounded |

## 2. GPU & inference (V1 Ch7/13/17 · V7 Ch6)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `gpu.scheduler` | admission control on | bool/`vol1_ch7` | on | 🟥 | RI-8 OOM-by-construction — **must stay on** |
| `gpu.vram_ledger` | VRAM accounting | reconciled | on | 🟥 | RI-8; drift = P-sev (V7 Ch6) |
| `gpu.utilization_target` | efficiency target | 0–1 | 0.80 | 🟧 | cost vs headroom (V3 Ch19 / V7 Ch15) |
| `gpu.warm_before_admit` | no cold model on hot path | bool | true | 🟥 | GPU-1 (V7 Ch6) — latency |
| `llm.model` | LLM model id | approved catalog | qwen3-8b | 🟥 | V5 Ch14 eval-gated; not free-form |
| `llm.kv_cache` / `prefix_cache` | caches | bool | true | 🟧 | TTFT + cost (V1 Ch12/13) |
| `stt.model` / `tts.model` | STT/TTS models | approved catalog | whisper-lv3-turbo-fp8 / veena | 🟥 | V5 Ch14 |

## 3. Data stores (V3 Ch4/5 · V7)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `postgres.replicas` | read replicas | int ≥0 | ≥1 | 🟧 | read scale / HA (V3 Ch5) |
| `postgres.connection_pool` | pool size | int | tuned | 🟧 | DB load (V7 Ch16) |
| `redis.cluster` | hot-state cluster | bool | true | 🟧 | HA (V3 Ch4/13) |
| `redis.ttl.*` | hot-state TTLs | duration | tuned | 🟩 | non-authoritative only (DM-1) |
| `redis.authoritative` | (guard) | **must be false** | false | 🟥 | DM-1 — Redis never authoritative |
| `object_store.*` | recordings/blobs/archive | bucket cfg | — | 🟧 | encryption on (V4 Ch8) |
| `event_log.retention` | event retention | duration | per policy | 🟥 | replay/audit (V3 Ch3 / V4 Ch9) |

*Note:* the spec mentions "Mongo"; VoiceOS's authoritative store is **Postgres** (V3 Ch5) with the **event log** for events and **Redis** for hot state. Document-store usage, where present, is non-authoritative and follows the same DM-1 rule (never authoritative).

## 4. Reliability — timeouts, retries, breakers, queues (V3 Ch8/10/13/14)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `timeouts.*` | per-dependency timeouts | duration | tuned | 🟧 | fail-fast (V3 Ch14) |
| `retry.policy` | retry/backoff | backoff cfg | exp+jitter | 🟧 | idempotency required (AR-15) |
| `circuit_breaker.*` | breaker thresholds | cfg | tuned | 🟧 | isolate failures (V3 Ch14) |
| `queue.max_depth` | bounded queue cap | int | bounded | 🟥 | RI-3 — must be bounded |
| `queue.dlq` | dead-letter | bool | true | 🟧 | poison handling (V3 Ch10) |
| `idempotency.window` | dedup window | duration | tuned | 🟥 | exactly-once (V3 Ch8 / AR-15) |
| `load_shedding.priority` | shed order | priority cfg | lowest-first | 🟧 | protect hot path (V3 Ch14) |

## 5. Security (V4 · V7 Ch18)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `auth.methods` | enabled authn | oauth2/oidc/saml/mtls | tenant-set | 🟥 | V4 Ch5; SSO/SCIM (V5 Ch22) |
| `secrets.backend` | vault | `vault` | vault | 🟥 | AR-19 — no secrets in config |
| `secrets.rotation` | auto-rotate | cfg | pre-expiry | 🟥 | SECOPS-1 (V7 Ch18) |
| `tls.*` / `cert.renewal` | encryption + cert renewal | cfg | auto | 🟥 | V4 Ch8; outage if expires |
| `encryption.at_rest` | encrypt stores | bool | true | 🟥 | V4 Ch8 — **must stay true** |
| `tenant_isolation` | (guard) | **enforced** | enforced | 🟥 | AR-8 — absolute |
| `pii.redaction` | redact before store/log | bool | true | 🟥 | V4 Ch10 / LOG-1 — **must stay true** |
| `rate_limit.*` | API limits | policy-driven | per tier | 🟧 | V4 Ch12; policy not hardcoded (AR-7) |

> **Policy/compliance values are NOT here.** RBI windows, DPDP retention, `must_say`/`must_not_say`, entitlements live in the **Policy Engine** (V4 Ch4 / AR-7), versioned + governed — never as code/config literals.

## 6. Observability (V3 Ch15–17 · V7 Ch7–10)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `metrics.scrape_interval_s` | Prometheus scrape | int s | 15 | 🟩 | MTTD (V7 Ch7) |
| `metrics.cardinality_limits` | bound label cardinality | cfg | enforced | 🟧 | TSDB health (V7 Ch7) |
| `metrics.standard_labels` | required labels | list | tenant/region/service/version | 🟧 | MON-1 |
| `logging.level` | log verbosity | error/warn/info/debug | info | 🟩 | cost/noise (V3 Ch16) |
| `logging.redaction` | PII redaction before store | bool | true | 🟥 | LOG-1 (V7 Ch9) — **must stay true** |
| `logging.retention` | hot/warm/cold tiers | duration cfg | 14/30/365d | 🟧 | cost vs compliance (V4 Ch9) |
| `tracing.sampling` | trace sampling | cfg | tail: errors+slow + 5% | 🟧 | overhead vs coverage (V7 Ch10) |
| `tracing.propagation` | context propagation | w3c | on (every hop) | 🟥 | TRACE-1 / EV-8 |

## 7. Scaling & capacity (V7 Ch12/13)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `autoscale.signals` | scale triggers | list | concurrent_calls, queue_depth, cpu | 🟧 | SCALE-1 — leading signals (V7 Ch13) |
| `autoscale.min_warm` | warm baseline | int | baseline | 🟥 | no cold hot-path (GPU-1) |
| `autoscale.max` | ceiling | int | cost ceiling | 🟧 | runaway protection (V7 Ch13) |
| `autoscale.scale_down` | drain-aware | bool | true | 🟥 | SCALE-2 — zero dropped calls |
| `autoscale.cooldown_s` | anti-flap | int s | 300 | 🟩 | flapping (V7 Ch13) |
| `capacity.headroom_pct` | spike/failover buffer | int | ≥30 | 🟥 | CAP-1 (V7 Ch12) |
| `capacity.predictive` | predictive pre-scale | cfg | on | 🟧 | spike readiness (V7 Ch12/13) |

## 8. Deployment & release (V7 Ch4/16)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `deploy.strategy` | rollout type | canary/blue_green/rolling | canary | 🟧 | risk-appropriate (V7 Ch4) |
| `deploy.canary_steps_pct` | progression | list | [5,25,50,100] | 🟧 | blast radius (V7 Ch4) |
| `deploy.drain_aware` | finish in-flight | bool | true | 🟥 | DEP-1 — zero dropped calls |
| `deploy.drain_timeout_s` | drain budget | int s | ≥1800 | 🟧 | ≥ max call duration |
| `deploy.auto_rollback` | rollback on gate fail | bool | true | 🟥 | CICD-7 / DEP-1 |
| `release.error_budget_gate` | freeze on budget burn | bool | enforced | 🟥 | REL-1 / error budget (V7 Ch1) |
| `feature_flags.*` | flag state | scope cfg | policy-evaluated | 🟧 | V5 Ch23 (via Policy Engine, AR-7) |

## 9. Disaster recovery (V7 Ch14)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `dr.rpo_min` | recovery point | int min | 5 | 🟥 | DR-1 (V3 Ch18) |
| `dr.rto_min` | recovery time | int min | 30 | 🟥 | DR-1 |
| `dr.replication` | mode + residency | async, residency-bound | on | 🟥 | residency (V4 Ch2) |
| `dr.backup_validation` | restore-test | continuous | on | 🟥 | DR-2 |
| `dr.drills` | DR game-days | cadence | regular | 🟧 | DR-3 |

## 10. Global & residency (V7 Ch17)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `global.routing` | route strategy | latency-based within residency | on | 🟥 | GLOBAL-1 — residency first |
| `global.residency` | permitted regions/tenant | region list | per tenant | 🟥 | V4 Ch2 — **never violated** |
| `global.sync.rpo_min` | cross-region sync lag | int min | 5 | 🟥 | GLOBAL-2 / V3 Ch18 |
| `global.edge` | edge/CDN | cfg | static assets | 🟧 | authority stays central (V7 Ch23) |

## 11. Cost (V7 Ch15)

| Item | Purpose | Allowed | Default | Risk | Dependencies |
|---|---|---|---|---|---|
| `cost.instance_mix` | reserved/spot/on-demand | cfg | reserved baseline | 🟧 | economics (V7 Ch15) |
| `cost.real_time_on_spot` | (guard) | **forbidden** | forbidden | 🟥 | COST-1 — real-time never on spot |
| `cost.caching` | prompt/kv/response caches | cfg | on (safe) | 🟧 | never break authority (V6 16.4) |

---

## Cross-cutting configuration rules
- **No secrets in config** (AR-19): secrets are vault refs (V4 Ch7); a secret literal is a P0.
- **No policy in config** (AR-7): compliance/commercial rules live in the Policy Engine (V4 Ch4).
- **Schema-validated** (V1 App.D): config is typed + validated; invalid config fails the deploy gate.
- **IaC/pipeline only** (IaC-3): config changes go through review + pipeline (V7 Ch3), never manual prod edits.
- **Invariant guards** (🟥 "must stay"): settings like `gpu.scheduler=on`, `redis.authoritative=false`, `encryption.at_rest=true`, `pii.redaction=true`, `tenant_isolation=enforced`, `deploy.drain_aware=true` encode invariants — changing them requires an EDR + Architecture-Board review (V6 Ch11/24) and is normally **forbidden**.
- **Tenant config** (V5 Ch13/14): tenant-facing settings change via the Admin Portal / AI Config Platform (governed, versioned), not raw config.

## Risk summary

| Risk | Meaning | Change path |
|---|---|---|
| 🟥 high | safety/invariant/SLO/residency impact | review + EDR; many are "must stay" guards |
| 🟧 medium | behavior/cost impact | review + canary; monitor |
| 🟩 low | tuning | standard PR + pipeline |

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Configuration reference consolidated from Vols 1–7 | Documentation Engineering |

**Change-log policy:** any new/changed config item in a volume MUST be recorded here with its risk + dependencies. The suite consistency audit (DocSuite-12) verifies every configuration section has documentation.

*End of Document 5 — Configuration Reference.*
