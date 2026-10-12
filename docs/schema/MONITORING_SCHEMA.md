# VoiceOS Monitoring — Data Schema Reference

**Phase 6 / G6 — authoritative map of every persistence surface used by the
monitoring, alerting, and incident-response pipeline.**

This document is the single reference an on-call engineer should reach for
when answering *"where does metric/alert/incident X live?"* without having
to grep through migrations, service code, and Grafana at the same time. It
is organized by storage back-end: PostgreSQL (Alembic head `0042`), Redis,
Prometheus TSDB, Loki, and in-process registries.

Each table lists:

- **Owner service** — which service writes/mutates the row.
- **Primary key / cardinality** — rough series/row cardinality so operators
  can reason about scrape and retention cost.
- **Retention** — enforced retention policy, if any.
- **Alert surface** — which Prometheus alert rule file (if any) consumes
  the data.

---

## 1. PostgreSQL — operational tables

Alembic head: `0042_support_tickets_impersonation`. Only tables that back
monitoring/alerting/incident flows are enumerated here; the business
tables (campaigns, leads, calls, PTPs, billing, tenants) are documented
in `docs/schema/CORE_SCHEMA.md` (orthogonal to this document).

| Table                        | Owner service        | Primary key          | Cardinality (per tenant/month) | Retention                       | Alert surface                                                   |
|------------------------------|----------------------|----------------------|--------------------------------|---------------------------------|------------------------------------------------------------------|
| `audit_log`                  | AuditLogger          | `(id)` hash-chained  | ~10⁴–10⁵ rows                  | 7y (compliance)                 | `application.yml::AuditChainBroken` (gap in prev_hash sequence) |
| `compliance_violations`      | PolicyEngine         | `(id, tenant_id)`    | ~10²                           | 2y                              | `application.yml::PolicyDenyRateHigh`                           |
| `incidents`                  | IncidentResponse     | `(id)`               | ~10¹                           | indefinite                      | `application.yml::OpenIncidentP1`                                |
| `incident_timeline_events`   | IncidentResponse     | `(incident_id, ts)`  | ~10² per incident              | tied to parent incident         | —                                                                |
| `regulatory_notifications`   | RegulatoryNotifier   | `(id, incident_id)`  | ~10⁰                           | 7y                              | `application.yml::DPDPNotificationOverdue`                       |
| `support_tickets`            | AdminPortal          | `(id, tenant_id)`    | ~10³                           | 2y                              | `application.yml::SupportTicketBacklog`                          |
| `impersonation_sessions`     | AdminPortal          | `(id, admin_id)`     | ~10¹                           | 7y (audit)                      | `application.yml::ImpersonationWithoutRationale`                 |
| `security_incidents`         | SecurityIncidentSvc  | `(id)`               | ~10⁰                           | 7y                              | `application.yml::SecurityIncidentOpen`                          |
| `call_attempts`              | DialerWorker         | `(id, call_id)`      | ~10⁵                           | 180d                            | `business.yml::CallFailureRateHigh`                              |
| `alert_rules_version`        | AlertLifecycleSvc    | `(id)`               | O(1)                           | indefinite                      | —                                                                |

---

## 2. Redis — hot path state

Only keys that persist monitoring/alerting state (not transient call-path
state like RTP buffers, VAD windows, etc.) are listed.

| Key pattern                                  | Owner service               | TTL / eviction      | Purpose                                                                                              |
|----------------------------------------------|-----------------------------|---------------------|------------------------------------------------------------------------------------------------------|
| `alert_lifecycle:open:{alert_id}`            | AlertLifecycleService       | 7d                  | Currently-firing alert state (severity, started_at, acknowledged_by, escalation_count)               |
| `alert_lifecycle:ack:{alert_id}`             | AlertLifecycleService       | 7d                  | Acknowledgement marker (operator + timestamp)                                                        |
| `alert_lifecycle:escalation_queue`           | AlertLifecycleService       | n/a (ZSET)          | Sorted set, score = next-escalation-at Unix timestamp. Scheduler drains this.                        |
| `incident_response:playbook:{incident_id}`   | IncidentResponse            | 24h                 | In-flight playbook state (P1-P6)                                                                      |
| `ops_intel:dedupe:{alert_fingerprint}`       | OpsIntelligence             | 1h                  | Dedupe window — prevents duplicate pageouts within the window                                        |
| `gpu_fleet:node:{node_id}`                   | GPUFleetHealthMonitor       | 90s                 | Last-known health snapshot. TTL slightly > poller interval so stale pollers mark nodes unhealthy.    |
| `rate_limit:{tenant_id}:{endpoint}`          | RateLimiter                 | 1m sliding window   | Per-tenant 100 RPS cap (Volume 4 security)                                                           |
| `circuit_breaker:{service}:state`            | CircuitBreaker              | n/a                 | CLOSED/HALF_OPEN/OPEN. Mirrored to `voiceos_circuit_breaker_state` gauge.                            |

---

## 3. Prometheus — metric families emitted by monitoring code

Every family here starts with `voiceos_` (G4 convention — enforced by
`assert_label_cardinality_safe()` in `src/libs/observability/metrics.py`).

### 3.1 Self-monitoring of the monitoring stack (Phase 1-3 additions)

| Metric                                                | Type    | Labels           | Emitted by                                                              |
|-------------------------------------------------------|---------|------------------|-------------------------------------------------------------------------|
| `voiceos_gpu_fleet_poller_last_run_timestamp`         | Gauge   | —                | `monitoring/gpu_fleet/metrics.py::record_poller_tick`                   |
| `voiceos_gpu_fleet_poller_errors_total`               | Counter | `node_id`        | `monitoring/gpu_fleet/metrics.py::record_poller_error`                  |
| `voiceos_escalation_scheduler_last_run_timestamp`     | Gauge   | —                | `src/services/ops_intelligence/plumbing/metrics.py::record_scheduler_tick` |
| `voiceos_escalation_scheduler_errors_total`           | Counter | —                | `record_scheduler_error`                                                |
| `voiceos_escalations_total`                           | Counter | `severity`       | `record_escalation`                                                     |
| `voiceos_startup_handlers`                            | Gauge   | `name`           | `BackgroundHandlerRegistry.register`                                    |

### 3.2 RED metrics (per-service)

Every service module calls `get_red_metrics(service_name)` and gets:

| Metric                                      | Type      | Labels                       |
|---------------------------------------------|-----------|------------------------------|
| `voiceos_<service>_requests_total`          | Counter   | `method`, `endpoint`         |
| `voiceos_<service>_errors_total`            | Counter   | `error_type`                 |
| `voiceos_<service>_request_duration_ms`     | Histogram | `method`, `endpoint`         |

### 3.3 Cross-cutting (singletons)

See `src/libs/observability/metrics.py` for the full list. Highlights:

| Metric                                      | Type    | Labels               | Alert                                                 |
|---------------------------------------------|---------|----------------------|--------------------------------------------------------|
| `voiceos_circuit_breaker_state`             | Gauge   | `service`            | `application.yml::CircuitBreakerOpen`                 |
| `voiceos_queue_size_current` / `_max`       | Gauge   | `queue_name`         | `application.yml::QueueDepthHigh`                      |
| `voiceos_gpu_fleet_health_score`            | Gauge   | —                    | `ai_services.yml::GPUFleetUnhealthy`                   |
| `voiceos_call_count_total`                  | Counter | `outcome`            | `business.yml::CallFailureRateHigh`                    |
| `voiceos_policy_decision_total`             | Counter | `domain`, `outcome`  | `application.yml::PolicyDenyRateHigh`                  |

### 3.4 Recording rules

Defined in `monitoring/prometheus/recording_rules.yml` — materialize SLO
burn rates and funnel ratios used by the SLO Overview + Call Funnel
dashboards. Family prefix: `voiceos:`. Example: `voiceos:first_audio_slo:burn_rate_6h`.

Retention: 15d (Prometheus TSDB) — matched to the systemd unit
`voiceos-prometheus.service` `--storage.tsdb.retention.time=15d` flag.

---

## 4. Loki — log labels

Promtail (G8) pushes with these label dimensions. Every stream has
`service` and `app_level` (bounded cardinality). High-cardinality fields
(`tenant_id`, `call_id`, `trace_id`) stay in the line body — matching the
G4 Prometheus convention.

| Label        | Values                                                                       |
|--------------|-------------------------------------------------------------------------------|
| `job`        | `systemd-journal` \| `voiceos-app` \| `<docker container name>`              |
| `host`       | hostname                                                                      |
| `unit`       | `voiceos-bff.service` \| `voiceos-webapi.service` \| `voiceos-scheduler.service` \| … |
| `service`    | `bff`, `webapi`, `voice-runtime`, `dialer-worker`, `scheduler`, `ops-intel`   |
| `app_level`  | `info`, `warn`, `error`, `fatal` (debug/trace dropped at Promtail)            |

Retention: 30d (`monitoring/logging/loki.yml`). Matches DPDP data-minimization.

---

## 5. In-process registries (not persisted)

| Registry                       | Location                                                   | Phase introduced |
|--------------------------------|------------------------------------------------------------|------------------|
| `_RED_METRICS_CACHE`           | `src/libs/observability/metrics.py`                        | Baseline         |
| `BackgroundHandlerRegistry`    | `src/libs/observability/metrics.py` + wired in `api.py`    | Phase 3 / G9     |
| `PlaybookRegistry`             | `src/services/incident_response/registry.py`               | Baseline (H5)    |

---

## 6. Cross-references

| Topic                           | Source of truth                                              |
|---------------------------------|---------------------------------------------------------------|
| Alertmanager routing            | `monitoring/grafana/alerts/alertmanager.yml` (Phase 2 / G2)   |
| Prometheus scrape config        | `monitoring/prometheus/prometheus.yml` (K8s) or `prometheus-single-host.yml` (laptop) — selected by `PROMETHEUS_CONFIG` |
| Alert rule → runbook mapping    | `runbook_url` annotation per rule — enforced by `scripts/ci/check_runbook_coverage.py` (D1) |
| Dashboard provisioning          | `monitoring/grafana/provisioning/dashboards/dashboards.yml`   |
| Loki/Promtail config            | `monitoring/logging/{loki,promtail}.yml`                      |
| Baseline inventory              | `MONITORING_BASELINE.md`                                      |
| Dependency map                  | `MONITORING_DEPENDENCY_MAP.md`                                |
| Gap register                    | `MONITORING_GAP_REGISTER.md`                                  |
