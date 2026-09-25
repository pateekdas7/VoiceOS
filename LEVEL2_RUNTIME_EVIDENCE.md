# VoiceOS Level-2 Runtime Evidence

RULE: This file records only actual runtime execution. Source code, documentation, manifests and planned commands are never runtime evidence.

| Environment | Service/test | Action | Result | Evidence |
|---|---|---|---|---|
| Coding container | Repository | Attempted git clone of designated branch | BLOCKED | DNS/network could not resolve github.com |
| GitHub | Baseline commit CI | Queried workflow runs for c33571a10b8882b1f3da5b0e0d25ebae7ce3c417 | NO RUNS RETURNED | GitHub workflow query returned empty |
| GitHub | Vercel | Queried combined commit status | FAILED | Vercel status reported failure |

## Required runtime evidence
CPU startup/health; Postgres/Redis/MongoDB/Vault connectivity; CPU-to-GPU STT/LLM/TTS; real Twilio WebSocket; real call lifecycle; dialer concurrency/crash recovery; CRM sync; billing/payment webhook; tenant isolation; DND/DNC/time-window enforcement; GPU failover/drain; staging deploy/rollback; backup/restore; load/capacity; onboarding; support workflow; cost attribution.

No item may become RUNTIME VERIFIED without exact command/action, timestamp, observed result, logs/metrics and commit SHA.

## Workstream 1 execution record

No CPU/GPU/staging runtime action was executed by this coding session. Source inspection and GitHub edits are implementation evidence only.

Required before RUNTIME VERIFIED:
- CPU service restart/readiness transition.
- Redis/Postgres/MongoDB/Vault failure and recovery.
- Prometheus scrape of BFF/GPU/exporters.
- Controlled Alertmanager fire/route/resolve.
- Backup verification plus non-destructive restore.
- Log rotation/retention check.
- Staging rollback/recovery drill.

All remain **RUNTIME EVIDENCE REQUIRED**.


| Environment | Service/test | Action | Result | Evidence |
|---|---|---|---|---|
| Coding container | Repository execution environment | `git clone --branch claude/ssh-gpu-cpu-servers-y99fib --depth 1 https://github.com/pateekdas7/VoiceOS.git /tmp/VoiceOS` at 2026-09-25T07:51:30Z UTC | BLOCKED | Exit 128: `Could not resolve host: github.com` |
| Coding container | Git working-tree status | `git status --short` at 2026-09-25T07:51:30Z UTC | BLOCKED | Exit 128: `fatal: not a git repository` |

This execution attempt produced no application/runtime evidence. No CPU/GPU/dependency/Prometheus/Alertmanager/backup/restore/recovery drill was executed.


## MongoDB monitoring remediation
No MongoDB runtime action was executed. Required evidence: exporter scrape with observed `mongodb_up`; controlled MongoDB failure/recovery; controlled exporter failure with `up{job="mongodb-exporter"}` transition; and Alertmanager fire → route → resolve. All remain **RUNTIME EVIDENCE REQUIRED**.


## Jaeger/OTel retention
Implementation evidence: the Jaeger Deployment now explicitly passes `--badger.span-store-ttl=168h0m0s` with `SPAN_STORAGE_TYPE=badger`. This is source/configuration evidence only.

Runtime evidence required: deploy the actual Jaeger 1.60 instance, ingest controlled traces with timestamps that straddle the 7-day boundary, verify query/storage behavior, and capture Jaeger/Badger logs or metrics showing expiry/compaction. No such runtime action was executed in this session. Status: **RUNTIME EVIDENCE REQUIRED**.


## Workstream 2 runtime evidence
No authorized Twilio/SIP carrier environment or production credentials are attached. Real outbound/inbound calls, real provider signatures, real Media Streams audio, caller-ID verification, carrier outcome drills, duplicate/out-of-order webhook drills, recording lifecycle, tenant-isolation runtime drills and provider rate-limit behavior all remain **RUNTIME EVIDENCE REQUIRED**. The new phone-number routing implementation is source/configuration evidence only.

## Workstream 2 — latest runtime evidence
No authorized Postgres/Redis/Twilio runtime is attached. Therefore canonical callback transition behavior, migration 038 application, provider CPS enforcement, real carrier callbacks, and real media calls remain **RUNTIME EVIDENCE REQUIRED**.


## W2 continuation evidence — 2026-09-25

| Area | Implementation | Automated test | Integration | Runtime | Production |
|---|---|---|---|---|---|
| Recording lifecycle | IMPLEMENTED | NOT EXECUTED — ENVIRONMENT BLOCKED | BLOCKED | RUNTIME EVIDENCE REQUIRED | RUNTIME EVIDENCE REQUIRED |
| Recording storage/access | IMPLEMENTED | NOT EXECUTED — ENVIRONMENT BLOCKED | BLOCKED | RUNTIME EVIDENCE REQUIRED | RUNTIME EVIDENCE REQUIRED |
| Callback timezone policy | IMPLEMENTED | NOT EXECUTED — ENVIRONMENT BLOCKED | BLOCKED | RUNTIME EVIDENCE REQUIRED | RUNTIME EVIDENCE REQUIRED |
| Provider failure classification | IMPLEMENTED | NOT EXECUTED — ENVIRONMENT BLOCKED | BLOCKED | RUNTIME EVIDENCE REQUIRED | RUNTIME EVIDENCE REQUIRED |
| Webhook timeout/retry hardening | IMPLEMENTED | NOT EXECUTED — ENVIRONMENT BLOCKED | BLOCKED | RUNTIME EVIDENCE REQUIRED | RUNTIME EVIDENCE REQUIRED |
| Telephony lifecycle metrics | IMPLEMENTED/PARTIAL | NOT EXECUTED — ENVIRONMENT BLOCKED | BLOCKED | RUNTIME EVIDENCE REQUIRED | RUNTIME EVIDENCE REQUIRED |
| Canonical call event boundary | IMPLEMENTED | NOT EXECUTED — ENVIRONMENT BLOCKED | BLOCKED | RUNTIME EVIDENCE REQUIRED | RUNTIME EVIDENCE REQUIRED |

Execution rule: source inspection is not runtime evidence. No PostgreSQL migration, Redis behavior, Prometheus scrape, Twilio call, Media Stream, S3 object, signed URL, or provider failure behavior has been executed in this environment.


### W2 continuation runtime evidence — 2026-09-25
No Twilio, PostgreSQL, Redis, S3/object-storage, Media Streams, or production CPU runtime was attached. Recording upload/presign/retention/deletion, callback timezone behavior against real scheduling, provider failure injection, webhook replay storms, media lifecycle telemetry, canonical event persistence, and CPS behavior are **RUNTIME EVIDENCE REQUIRED**. No source inspection is treated as runtime verification.


## W2 continuation — canonical event boundary
As of 2026-09-25, source implementation now includes a PostgreSQL transactional canonical-event outbox (`telephony_event_outbox`), durable DLQ (`telephony_event_dlq`), and a bounded Redis relay. No PostgreSQL or Redis runtime is attached to this coding session, so the following are explicitly **NOT EXECUTED**: migration 042 application, outbox persistence against a real DB, Redis delivery, retry/backoff, stale-lock recovery, DLQ transition, and downstream-unavailable recovery.

No source-level event relay result is treated as runtime evidence. Real consumer/delivery verification remains required.
