# VoiceOS Level-2 Changelog

## 2026-09-25 — Baseline

Task: establish Level-2 implementation and verification system. No major feature implementation started.

Inspected: README, CLAUDE.md, PROJECT_STATUS.md, implementation planning documents, src/services, src/engines, deployment CPU/GPU/K8s assets, frontend, tests, migrations, monitoring, infra and GitHub CI/release workflows.

Architecture finding: repository is a distributed production-oriented platform with Python runtime/services, Node BFF/dialer, Postgres/Redis/MongoDB/Vault, GPU STT/LLM/TTS, CRM/collections/campaign/billing/analytics/admin/API services, frontend, K8s/Helm/Terraform/systemd and observability.

Testing finding: no repository test suite was executed in this baseline session because the coding container could not clone the branch due to DNS/network failure. Historical test counts in project documentation are not treated as current-session evidence.

GitHub finding: baseline HEAD is c33571a10b8882b1f3da5b0e0d25ebae7ce3c417. GitHub returned no workflow runs for that commit. Combined status reported Vercel failure.

Files created:
- LEVEL2_MASTER_TRACKER.md
- LEVEL2_ACCEPTANCE_CRITERIA.md
- LEVEL2_TEST_MATRIX.md
- LEVEL2_RUNTIME_EVIDENCE.md
- LEVEL2_CHANGELOG.md
- LEVEL2_BLOCKERS.md

Commit: pending dedicated atomic baseline commit.

## 2026-09-25 — Workstream 1 production stabilization

Pre-implementation HEAD: `f9a96abdade81a6717dc154bdc80b19ed1a336fc`; Level-2 baseline: `1646e0896322733e351740f9522db95026956d18`.

Implemented: BFF metrics/error tracking, BFF health truthfulness, Vault file-backend backup scheduling, Level-2 branch CI + Node/Jest job, W1 runbooks and tracking.

Tests: no local commands executed. Runtime/production evidence remains pending.

Commits: `a8b33f676c80d8ba2b12207e2954a4a800308af0`, `9b4d4b8d14234ee01874d2996340b0d0880b6e21`, `3f66005768a1dd01903cac8218f7257089df7ade`, final tracking commit `ad864c62d22e99ce84553b3d187ebd7db8857a3b`, Vault path correction `2bcf6b62f4cb4f48216deb31ce22b6a8e05bc2d4`, final tracker commit `d97b1d42c1f7cfad45d9d421b0f571482bcb33c1`.


## 2026-09-25 — Workstream 1 execution-environment re-check (2026-09-25T07:51:30Z UTC)

Attempted to establish an executable repository environment before running the W1 matrix. A fresh clone of `claude/ssh-gpu-cpu-servers-y99fib` failed with exit 128 because `github.com` could not be resolved. A direct `git status --short` also confirmed there is no local repository checkout. No test or runtime result was fabricated or promoted to PASS. W1 remains open.

Additional inspection clarified the MongoDB PARTIAL status: Docker Compose provides a MongoDB healthcheck and DR tooling, but the inspected Prometheus configuration has no MongoDB-specific exporter/scrape target or MongoDB-specific service-health alert. Runtime verification remains unavailable.


## 2026-09-25 — Workstream 1 MongoDB monitoring remediation
Fixed the MongoDB monitoring implementation gap: added Percona exporter wiring for Compose and Kubernetes, Prometheus scraping, exporter-down and database-unavailable alerts, deploy-time secret injection, network-policy paths, and configuration tests. Implementation is VERIFIED BY SOURCE/CONFIGURATION. Tests are NOT EXECUTED — ENVIRONMENT BLOCKED. Runtime monitoring and Alertmanager fire → route → resolve remain RUNTIME EVIDENCE REQUIRED.


## 2026-09-25 — Workstream 1 Jaeger/OTel retention enforcement
Inspected the actual Jaeger deployment and found a real implementation gap: `monitoring/tracing/jaeger.yml` documented `retention.max_age: 168h`, but the deployed `jaegertracing/all-in-one:1.60` Deployment did not consume that YAML retention field. The documented `jaeger-badger-purge` CronJob also did not exist. The existing architecture was retained and corrected by passing Jaeger's native Badger TTL flag `--badger.span-store-ttl=168h0m0s` directly to the deployed Jaeger process. The reference configuration was updated to document this authoritative enforcement path.

Added `tests/unit/monitoring/test_jaeger_retention.py` covering the 7-day value, actual Deployment attachment, Badger storage paths, ConfigMap wiring, and removal of the nonexistent purge-job claim. Tests: **NOT EXECUTED — ENVIRONMENT BLOCKED**. Runtime retention: **RUNTIME EVIDENCE REQUIRED**.


## Workstream 2 — initial production telephony boundary implementation
Inspected the actual Twilio live path and corrected a genuine tenant-isolation gap: the media gateway previously carried a process-wide DEFAULT_TENANT_ID. Added migration 037 and TelephonyNumberResolver; signed /voice resolves the owning tenant from the provider number (From for outbound, To for inbound), fails closed for unassigned/inactive numbers, binds the resolved tenant into the single-use WSS admission ticket, and uses that tenant for CRM/context/session allocation. The production dialer now selects an active tenant-owned caller ID, with campaign-specific precedence; the legacy global caller ID requires explicit ALLOW_GLOBAL_TWILIO_FROM=true.

Added unit and integration tests. Tests are NOT EXECUTED — ENVIRONMENT BLOCKED.
