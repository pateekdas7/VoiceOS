# MONITORING_DEPENDENCY_MAP.md

**Scope:** Call graphs, data flows, and configuration dependencies between the components inventoried in `MONITORING_BASELINE.md`.
**Produced:** Phase 0 of the Monitoring Productionization Mission.

---

## 1. Alert ingress → lifecycle → response pipeline

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Prometheus (monitoring/prometheus/*)                                     │
│  scrapes gpu-node + voiceos-services (file_sd) + exporters                │
│  evaluates alert_rules/*.yml at 15s                                       │
└──────────────────┬───────────────────────────────────────────────────────┘
                   │ fires alert (POST)
                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Alertmanager (infra/k8s/observability/alertmanager.yaml)                 │
│  config = monitoring/grafana/alerts/alertmanager.yml                      │
│  routes:                                                                  │
│    • CRITICAL → pagerduty-critical                                        │
│    • WARNING  → jira-warning                                              │
│    • INFO     → default-slack                                             │
│    • ALL      → voiceos-webhook (continue:true, fan-out)                  │
└──────────────────┬───────────────────────────────────────────────────────┘
                   │ POST /webhooks/alertmanager (unauthenticated, cluster-internal)
                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Web API — src/services/web_api/api.py:2600-2621                          │
│  WebhookIngestService.ingest_alertmanager_webhook(payload)                │
│    → AlertLifecycleService.ingest(fingerprint, labels, annotations)       │
│    → asyncio.create_task(SystemXController.handle_alert(raw))             │
└──────────────────┬───────────────────────────────────────────────────────┘
                   │
       ┌───────────┴──────────────────────────┐
       ▼                                      ▼
┌───────────────────────┐           ┌──────────────────────────────────┐
│ AlertLifecycleService │           │ SystemXController                │
│ (plumbing/            │           │ src/services/system_x/           │
│  alert_lifecycle.py)  │           │  controller.py                   │
│                       │           │                                  │
│ State:                │           │ Flow:                            │
│   FIRING →            │           │   classifier → correlator →      │
│   ACKNOWLEDGED or     │           │   policy → guardrails →          │
│   ESCALATED →         │           │   recovery → health_verifier →   │
│   RESOLVED            │           │   audit                          │
│                       │           │                                  │
│ evaluate_escalations: │           │ Claude (claude_client.py) is     │
│   NOT WIRED (C3)      │           │ advisory only                    │
└──────────┬────────────┘           └──────────────────────────────────┘
           │ /admin/alerts (API)
           ▼
    Admin UI (frontend/)
```

**Severity timeouts (AlertLifecycleService):** CRITICAL 15 min, WARNING 60 min, INFO 24 h.
**Dead code warning:** `evaluate_escalations(now)` exists but has no scheduler calling it. See gap **C3**.

---

## 2. GPU fleet health flow

```
GPU scheduler (src/services/gpu_scheduler/)  ──  ???  ──┐
VRAM ledger / warmup health                             │
                                                        │   MISSING LINK (C2):
                                                        │   nobody calls
                                                        ▼   report_node() in prod
┌────────────────────────────────────────────────────────────────────────┐
│  GPUFleetHealthMonitor (monitoring/gpu_fleet/fleet_health.py)          │
│  instantiated at src/services/web_api/main.py:302                      │
│                                                                        │
│  API: report_node(snapshot)        — never called in prod code         │
│       fleet_health_score()         — called by routes + alert rules    │
│       is_degraded() / severely…    — called by alert rules             │
└───────────────┬────────────────────────────────────────────────────────┘
                │
     ┌──────────┼─────────────────────────────────┐
     ▼          ▼                                 ▼
Prometheus   /admin/infrastructure/gpu-fleet   OpsIntelligence
gauges       (web_api route)                   default_checks.py
             (always returns 1.0 today)        (reads score for insights)
```

**Impact:** gauges and routes report `1.0` constantly; `GPUFleetDegraded` alert never fires. See gap **C2**.

**Fix shape (sketch — not yet implemented):** background task running in `main.py` lifespan polls `gpu_scheduler.vram_ledger` (or GPU runtime status endpoint) every N seconds, builds `GPUNodeSnapshot(node_id, healthy, vram_used, vram_budget)`, calls `monitor.report_node(snapshot)`.

---

## 3. OpsIntelligence kill switch gating

```
Feature flag: ops_intelligence_reasoning (platform scope, default off)
                   │
                   ▼
┌────────────────────────────────────────────┐
│  OpsIntelligenceService                    │
│  (ops_intelligence/service.py)             │
└──────┬──────────────────────────┬──────────┘
       │ is_reasoning_enabled()   │ run_scheduled_analysis()
       │                          │
       ▼                          ▼
Web API routes                 CronJob trigger
/admin/ai-insights             (external, not wired yet)
/admin/ai-reports              Returns () when flag off
/admin/capacity-forecasts

When flag OFF → routes return 503/empty, cron returns tuple(); reasoning adapters
NEVER instantiated → _UnreachableReasoningAdapter raise is defense-in-depth only.
```

**Note:** always-on plumbing (`AlertLifecycleService`, `WebhookIngestService`) is deliberately independent of this flag and runs unconditionally.

---

## 4. Incident Response — regulatory path

```
Alert / SystemX decision / manual trigger
                │
                ▼
┌──────────────────────────────────────────────────────────────────┐
│  IncidentResponse (src/services/incident_response/service.py)    │
│    State: OPEN → TRIAGED → CONTAINED → ERADICATED →              │
│           RECOVERED → CLOSED (hash-chained audit on every edge)  │
│                                                                  │
│  Playbook registry (playbooks.py):                               │
│    P1 SECURITY_BREACH  → isolate, block, patch, restore, notify  │
│    P2 DATA_BREACH      → stop_egress, forensics, DPDP notify     │
│    P3 AI_MISBEHAVIOR   → tighten thresholds, disable capability  │
│    P4 UNAUTHORIZED_ACCESS                                        │
│    P5 COMPLIANCE_VIOLATION                                       │
│    P6 CREDENTIAL_COMPROMISE                                      │
└──────────────┬──────────────────────────────────────┬────────────┘
               │                                      │
               ▼                                      ▼
    RegulatoryNotifier                      HTTP routes
    (notifier.py)                           api.py:3316-3390
    DPDP 72h deadline on P2                 POST /admin/security-incidents
    is_overdue(deadline, now)               GET  /admin/security-incidents
                                            GET  /admin/security-incidents/{id}
                                            POST /admin/security-incidents/{id}/close
```

**Wiring gap:** `main.py:358` instantiates `IncidentResponse()` with defaults — real audit logger and real notifier backends are not injected.

---

## 5. Startup / lifespan handlers

```
create_web_api() [api.py:458-466]
    └─ _build_startup_handlers(gpu_fleet_monitor, analytics_service, alert_lifecycle)
         ├─ (daily analytics aggregator)         ← runs in BFF process
         ├─ (gpu polling placeholder)            ← C2: not yet calling report_node()
         └─ (alert lifecycle init)               ← C3: no escalation scheduler
```

**Lifetime:** these handlers only live for the duration of the BFF process. On restart they stop. No persistent job queue → single-point-of-failure for all background monitoring work. See gap **G5**.

---

## 6. Configuration dependency graph

```
monitoring/prometheus/prometheus.yml
    ├─ reads: monitoring/prometheus/targets/{dev,staging,production}.yml
    ├─ reads: monitoring/prometheus/alert_rules/*.yml
    ├─ reads: monitoring/prometheus/recording_rules.yml
    └─ sends to: alertmanager.voiceos-ops.svc:9093

infra/k8s/observability/alertmanager.yaml  (K8s Deployment)
    └─ ConfigMap generated from: monitoring/grafana/alerts/alertmanager.yml (live SoT)
    └─ env substitution for ${PAGERDUTY_SERVICE_KEY}, ${JIRA_WEBHOOK_URL}, ${SLACK_WEBHOOK_URL}
    └─ parallel stale config: scripts/k8s/alertmanager-config.yaml  (G2)

monitoring/grafana/provisioning/dashboards/dashboards.yml
    └─ reads: monitoring/grafana/dashboards/*.json  (8 files — G3)

monitoring/grafana/provisioning/datasources/datasources.yml
    └─ Prometheus url: http://host.docker.internal:9090

monitoring/logging/loki.yml      → alertmanager.voiceos-ops.svc:9093 (ruler)
monitoring/tracing/jaeger.yml    → OTLP collector 4317/4318
```

---

## 7. Database dependencies

Head migration: **0042**. Monitoring/ops tables (inferred via grep + W17 commit):
- `alerts` + fingerprint index — read/written by `AlertLifecycleService`.
- `insights`, `reports`, `capacity_forecasts` — written by `OpsIntelligenceService.run_scheduled_analysis()`, read by `/admin/ai-insights|ai-reports|capacity-forecasts`.
- `incidents` + `audit_log` (hash-chained) — SystemX repositories; `/admin/system-x/*` routes.
- `support_tickets`, `support_ticket_comments` — W17 Phase 17 (migration 0042).
- `impersonation_sessions`, `impersonation_actions` — W17 Phase 17 (migration 0042).
- `incident_response_incidents` (per playbook registry) — written by `IncidentResponse` service.

Full per-migration enumeration is pending (gap **G6**).

---

## 8. HTTP route → service dependency table

| Route | Service | Dependency chain |
|---|---|---|
| `GET /system/health` | HealthAggregator | → DB, Redis, external deps |
| `GET /metrics` | prometheus_client registry | ← all 25 service metrics modules |
| `GET /status` | HealthAggregator | → component health vector (public) |
| `GET /admin/alerts` | AlertRepositoryPort | → `alerts` table |
| `POST /admin/alerts/{id}/ack` | AlertLifecycleService | → `alerts` table |
| `GET /admin/ai-insights` | OpsIntelligenceService | → `insights` + flag check |
| `POST /webhooks/alertmanager` | WebhookIngestService + SystemXController | → AlertLifecycleService.ingest, async SystemX |
| `GET /admin/system-x/incidents` | SystemXService | → `incidents` + `audit_log` |
| `POST /admin/security-incidents` | IncidentResponse | → playbook registry + notifier |
| `GET /admin/support/tickets` | SupportTicketRepository | → `support_tickets` (W17) |
| `POST /admin/impersonation/start` | ImpersonationService | → `impersonation_sessions` (W17) |
| `GET /admin/infrastructure/gpu-fleet` | GPUFleetHealthMonitor | → in-memory node dict (empty — C2) |
| `/admin/v1/*` | AdminPortal ASGI app | → tenants/users/campaigns/billing/audit/AI config/API keys |

---

## 9. External system dependencies

| Dependency | Direction | Config location | Status |
|---|---|---|---|
| Prometheus → Alertmanager | push | prometheus.yml | OK via static |
| Alertmanager → PagerDuty | push | alertmanager.yml receiver | Stubbed (G2) |
| Alertmanager → Jira | push | alertmanager.yml receiver | Stubbed |
| Alertmanager → Slack | push | alertmanager.yml receiver | Stubbed |
| Alertmanager → BFF (`/webhooks/alertmanager`) | push | alertmanager.yml receiver | OK |
| BFF → Postgres | query | `POSTGRES_DSN` env | OK |
| BFF → Redis | pub/sub + rate limit | Redis URL env | OK |
| BFF → Vault | secret fetch at startup | VAULT_TOKEN env | OK |
| BFF → GPU runtime (STT/LLM/TTS) | HTTP | STT_BASE_URL / LLM_BASE_URL / TTS_BASE_URL | OK on CPU node |
| Grafana → Prometheus | query | datasources.yml | OK (via Docker host) |
| Loki ← FluentBit (or external promtail) | push | (external) | Promtail config missing in repo |
| Jaeger ← OTLP collectors | push | jaeger.yml | OK |

---

## 10. Risks implied by the map

1. **C2 (GPU fleet dead)** blocks all `voiceos_gpu_fleet_*` alerts from ever firing — any GPU degradation is silent today.
2. **C3 (no escalation scheduler)** silently defeats CRITICAL/WARNING SLA — alerts age forever if the operator never acks.
3. **G1 (K8s SD broken)** — new services added to K8s won't be scraped unless manually appended to `targets/dev.yml`.
4. **G2 (Alertmanager divergence)** — three config files, no CI drift check; receiver credentials may silently drift between environments.
5. **G5 (no persistent scheduler)** — all periodic work dies with the BFF process; no replay on crash.
6. **Startup wiring default-only** (`IncidentResponse()`, `GPUFleetHealthMonitor()` instantiated without their collaborators) — components present but inert.

Each of these flows directly into `MONITORING_GAP_REGISTER.md`.
