# MONITORING_BASELINE.md

**Scope:** VoiceOS v2 monitoring/observability/incident-management surface as it stands on branch `w17-support` at HEAD `b2595fe`.
**Produced:** Phase 0 of the Monitoring Productionization Mission (2026-10-11).
**Companion docs:** `MONITORING_DEPENDENCY_MAP.md`, `MONITORING_GAP_REGISTER.md`.

---

## 1. Prometheus

**Config file:** `monitoring/prometheus/prometheus.yml` (153 lines)
**Global:** `scrape_interval: 15s`, `evaluation_interval: 15s`, external labels `cluster=voiceos-cpu-node`, `environment=dev`.
**Alertmanager target:** `alertmanager.voiceos-ops.svc.cluster.local:9093` (static).

**Scrape jobs (9):**
| Job | Mode | Status |
|---|---|---|
| `prometheus` | static (self) | OK |
| `kubernetes-apiservers` | k8s_sd role=endpoints | BROKEN (TT-015: apiserver ClusterIP unroutable from GPU node) |
| `kubernetes-nodes-cadvisor` | k8s_sd role=node | BROKEN (same root cause) |
| `voiceos-services` | k8s_sd role=pod, 3 ns | Degraded (depends on K8s SD) |
| `voiceos-services-static` | file_sd (`targets/dev.yml`) | OK — current source of truth |
| `gpu-node` | static (3 ports on `185.216.21.242`: 8100 STT, 8000 LLM, 8200 TTS) | OK |
| `node-exporter` | static | OK |
| `redis-exporter` | static | OK |
| `postgres-exporter` | static | OK |

**Alert rules:** `monitoring/prometheus/alert_rules/` (5 files, 20+ rules)
- `slo_alerts.yml` (60 lines) — 4 rules: FirstAudioSLO{Fast,Slow}Burn, AvailabilitySLO{Fast,Slow}Burn (multi-window burn-rate per Sprint-027).
- `infrastructure.yml` — GPUFleetDegraded, GPUFleetSevereDegradation (reads `voiceos_gpu_fleet_health_score`).
- `application.yml` — service latency, error rate, resource saturation.
- `business.yml` — PTP rate, campaign completion.
- `ai_services.yml` — STT/LLM/TTS service-level SLOs.

**Recording rules:** `monitoring/prometheus/recording_rules.yml` (114 lines, 38 rules in 7 groups):
`voiceos_slo_first_audio`, `voiceos_slo_availability`, `voiceos_business_recording`, `voiceos_stt_latency`, `voiceos_llm_latency`, `voiceos_tts_latency`, `voiceos_conversation_engine_latency`.

**Static targets:** `monitoring/prometheus/targets/{dev,staging,production}.yml` — 26 service targets each, FQDN form `voiceos-platform-<svc>.voiceos-runtime.svc.cluster.local:<port>`. Reload: file_sd watches both files; **manual regeneration required** when a chart `service.port` changes.

---

## 2. Alertmanager

**Three config files on disk — divergence risk (see gap G2):**

| File | Role |
|---|---|
| `monitoring/grafana/alerts/alertmanager.yml` (79 lines) | **Live source of truth.** Routes CRITICAL→pagerduty, WARNING→jira, INFO→slack; voiceos-webhook branch forwards all alerts (continue:true) to BFF ingest. Inhibit rule: CRITICAL suppresses matching WARNING. Severity timeouts: CRITICAL 15m, WARNING 60m, INFO 24h. |
| `infra/k8s/observability/alertmanager.yaml` | K8s Deployment template; init container (busybox sed) substitutes `${VAR}` placeholders with env values. Fallback stubs: `REPLACE_WITH_REAL_PAGERDUTY_ROUTING_KEY`, `example.invalid`. |
| `scripts/k8s/alertmanager-config.yaml` (51 lines) | Minimal stub routing to `http://10.0.2.2:8001/webhooks/alertmanager`. Appears obsolete. |

**Receivers:** `pagerduty-critical`, `jira-warning`, `default-slack`, `voiceos-webhook` (→ `http://web-api.voiceos-runtime.svc:8001/webhooks/alertmanager`).

---

## 3. Grafana

**Dashboards:** `monitoring/grafana/dashboards/` — 8 JSON files (vs. prior claim of 15):
`business.json`, `call-funnel.json`, `gpu-fleet.json`, `latency-breakdown.json`, `reliability.json`, `slo-overview.json`, `governance/*`, `security/*`.

**Provisioning:**
- `provisioning/dashboards/dashboards.yml` — file-based provider, path `/var/lib/grafana/dashboards`, auto-reload every 30s.
- `provisioning/datasources/datasources.yml` — Prometheus datasource at `http://host.docker.internal:9090`, `isDefault: true`.

**Alerting:** reads `monitoring/grafana/alerts/alertmanager.yml`.

---

## 4. Logging & Tracing

- **Loki:** `monitoring/logging/loki.yml` (65 lines). Monolithic, local BadgerDB, 30-day retention (DPDP). Labels indexed: `tenant_id`, `service`, `call_id`, `trace_id`, `severity`.
- **Jaeger:** `monitoring/tracing/jaeger.yml` (43 lines). OTLP gRPC:4317 + HTTP:4318 collector; query UI at `/jaeger` (16686). 7-day retention via `jaeger-badger-purge` CronJob.
- **Promtail:** no config found in repo (likely external DaemonSet).

---

## 5. GPU Fleet Health

**Module:** `monitoring/gpu_fleet/fleet_health.py` (93 lines).
**Class:** `GPUFleetHealthMonitor`. Public API: `report_node(snapshot)`, `remove_node(node_id)`, `node_snapshots()`, `fleet_health_score()`, `is_degraded()` (<0.8), `is_severely_degraded()` (<0.5). Each report updates Prometheus gauges (`voiceos_gpu_fleet_health_score`, `voiceos_gpu_fleet_node_healthy{node_id}`).
**Metrics module:** `monitoring/gpu_fleet/metrics.py` (60 lines) — gauges for fleet score, node health, VRAM used/budget, model pool occupancy.
**Production caller:** `src/services/web_api/main.py:302` instantiates monitor; **no production code path calls `report_node()`**. Only test files and `scripts/dev/run_web_api_dev.py` touch it. See gap C2.

---

## 6. Service Metrics Modules

25 metrics modules under `src/services/*/metrics.py` (one per service): `integration_platform`, `media_gateway`, `metering`, `tts`, `crm`, `ops_intelligence` (plus `ops_intelligence/plumbing/metrics.py`), `dialer`, `conversation_engine`, `audio_preprocessing`, `vad_endpointing`, `ai_governance`, `gpu_scheduler`, `audio_session_manager`, `saas_ops`, `admin_portal`, `billing`, `stt`, `collections`, `policy_engine`, `llm_runtime`, `campaign_management`, `ai_config`, `contact_center`, `api_platform`.

---

## 7. OpsIntelligence

**Facade:** `src/services/ops_intelligence/service.py` — class `OpsIntelligenceService`.
Entry points (two):
- `is_reasoning_enabled(tenant_id)` — gates BFF routes on `ops_intelligence_reasoning` feature flag.
- `run_scheduled_analysis(tenant_id)` — CronJob trigger; returns `()` when flag off.

**Reasoning adapters:** `src/services/ops_intelligence/reasoning/adapters/`
- Real: `ClaudeReasoningAdapter`.
- Default placeholder: `_UnreachableReasoningAdapter` (raises if invoked while flag is off — defense in depth).

**Plumbing (always-on, zero LLM):**
- `plumbing/alert_lifecycle.py` (186 lines) — `AlertLifecycleService`: `ingest()`, `acknowledge()`, `escalate()`, `resolve()`, `evaluate_escalations(now)`. FIRING→ACKNOWLEDGED→RESOLVED or →ESCALATED state machine.
- `plumbing/webhook_ingest.py` (80 lines) — `WebhookIngestService`: adapts Alertmanager POSTs + ComplianceAlerter event-bus events.
- `plumbing/webhook_app.py` — mountable ASGI.
- **No scheduler polls `evaluate_escalations()` in production.** See gap C3.

---

## 8. SystemX

**Purpose:** Governed, auditable incident lifecycle orchestrator; Claude is advisory only.
**Files under `src/services/system_x/`:** `controller.py`, `classifier.py`, `claude_client.py`, `correlator.py`, `guardrails.py`, `health_verifier.py`, `models.py`, `policy.py`, `validator.py`, `evidence_collector.py`, `diagnostic_session.py`, `notifications/engine.py`, `package_builder.py`, `recovery/engine.py`, `repositories/{audit,incident}.py`.

**Production wiring:** `src/services/web_api/main.py:348` instantiates `SystemXService(conn)`. Routes `/admin/system-x/incidents*` (list, detail, audit-trail, recovery-actions, notifications). Entry point: `/webhooks/alertmanager` spawns `SystemXController.handle_alert()` per alert batch.

---

## 9. Incident Response

**Playbooks:** `src/services/incident_response/playbooks.py` — six mandatory P1-P6 playbooks per V4 Ch17 §17.2/§17.12:
P1 SECURITY_BREACH, P2 DATA_BREACH, P3 AI_MISBEHAVIOR, P4 UNAUTHORIZED_ACCESS, P5 COMPLIANCE_VIOLATION, P6 CREDENTIAL_COMPROMISE.

**Service:** `IncidentResponse` (`service.py`) — state lifecycle OPEN→TRIAGED→CONTAINED→ERADICATED→RECOVERED→CLOSED. All transitions audit-logged.
**Notifier:** `RegulatoryNotifier` (`notifier.py`, 36 lines) — DPDP 72h deadline on DATA_BREACH only; `is_overdue()` check.
**HTTP routes:** `src/services/web_api/api.py:3316-3390` (`_build_security_incident_routes`): `POST /admin/security-incidents`, `GET /admin/security-incidents`, `GET /admin/security-incidents/{id}`, `POST /admin/security-incidents/{id}/close`.
**Wiring:** `main.py:358` — default `IncidentResponse()` without real audit logger or notifier backend wired.

---

## 10. Admin Portal

**Directory:** `src/services/admin_portal/` (controllers for tenants, users, campaigns, billing, audit, AI config, API keys).
**Wiring:** `main.py:401-456` (`_build_admin_portal`) — assembled ASGI app; mounted as catch-all at end of `create_web_api()`.
**Mount point:** `/admin/v1/*` with `AdminRoleGateMiddleware + AuditMiddleware`.
**SLA tracking endpoint:** `GET /admin/support/sla-summary` (`_build_sla_routes`, `api.py:3304-3313`) via `ticket_repo.sla_stats()`.
**Public status page:** `GET /status` (`_build_service_status_route`, `api.py:3393-3412`) via `HealthAggregator` — returns `{status, components[], updatedAt}`, no auth.

---

## 11. Web API Routes

**Assembler:** `src/services/web_api/api.py::create_web_api()` (3,415 lines). 32 route groups. Highlights relevant to monitoring/ops:

| Group | Mount |
|---|---|
| System health | `/system/health`, `/metrics`, `/status` |
| Infrastructure (GPU fleet) | `/infrastructure/*` |
| Alerts | `/admin/alerts` (list/ack/resolve) |
| AI Insights/Reports | `/admin/ai-insights`, `/admin/ai-reports` (flag-gated) |
| Capacity | `/admin/capacity-forecasts` (flag-gated) |
| Incident timeline | `/admin/incident-timeline` |
| Webhooks | `/webhooks/alertmanager` (unauthenticated, Alertmanager-ingest + SystemX trigger) |
| SystemX | `/admin/system-x/incidents*` |
| Support (W17) | `/admin/support/{tickets,sla-summary}` |
| Impersonation (W17) | `/admin/impersonation/{start,sessions,{id}/end,{id}/actions}` |
| Security incidents | `/admin/security-incidents*` |
| Admin portal | `/admin/v1/*` (catch-all mount) |

**Middleware stack (`api.py:471-489`):** InternalAuthMiddleware → CORSMiddleware → WebSessionMiddleware.
**Startup handlers (`api.py:458-466`):** `_build_startup_handlers(gpu_fleet_monitor, analytics_service, alert_lifecycle)` registered via lifespan context manager.

---

## 12. Workers / Schedulers / systemd

**Systemd units in `scripts/systemd/`:** only `alertmanager.service` present. No units for `prometheus`, `grafana`, `loki`, `jaeger`, `ops_intelligence-worker`, `alert-lifecycle-scheduler`.
**In-process scheduling:** startup handlers in `main.py` run inside the BFF process lifespan — single-point-of-failure; no persistent/restartable job queue. See gap G5.

---

## 13. Database Schema

**Alembic path:** `scripts/db/migrations/alembic/versions/` — head currently `0042`. Tables relevant to monitoring/ops (inferred via grep): `alerts`, `insights`, `reports`, `capacity_forecasts`, `incidents`, `audit_log` (hash-chained per V3 Ch11), `support_tickets`, `support_ticket_comments`, `impersonation_sessions`, `impersonation_actions`. Full migration-by-migration inventory pending (gap G6).

---

## 14. Frontend

**Directory:** `frontend/`. Admin pages (tenants/users/campaigns/billing/audit/AI config/API keys), Alerts Center, AI Insights, Reports, Capacity, Incident Timeline, SystemX dashboard, Support tickets, Impersonation audit trail, Service status, Security incidents. CLAUDE.md insists on reading `node_modules/next/dist/docs/` before writing new frontend code — this is a non-standard Next.js variant.

---

## 15. CI

**Workflows path:** `.github/workflows/` (not fully enumerated at Phase 0). Known: `ci.yml` 7-stage pipeline. Monitoring-specific gates (config drift, dashboard validation) not yet confirmed.

---

## 16. Coverage Summary

| Component | Present | State | Primary gap |
|---|---|---|---|
| Prometheus | ✓ | 9 scrape jobs; K8s SD broken, file_sd operational | G1 |
| Alertmanager | ✓ | Live config in `monitoring/grafana/alerts/`; two sibling files diverge | G2 |
| Grafana | ✓ | 8 dashboards vs. 15 target | G3 |
| Loki | ✓ | 30d retention OK | — |
| Jaeger | ✓ | 7d retention OK | — |
| Recording rules | ✓ | 38 rules, 7 groups | — |
| Alert rules | ✓ | 20+ rules, 5 files | — |
| GPUFleetHealthMonitor | ✓ | Class present, `report_node()` dead in production | **C2** |
| AlertLifecycleService | ✓ | State machine complete, escalation scheduler absent | **C3** |
| OpsIntelligenceService | ✓ | Flag-gated, reasoning off by default | — |
| SystemX | ✓ | Wired, incident/audit lifecycle complete | — |
| IncidentResponse | ✓ | 6 playbooks, DPDP notifier, routes mounted | — |
| Admin Portal | ✓ | Mounted, SLA tracking active | — |
| systemd units | Partial | `alertmanager.service` only | G5 |
| Schema | ✓ | Head 0042; monitoring tables present | G6 |

All critical and structural gaps are enumerated in `MONITORING_GAP_REGISTER.md`.
