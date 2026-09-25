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

## 2026-09-25 — Workstream 1 implementation

Task: implement the executable portion of Production Stabilization without starting other Level-2 workstreams.

Implementation:
- Added BFF and Web API liveness/readiness endpoints.
- Corrected BFF system-health semantics so unsupported Twilio/SIP health is not falsely reported healthy and Event Bus follows Redis health.
- Bounded systemd restart recovery for BFF, Web API, voice runtime, dialer worker and frontend.
- Loaded the existing BFF/dialer Prometheus alert group.
- Added service target, GPU target, CPU, memory, network-error and restart-storm alerts.
- Added scheduled backup-artifact verification for existing MongoDB/Vault/Redis/PostgreSQL backup mechanisms.
- Added 15 requested production-stabilization runbooks.
- Added unit tests for health semantics and systemd restart policy.
- Added backup-verification checks to the existing backup/DR test harness.

Tests:
- PASS: bash -n /tmp/backup_verify.sh.
- NOT EXECUTED: pytest, Jest, Ruff, mypy and coverage because the execution container cannot resolve github.com and no repository checkout is available.
- RUNTIME EVIDENCE REQUIRED: live service restart, dependency failure/recovery, GPU, Prometheus/Alertmanager, backup timer/artifact and log/trace retention tests.

Files changed: BFF, Web API, systemd units/tests, Prometheus configuration/rules, backup verifier/timer/service/test, 15 production-stabilization runbooks, Level-2 tracking files.

Failures/fixes:
- Existing BFF /system/health had unconditional healthy status for Twilio/SIP and Event Bus. Fixed the misleading health semantics.
- Existing core systemd units used Restart=always without a restart-loop bound. Replaced with bounded Restart=on-failure policies.
- Prometheus did not load voiceos_bff_dialer.yml despite the file existing. Added it to rule_files.
- A backup alert was initially drafted against a metric not produced by the verifier; removed it before finalizing so no unsupported metric dependency remains.

Runtime state: no live production infrastructure was available to this coding environment. No runtime claims made.

Commit: implementation changes are split into focused repository commits; final tracking synchronization commit will be recorded after the last tracking update.
