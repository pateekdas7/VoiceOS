# MONITORING_GAP_REGISTER.md

**Scope:** Every monitoring-stack gap identified in Phase 0, with priority, impact, remediation sketch, and the phase that will close it.
**Produced:** Phase 0 of the Monitoring Productionization Mission.
**See also:** `MONITORING_BASELINE.md`, `MONITORING_DEPENDENCY_MAP.md`.

**Severity key:** `C` = critical (production-correctness blocker), `H` = high, `G` = structural/governance, `D` = documentation.

---

## C2 — GPU fleet health monitor never receives node reports

- **Files:** `monitoring/gpu_fleet/fleet_health.py`, `src/services/web_api/main.py:302`
- **Observation:** `GPUFleetHealthMonitor` is instantiated and wired into routes/alert rules, but `report_node()` is called only from tests and `scripts/dev/run_web_api_dev.py`. No production code path supplies a `GPUNodeSnapshot`.
- **Impact:** `voiceos_gpu_fleet_health_score` gauge is pinned at `1.0`, so `GPUFleetDegraded` and `GPUFleetSevereDegradation` alerts can never fire. `/admin/infrastructure/gpu-fleet` reports a healthy, empty fleet. GPU outages are invisible to the alerting pipeline.
- **Remediation shape:** background task registered via `_build_startup_handlers`; polls the GPU scheduler's VRAM ledger / runtime status endpoint every N seconds, builds a `GPUNodeSnapshot(node_id, healthy, vram_used_mb, vram_budget_mb)` per registered node, calls `monitor.report_node(snapshot)`. Must be covered by `main.py` lifespan task + corresponding test using fake clock.
- **Phase:** 1

---

## C3 — Alert escalation scheduler is not running

- **File:** `src/services/ops_intelligence/plumbing/alert_lifecycle.py` (method `evaluate_escalations(now)`)
- **Observation:** The method exists, but nothing in the BFF lifespan or any systemd service calls it. CRITICAL/WARNING escalation timeouts (15 min / 60 min) are declared but inert.
- **Impact:** An un-acked CRITICAL alert never escalates to a secondary responder. The declared SLA is silently defeated.
- **Remediation shape:** add an asyncio periodic task in `_build_startup_handlers` that runs `alert_lifecycle.evaluate_escalations(now=utcnow())` every 60 s. Must capture exceptions and emit a `voiceos_alert_lifecycle_scheduler_last_run_timestamp` gauge for self-monitoring. Also add a `voiceos_alert_lifecycle_escalations_total{severity}` counter.
- **Phase:** 1

---

## C1 — Prometheus service discovery for the laptop CPU node

- **File:** `monitoring/prometheus/prometheus.yml` (lines 56-116)
- **Observation:** Three Kubernetes SD jobs (`kubernetes-apiservers`, `kubernetes-nodes-cadvisor`, `voiceos-services`) require that the Prometheus pod can reach the apiserver ClusterIP `10.96.0.1`, which is unroutable from the single-host dev deployment (TT-015). Only `voiceos-services-static` (file_sd on `targets/dev.yml`) actually works today.
- **Impact:** New services added to K8s manifests never get scraped unless `targets/dev.yml` is manually edited; developer surprise and silent gap. Also blocks future multi-GPU expansion where new nodes need to be discovered automatically.
- **Remediation shape:** split the config into two profiles — a `single-host` profile that drops the k8s_sd jobs, and a `k8s-cluster` profile that keeps them; select via `--config.file` env in the systemd unit. Document the trade-off in `monitoring/prometheus/README.md` (new file).
- **Phase:** 1

---

## C4 — AdminPortal routing completeness (W17 fix verification)

- **File:** `src/services/web_api/api.py`, `src/services/web_api/main.py`
- **Observation:** W17 commit `b2595fe` added support, impersonation, status, and security-incident routes to the Starlette router before the AdminPortal catch-all `Mount("", app=admin_portal_app)`. Need runtime verification that order is preserved after any future refactor (easy regression — a stray reorder sinks all `/admin/*` endpoints into the catch-all).
- **Impact:** potential silent 404s on W17 endpoints if the mount order is ever disturbed.
- **Remediation shape:** add an integration test that asserts each W17 endpoint returns a 200 (or auth-required 401) and that the raw `app.routes` list places those routes before the catch-all `Mount`.
- **Phase:** 2

---

## H5 — IncidentResponse: HTTP routes present, backend wiring default-only

- **Files:** `src/services/web_api/main.py:358`, `src/services/incident_response/service.py`
- **Observation:** `IncidentResponse()` is instantiated with default (no-op) notifier and audit logger. Routes `/admin/security-incidents*` exist and respond, but DPDP 72 h deadlines and audit-log writes land in memory only.
- **Impact:** incident lifecycle is not durable or auditable across restarts; regulatory notifications never actually dispatch.
- **Remediation shape:** inject the real `AuditLogger` (hash-chained Postgres writer) and a configurable `RegulatoryNotifier` backend (SMTP + webhook) into the constructor. Add a feature flag `incident_response_regulatory_notifications` for cutover.
- **Phase:** 2

---

## G1 — Kubernetes service discovery broken (duplicate of C1 but tracked separately for infra team)

See C1. Owned at the infrastructure / network layer; blocks C1 fully disappearing.

---

## G2 — Alertmanager config divergence

- **Files:** `monitoring/grafana/alerts/alertmanager.yml` (live SoT), `infra/k8s/observability/alertmanager.yaml`, `scripts/k8s/alertmanager-config.yaml`
- **Observation:** three independent Alertmanager configs exist; the Grafana-folder file is the declared source of truth, but no CI check prevents drift and the stub under `scripts/k8s/` points to a long-abandoned webhook URL.
- **Impact:** receiver credentials, routing trees, and inhibit rules can diverge between dev and prod without detection. The stub config, if ever accidentally applied, routes everything to a non-existent host.
- **Remediation shape:** (a) delete `scripts/k8s/alertmanager-config.yaml` (confirmed obsolete); (b) rewrite `infra/k8s/observability/alertmanager.yaml` to mount the Grafana config via ConfigMap generated at deploy time — remove hand-maintained duplication; (c) add a CI job `monitoring-config-drift` that diffs the deployed ConfigMap digest against the file in `monitoring/grafana/alerts/`.
- **Phase:** 2

---

## G3 — Grafana dashboard count below production target

- **Path:** `monitoring/grafana/dashboards/`
- **Observation:** 8 dashboards present (`business`, `call-funnel`, `gpu-fleet`, `latency-breakdown`, `reliability`, `slo-overview`, `governance/*`, `security/*`). The production target of 15 (Volume 7 Ch16) is not met.
- **Missing dashboards (per Volume 7):** infrastructure stack health, service topology / dependency map, incident timeline consolidated view, support tickets SLA board, impersonation audit, AI-reasoning kill-switch state, campaign operational health.
- **Impact:** incident responders have to flip between Prometheus, logs, and ad-hoc queries during an outage.
- **Remediation shape:** scaffold seven new dashboard JSONs via `scripts/grafana/generate_dashboards.py`, provision them, and lint each via `scripts/grafana/validate_dashboard.py`.
- **Phase:** 4

---

## G4 — Metrics module conventions not enforced

- **Path:** `src/services/*/metrics.py` (25 files present)
- **Observation:** files exist but naming conventions, label cardinality guards, and prometheus_client singleton registration are inconsistent. No shared base helper.
- **Impact:** risk of label-cardinality blow-ups (one unbounded label = Prometheus OOM), inconsistent metric names across services, no central discoverability for new engineers.
- **Remediation shape:** introduce `src/services/_common/metrics.py` with `make_counter / make_histogram / make_gauge` helpers that enforce `tenant_id` dimension discipline and bind to a shared registry. Migrate all 25 services in a mechanical pass. Add a flake8 custom plugin or ruff rule that forbids direct `prometheus_client.Counter` imports outside the common module.
- **Phase:** 3

---

## G5 — No persistent background job scheduler

- **File:** `src/services/web_api/main.py` (startup handlers run inside BFF process)
- **Observation:** all periodic work (GPU polling, daily analytics aggregation, alert-lifecycle escalation when wired, incident-response notifier re-check) is set up as asyncio tasks inside the BFF lifespan. No persistent queue, no replay on crash, no horizontal scaling.
- **Impact:** every BFF crash loses in-flight scheduling state; cannot run multiple BFF replicas without duplicate work.
- **Remediation shape:** extract scheduled work into a dedicated `scripts/workers/scheduler.py` entry point run by its own systemd unit; use APScheduler + Redis `JobStore` so jobs survive restarts and only one replica fires each tick (Redis lock). BFF retains in-process handlers only for strictly process-local concerns.
- **Phase:** 5

---

## G6 — Alembic migration inventory not captured in docs

- **Path:** `scripts/db/migrations/alembic/versions/` (head at 0042)
- **Observation:** monitoring/ops tables (`alerts`, `insights`, `reports`, `incidents`, `audit_log`, `support_tickets`, `impersonation_sessions`, …) exist, but no written mapping between migration number → tables created/altered.
- **Impact:** on-boarding engineers must grep each migration to understand the ops schema; schema review during incidents is slow.
- **Remediation shape:** generate `docs/schema/MONITORING_SCHEMA.md` listing each migration ID, its tables, columns relevant to monitoring/ops, and the service that owns the table. Automate via `scripts/db/dump_schema_markdown.py` run in CI.
- **Phase:** 6

---

## G7 — No systemd unit coverage for monitoring daemons

- **Path:** `scripts/systemd/`
- **Observation:** only `alertmanager.service` present. No units for `prometheus`, `grafana`, `loki`, `jaeger`, and no unit for the proposed scheduler worker (G5).
- **Impact:** monitoring stack cannot be managed uniformly on the laptop/single-host deployment; manual process management today.
- **Remediation shape:** add `scripts/systemd/{prometheus,grafana,loki,jaeger,voiceos-scheduler}.service`. Each must use `ExecStartPre` to validate config, `Restart=on-failure`, and a dedicated runtime user. Document in `scripts/systemd/README.md`.
- **Phase:** 5

---

## G8 — Promtail config absent from repo

- **File:** (not found)
- **Observation:** `monitoring/logging/loki.yml` expects FluentBit/Promtail to push labelled streams, but no Promtail config exists in the repo.
- **Impact:** log ingestion depends on an undocumented external config; `loki.yml` cannot be set up end-to-end from this repo alone.
- **Remediation shape:** add `monitoring/logging/promtail.yml` capturing journald + Docker socket sources, with `tenant_id` label extraction from JSON log fields.
- **Phase:** 4

---

## G9 — Startup handler signatures bind collaborators by position

- **File:** `src/services/web_api/api.py:458` (`_build_startup_handlers(gpu_fleet_monitor, analytics_service, alert_lifecycle)`)
- **Observation:** adding a fourth or fifth background concern (e.g., GPU polling + incident notifier re-check) means another positional parameter and another API-wide refactor.
- **Impact:** growth friction; also makes it easy for a reviewer to miss a new background concern added to the lifespan.
- **Remediation shape:** change signature to accept a `BackgroundHandlerRegistry` object; each new concern registers itself as a `(name, start, stop)` triple. Record registrations in a `voiceos_startup_handlers` gauge for observability.
- **Phase:** 3

---

## D1 — Monitoring playbooks missing from runbook corpus

- **Path:** `docs/runbooks/` (indirect — not inventoried in Phase 0)
- **Observation:** 22 runbooks referenced by alert rules; need to confirm one-to-one coverage for each `*.yml` alert in `alert_rules/`.
- **Impact:** alerts may fire without a corresponding runbook link in annotations.
- **Remediation shape:** CI check that every alert in `alert_rules/*.yml` has an `annotations.runbook_url` field and that the pointed-to file exists under `docs/runbooks/`.
- **Phase:** 6

---

## Summary

| ID | Severity | Phase | Owner area |
|---|---|---|---|
| C1 | Critical | 1 | Prometheus / infra |
| C2 | Critical | 1 | GPU scheduler ↔ fleet health |
| C3 | Critical | 1 | Ops intelligence plumbing |
| C4 | Critical | 2 | Web API routing |
| H5 | High | 2 | Incident response wiring |
| G1 | Structural | parallel to C1 | Network / infra |
| G2 | Structural | 2 | Alertmanager config governance |
| G3 | Structural | 4 | Grafana dashboards |
| G4 | Structural | 3 | Metrics conventions |
| G5 | Structural | 5 | Background scheduling |
| G6 | Governance | 6 | Schema docs |
| G7 | Structural | 5 | systemd unit coverage |
| G8 | Structural | 4 | Log ingestion |
| G9 | Structural | 3 | Startup handler ergonomics |
| D1 | Documentation | 6 | Runbook coverage |

**Phase-1 scope (next):** C1, C2, C3 — the three correctness blockers that make the alerting pipeline operationally real.
