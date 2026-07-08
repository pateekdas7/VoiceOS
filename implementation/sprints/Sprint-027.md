# Sprint-027 — Monitoring, Alerting, Logging, Tracing & Disaster Recovery

**Epic:** E7 — Production Alpha  
**Status:** ⬜ Pending  
**Depends on:** Sprint-026  
**Blocks:** Sprint-028  

---

## Objective

Wire up the complete production observability stack (Prometheus, Grafana SLO dashboards, Alertmanager, centralized logging, distributed tracing), multi-window burn-rate alerting, and implement the disaster recovery procedures (PITR, multi-AZ failover, Autoscaling) with a verified DR drill.

---

## Architecture References

- Volume 7: Ch4 (Deployment Strategy — canary, drain-aware), Ch6 (GPU Fleet Management — fleet pool health, model warm-up, fleet-level VRAM management, node failure reroute), Ch7 (Monitoring Platform — Prometheus/Grafana, SLO engine), Ch8 (Alerting — CRITICAL/WARNING/INFO, burn-rate alerts, Alertmanager routing), Ch9 (Logging — centralized, structured), Ch10 (Tracing — OpenTelemetry), Ch13 (Autoscaling — HPA, custom metrics), Ch14 (Disaster Recovery — PITR, multi-AZ, RTO ≤ 30min), Ch15 (Cost Optimization — cost-per-conversation tracking, GPU efficiency, spot/reserved/on-demand mix), Ch21 (Operational Analytics — combined technical+business KPI synthesis, unit economics, MTTR/MTBF)
- Volume 4: Ch18 (Governance Dashboards — consent coverage, policy violation rate, AI incident rate, data-retention compliance), Ch23 (Security Metrics & KPIs — MTTD, MTTR, vulnerability counts, authentication failure rate, threat-detection coverage)
- Volume 3: Ch15–17 (Observability — from reliability layer)
- DocSuite-09: Deployment Cookbook

---

## Related Technical Debt — TT-002 (Redis Production Hardening)

> **Added 2026-07-04 (Sprint-013 TT-003 documentation cleanup) — planning note only, does not change this sprint's scope or objectives.**

`implementation/BACKLOG.md` tracks **TT-002 — Redis Production Hardening — Persistence & Eviction Policy** (filed Sprint-013). **Sprint-019 is TT-002's primary scheduled closure sprint; Sprint-027 is the fallback** if it is not closed there. Check `implementation/BACKLOG.md` and `implementation/sprints/Sprint-019.md` first — if TT-002 is already marked RESOLVED, this note requires no action here.

If TT-002 is still open when this sprint begins, this sprint's Disaster Recovery and observability scope is a natural fit to close it, since it already covers PITR/failover/DR-drill testing for the rest of the stack. Include:

- Redis persistence hardening (AOF configuration: `appendonly yes`, `appendfsync everysec`)
- Appropriate Redis persistence settings for the production topology
- Eviction policy review (`maxmemory-policy volatile-ttl` or the architecture-approved equivalent)
- Redis durability validation and crash recovery testing (fits directly alongside this sprint's DR drill work)
- Persistence benchmarking (write-path latency impact on EventBus/DistributedLock/RateLimiter)
- Production configuration validation
- Deployment updates (`deployment/cpu/bootstrap.sh`/`restore.sh`/`.env.example`)
- Infrastructure validation (re-run `scripts/sprint013_infra_validation.py` or its successor)
- Documentation updates (`deployment/CPU_NODE_STATE.md` §7.1/§18 — close out the TT-002 row)

Update `implementation/BACKLOG.md`'s TT-002 row to **RESOLVED** with date and verification evidence once closed, wherever it ends up being closed (Sprint-019 or here).

---

## Components to Implement

### Prometheus Configuration (`monitoring/prometheus/`)

```
monitoring/prometheus/
├── prometheus.yml          (scrape configs for all services + infra)
├── recording_rules.yml     (SLO recording rules — aggregated over 5m, 30m, 1h, 6h windows)
├── alert_rules/
│   ├── slo_alerts.yml      (SLO burn-rate alerts: first_audio, availability)
│   ├── infrastructure.yml  (node down, disk full, OOM, GPU unavailable)
│   ├── application.yml     (error rate spike, circuit breaker open, DLQ growing)
│   └── business.yml        (ptp_rate drop, campaign_completion_rate drop)
└── targets/                (service discovery target configs per environment)
```

**SLO Burn-Rate Alerts (multi-window):**
- `first_audio_p95_slo` target: ≤ 1.5s
- Page (CRITICAL): fast burn — 1h burn rate > 14.4× AND 5m burn rate > 14.4×
- Ticket (WARNING): slow burn — 6h burn rate > 6× AND 1h burn rate > 6×
- `availability_slo` target: ≥ 99.95%
- Same multi-window burn-rate pattern

### Grafana Dashboards (`monitoring/grafana/`)

```
monitoring/grafana/
├── provisioning/           (datasource and dashboard provisioning configs)
├── dashboards/
│   ├── slo-overview.json   (SLO attainment + error budget remaining, by service)
│   ├── call-funnel.json    (calls started → VAD → STT → CIL → TTS → completed funnel)
│   ├── gpu-fleet.json      (VRAM used/available per GPU, model pool occupancy, admission rate)
│   ├── latency-breakdown.json (per-stage p50/p95/p99 latency: GW, ASM, preproc, VAD, STT, CIL, LLM, TTS, playback)
│   ├── reliability.json    (circuit breaker states, DLQ depth, retry rates, recovery events)
│   └── business.json       (calls today, PTPs today, conversion rate, campaign progress)
└── alerts/alertmanager.yml (routing: CRITICAL→PagerDuty, WARNING→JIRA, INFO→Slack)
```

### Centralized Logging (`monitoring/logging/`)

```
monitoring/logging/
├── fluentbit.conf          (DaemonSet log collector: tail container logs, parse JSON, forward)
├── loki.yml                (Loki configuration: label-based log storage, retention 30 days)
└── log_query_examples.md   (Common LogQL queries: find call by call_id, tenant errors, etc.)
```

**Log requirements:**
- All logs must be JSON (StructuredLogger from Sprint-016 ensures this)
- PII redacted (PIIRedactor from Sprint-020 applied in StructuredLogger)
- Indexed labels: `tenant_id`, `service`, `call_id`, `trace_id`, `severity`
- Retention: 30 days (DPDP compliance — no longer than needed)

### Distributed Tracing (`monitoring/tracing/`)

```
monitoring/tracing/
├── otel-collector.yml      (OpenTelemetry Collector: receive OTLP, export to Jaeger/Tempo)
└── jaeger.yml              (Jaeger/Tempo: trace storage, UI, retention 7 days)
```

### Disaster Recovery (`infra/dr/`)

```
infra/dr/
├── runbooks/
│   ├── postgres-failover.md        (RDS/Cloud SQL failover steps, RTO target)
│   ├── redis-failover.md           (ElastiCache/Memorystore failover)
│   ├── gpu-node-failure.md         (GPU node replacement procedure)
│   └── full-region-failure.md      (multi-region DR if applicable)
├── scripts/
│   ├── trigger-db-failover.sh      (initiates managed DB failover)
│   └── verify-recovery.sh          (post-failover health checks)
└── DR_DRILL_REPORT_TEMPLATE.md     (template for recording each DR drill)
```

**Autoscaling:**
- HPA for all CPU services: scale on `cpu_utilization > 70%`
- Custom metrics HPA for conversation-engine: scale on `active_calls_per_pod > 50`
- GPU Scheduler: scale GPU node pool via Cluster Autoscaler when admission_rejection_rate > 5%

### GPU Fleet Management Operational Layer (V7 Ch6)

Extends the GPU Scheduler (Sprint-008) with fleet-level operational tooling:

```
monitoring/gpu-fleet/
├── fleet_health.py         (GPUFleetHealthMonitor: fleet-level pool health, node status aggregation)
├── warmup.py               (ModelWarmupOrchestrator: pre-loads models on new GPU nodes before traffic)
└── vram_budget.py          (FleetVRAMBudget: fleet-level VRAM allocation accounting)
```

**GPUFleetHealthMonitor:**
- Aggregates per-node VRAM metrics into fleet-level health score
- `fleet_health_score() -> float` — 1.0 = all nodes healthy; < 0.5 = degraded
- Alerts: if fleet_health_score < 0.8 → CRITICAL alert (GPU fleet degraded); if < 0.5 → page on-call
- Grafana dashboard: `monitoring/grafana/dashboards/gpu-fleet.json` expanded with fleet-level VRAM budget view

**ModelWarmupOrchestrator:**
- On GPU node join event: automatically warms up all required model pools (STT, LLM, TTS) before the node accepts traffic
- Warm-before-admit: implements GPU-1 invariant at fleet level (node not added to pool until all models warmed)

### Governance Dashboards (`monitoring/grafana/dashboards/governance/`) (V4 Ch18)

```
monitoring/grafana/dashboards/governance/
├── consent-coverage.json       (% of calls with valid consent; consent revocation trend)
├── policy-violations.json      (policy denial rate by domain: RBI, DPDP, Billing)
├── ai-incidents.json           (AI misbehavior incidents: BLOCK verdicts, REQUIRE_HUMAN rate)
├── data-retention.json         (data older than retention policy: red if any records found)
└── break-glass-usage.json      (supervisor override count, rationale coverage audit)
```

These are governance-specific dashboards distinct from the SLO dashboards above. They serve compliance officers and the AI governance board, not on-call engineers.

### Security Metrics & KPIs Dashboard (V4 Ch23)

```
monitoring/grafana/dashboards/security/
├── security-kpis.json          (MTTD per violation class, MTTR per incident class)
├── vulnerability-tracker.json  (open vulnerability count by severity, patch lag)
├── auth-anomalies.json         (authentication failure rate, MFA bypass attempts)
└── threat-detection.json       (threat-detection coverage %, active threats)
```

**Key security KPIs tracked:**
- MTTD (Mean Time to Detect): per violation class (data breach, policy violation, prompt injection)
- MTTR (Mean Time to Resolve): per incident class
- Authentication failure rate: per tenant (spike = brute-force signal)
- Open vulnerability count: feeds from checkov/tfsec CI scan results
- Threat-detection coverage: % of STRIDE threat model threats with active detection rules

### Cost Optimization Service (`src/services/cost-optimizer/`) (V7 Ch15)

```
src/services/cost-optimizer/
├── __init__.py
├── service.py              (CostOptimizer: cost-per-conversation tracking and optimization)
├── tracker.py              (ConversationCostTracker: aggregates GPU-seconds, STT/LLM tokens per call)
├── gpu_efficiency.py       (GPUEfficiencyAnalyzer: target ≥80% utilization, recommends pool adjustments)
└── instance_mix.py         (InstanceMixOptimizer: spot/reserved/on-demand ratio recommendations)
```

**CostOptimizer interface:**
- `cost_per_conversation(tenant_id, date_range) -> CostReport` — per-call cost breakdown (GPU, STT, LLM, TTS, storage)
- `gpu_efficiency(date_range) -> float` — fleet-level GPU utilization ratio (target ≥0.80)
- `optimize_instance_mix() -> InstanceMixRecommendation` — recommends spot/reserved/on-demand ratio based on usage patterns
- `recommend() -> list[CostRecommendation]` — ranked list of actionable savings recommendations

### Operational Analytics Service (`src/services/ops-analytics/`) (V7 Ch21)

```
src/services/ops-analytics/
├── __init__.py
├── service.py              (OpsAnalytics: combined technical + business KPI synthesis)
├── technical_kpis.py       (TechnicalKPISynthesizer: MTTR, MTBF, SLO attainment from observability data)
└── unit_economics.py       (UnitEconomics: cost/margin per conversation, revenue attribution)
```

**OpsAnalytics:**
- Joins technical KPIs (availability, MTTR, MTBF, SLO attainment) with business KPIs (revenue, collections recovered, cost per call)
- `get_operator_scorecard(date_range) -> OperatorScorecard` — the operator-facing executive view combining both KPI sets
- `unit_economics(tenant_id, date_range) -> UnitEconomicsReport` — cost-per-conversation, margin-per-conversation, break-even analysis

---

## Files Expected to Change

**New:** `monitoring/prometheus/`, `monitoring/grafana/`, `monitoring/logging/`, `monitoring/tracing/`, `infra/dr/`  
**New:** `monitoring/gpu-fleet/`, `monitoring/grafana/dashboards/governance/`, `monitoring/grafana/dashboards/security/`  
**New:** `src/services/cost-optimizer/`, `src/services/ops-analytics/`  
**Modified:** `infra/helm/charts/*/values.yaml` — add Prometheus annotations for scraping  
**New:** `tests/unit/services/test_cost_optimizer.py`, `test_ops_analytics.py`

---

## Acceptance Criteria

- [ ] Prometheus scrapes all services (all targets healthy in Prometheus UI)
- [ ] SLO burn-rate alert fires correctly when simulated latency > 1.5s for 5 minutes
- [ ] Grafana SLO dashboard shows correct error-budget burn rate
- [ ] All service logs appear in Loki with correct labels (tenant_id, call_id searchable)
- [ ] End-to-end trace visible in Jaeger for a test call
- [ ] HPA scales up conversation-engine pods when active_calls_per_pod > 50 (load test)
- [ ] DR drill: simulate Postgres primary failure → standby promotes within 60s (RTO sub-component test)
- [ ] DR drill report: `infra/dr/DR_DRILL_REPORT_2026-MM-DD.md` committed with results
- [ ] `GPUFleetHealthMonitor.fleet_health_score()` returns 1.0 with all nodes healthy; < 0.5 with majority down (simulated)
- [ ] `ModelWarmupOrchestrator`: new GPU node joined → all model pools warmed before node enters serving pool
- [ ] Governance dashboards: all 5 JSON files parse as valid Grafana dashboard definitions
- [ ] Security KPI dashboards: security-kpis.json, auth-anomalies.json validate without error
- [ ] `CostOptimizer.cost_per_conversation()` returns non-zero cost for test call with known GPU/token usage
- [ ] `CostOptimizer.gpu_efficiency()` returns correct utilization ratio for test fixture
- [ ] `OpsAnalytics.get_operator_scorecard()` returns both technical and business KPI sections

---

## Required Tests

**CI validation:**
- `promtool check rules` — validates Prometheus rule files
- `promtool check config` — validates prometheus.yml
- `grafana-dashboard-linter` on all governance + security JSON dashboards

**Unit tests:**
- `test_cost_per_conversation_calculation` — known GPU-seconds + tokens → expected cost
- `test_gpu_efficiency_ratio` — total VRAM used / total VRAM available → correct ratio
- `test_ops_analytics_scorecard_fields` — scorecard has all required technical + business KPI fields
- `test_fleet_health_score_full` — all nodes healthy → score = 1.0
- `test_fleet_health_score_degraded` — 2/4 nodes down → score ≤ 0.5

**Manual validation (documented in DR report):**
- Trigger Postgres failover → verify application reconnects within 30s
- Trigger Redis failover → verify application reconnects, no data loss for TTL-managed keys
- Simulate GPU node failure → verify GPU Scheduler drains to surviving node
- Verify governance dashboards display correctly in Grafana UI

---

## Definition of Done

- [ ] All AC items checked
- [ ] DR drill completed and report committed
- [ ] RTO demonstrated ≤ 30 min in DR drill
- [ ] All Prometheus rules validate
- [ ] SLO burn-rate alerting tested
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-028

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Prometheus rule validation runs via `promtool check rules` locally; Grafana dashboard JSON validated by linter; GPU fleet management tests use simulated node state; cost optimizer and ops analytics use fixture data.

### Files Created

- `monitoring/prometheus/prometheus.yml`, `recording_rules.yml`, `alert_rules/slo_alerts.yml`, `alert_rules/infrastructure.yml`, `alert_rules/application.yml`, `alert_rules/business.yml`, `targets/`
- `monitoring/grafana/provisioning/`, `monitoring/grafana/dashboards/slo-overview.json`, `call-funnel.json`, `gpu-fleet.json`, `latency-breakdown.json`, `reliability.json`, `business.json`
- `monitoring/grafana/dashboards/governance/consent-coverage.json`, `policy-violations.json`, `ai-incidents.json`, `data-retention.json`, `break-glass-usage.json`
- `monitoring/grafana/dashboards/security/security-kpis.json`, `vulnerability-tracker.json`, `auth-anomalies.json`, `threat-detection.json`
- `monitoring/grafana/alerts/alertmanager.yml`
- `monitoring/logging/fluentbit.conf`, `loki.yml`, `log_query_examples.md`
- `monitoring/tracing/otel-collector.yml`, `jaeger.yml`
- `infra/dr/runbooks/postgres-failover.md`, `redis-failover.md`, `gpu-node-failure.md`, `full-region-failure.md`
- `infra/dr/scripts/trigger-db-failover.sh`, `verify-recovery.sh`
- `infra/dr/DR_DRILL_REPORT_TEMPLATE.md`
- `monitoring/gpu-fleet/fleet_health.py`, `warmup.py`, `vram_budget.py`
- `src/services/cost-optimizer/__init__.py`, `service.py`, `tracker.py`, `gpu_efficiency.py`, `instance_mix.py`
- `src/services/ops-analytics/__init__.py`, `service.py`, `technical_kpis.py`, `unit_economics.py`
- `tests/unit/services/test_cost_optimizer.py`, `test_ops_analytics.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| GPU node state | In-memory fixture dict | Simulates 4-node fleet for fleet health score tests |
| Usage data | Static fixture (known GPU-seconds + token counts) | Cost calculation without real Prometheus |
| Prometheus (cost) | Static metric fixtures | `CostOptimizer` uses fixture data for unit tests |

### Validations

| Check | Command | Expected |
|---|---|---|
| Prometheus rule syntax | `promtool check rules monitoring/prometheus/alert_rules/*.yml monitoring/prometheus/recording_rules.yml` | 0 errors |
| Prometheus config | `promtool check config monitoring/prometheus/prometheus.yml` | Valid |
| Grafana dashboard linter | `grafana-dashboard-linter lint monitoring/grafana/dashboards/**/*.json` | 0 errors |
| Alertmanager config | `amtool check-config monitoring/grafana/alerts/alertmanager.yml` | Valid |
| Cost optimizer unit tests | `pytest tests/unit/services/test_cost_optimizer.py` | All pass |
| Ops analytics unit tests | `pytest tests/unit/services/test_ops_analytics.py` | All pass |
| GPU fleet unit tests | `pytest tests/unit/ -k "fleet_health"` | `fleet_health_score = 1.0` (all healthy); `≤ 0.5` (majority down) |
| Coverage | `pytest --cov=src/services/cost-optimizer --cov=src/services/ops-analytics --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `GPUFleetHealthMonitor.fleet_health_score()`: 4/4 nodes healthy → 1.0; 2/4 down → ≤ 0.5
- `CostOptimizer.cost_per_conversation()`: test call with 30s GPU + 2K STT tokens + 1K LLM tokens → expected cost
- `CostOptimizer.gpu_efficiency()`: test fixture with 80% VRAM used → 0.80
- `OpsAnalytics.get_operator_scorecard()`: returns dict with both `technical_kpis` and `business_kpis` keys
- All Prometheus alert rule files: `promtool check rules` → 0 errors
- All Grafana dashboard JSON: linter → 0 errors

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| Prometheus | K8s Deployment — `voiceos-ops` | Scrapes all services; stores metrics |
| Grafana | K8s Deployment — `voiceos-ops` | SLO, call-funnel, GPU fleet, governance, security dashboards |
| Alertmanager | K8s Deployment — `voiceos-ops` | Routes CRITICAL → PagerDuty, WARNING → JIRA, INFO → Slack |
| Loki + FluentBit DaemonSet | `voiceos-ops` | Centralized log aggregation from all pods |
| OpenTelemetry Collector + Jaeger | `voiceos-ops` | Distributed trace collection and visualization |
| CostOptimizerService | K8s Deployment — `voiceos-ops` | Cost-per-conversation tracking + GPU efficiency |
| OpsAnalyticsService | K8s Deployment — `voiceos-ops` | Technical + business KPI synthesis |

**Previously deployed services that remain running:**
- All Sprint-004–026 services

**Deployment procedure:**
1. Deploy Prometheus, Grafana, Alertmanager, Loki, FluentBit, OTel Collector, Jaeger
2. Verify all service targets healthy: Prometheus UI → Targets → all `UP`
3. Deploy CostOptimizerService and OpsAnalyticsService
4. Validate SLO dashboards: inject test latency spike → burn-rate alert fires in Alertmanager
5. Run DR drill: initiate Postgres failover → measure standby promotion time

**Health checks:**
- Prometheus: all service scrape targets `UP`
- Grafana: SLO dashboard renders without errors
- Loki: log query by `call_id` returns logs from multiple services for test call
- Jaeger: full trace for test call visible with all spans

**Integration validation:**
- SLO burn-rate alert: simulate 5 minutes of p95 > 1.5s → CRITICAL alert fires in Alertmanager
- HPA: load test conversation-engine → `active_calls_per_pod > 50` → replica count increases
- DR drill: Postgres failover completed; `verify-recovery.sh` passes; RTO ≤ 30 min documented
- GPU fleet health: `GPUFleetHealthMonitor.fleet_health_score()` returns 1.0 on healthy fleet

**Rollback procedure:**
- Monitoring stack is additive; rollback by removing Helm charts; services remain functional without monitoring
- `helm uninstall <monitoring-chart> -n voiceos-ops` if needed

### GPU Node

**GPU fleet management validation this sprint:**

| Validation | What to check |
|---|---|
| Fleet health score | `GET /gpu-fleet/health` → `fleet_health_score = 1.0` (all nodes healthy) |
| ModelWarmupOrchestrator | Simulate GPU node join → all model pools warmed before traffic admitted |
| Fleet VRAM budget | `monitoring/grafana/dashboards/gpu-fleet.json` shows correct VRAM used/available |
| GPU node failure reroute | Simulate GPU node failure → GPU Scheduler reroutes to surviving node |

No new GPU model deployments. Fleet management and operational visibility are the focus.

### Infrastructure Validation

**CPU Validation:**
- Prometheus targets: `GET /prometheus/api/v1/targets` → all services `state=up`
- SLO alert fires: inject latency spike → Alertmanager receives alert within 60s
- Log search: `{call_id="test-call-001"}` in Loki → logs from Media GW, ASM, VAD, STT, LLM, TTS, ConversationEngine
- Trace: `GET /jaeger/api/traces?service=media-gateway` → test call trace with all spans

**GPU Validation:**
- Fleet health dashboard: `gpu-fleet.json` renders correctly in Grafana with live metrics
- ModelWarmupOrchestrator: new node simulation → pre-warm completes before serving begins

**Networking Validation:**
- FluentBit DaemonSet: log collection from all namespaces (`voiceos-runtime`, `voiceos-data`, `voiceos-platform`)
- OTel Collector: traces from all services forwarded to Jaeger within 5s

### Regression Validation

- Walking skeleton e2e test: passes; full trace visible in Jaeger
- Governance dashboards: all 5 consent/policy/AI-incident panels render without errors
- Security dashboards: all 4 security KPI panels render without errors

**DR Drill Documentation:**
- `infra/dr/DR_DRILL_REPORT_<date>.md` committed with: scenario, timeline, actual RTO, observations, action items

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] Prometheus alert rules and recording rules pass `promtool check`
- [ ] All Grafana dashboard JSON files pass linter (SLO, governance, security)
- [ ] GPU fleet management code (fleet_health.py, warmup.py, vram_budget.py) implemented
- [ ] CostOptimizerService implemented and unit-tested
- [ ] OpsAnalyticsService implemented and unit-tested
- [ ] DR runbooks and scripts written
- [ ] Coverage ≥ 85% (cost-optimizer + ops-analytics)
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] Full observability stack deployed and operational
- [ ] All Prometheus targets in `UP` state
- [ ] SLO burn-rate alert fires correctly on test spike
- [ ] Logs searchable by call_id in Loki
- [ ] Full trace visible in Jaeger for test call
- [ ] DR drill completed; RTO ≤ 30 min documented
- [ ] GPU fleet health score returns 1.0 on healthy fleet
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-028

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-027 deploys the full observability stack — this is the first sprint where the infrastructure documents themselves have automated monitoring after a rebuild.

### CPU_NODE_STATE.md — Updates This Sprint

- Add to Services table (§8.1): Prometheus, Grafana, Alertmanager, Loki, FluentBit DaemonSet, OTel Collector, Jaeger, CostOptimizerService, OpsAnalyticsService — namespace: `voiceos-ops`
- **§13 Observability:** Fully populate all entries with actual endpoints, dashboard URLs, and alert routing rules
- Add Prometheus scrape target list and recording rules summary (§13)
- Add Grafana dashboard inventory: SLO, call-funnel, GPU fleet, latency breakdown, reliability, business, governance, security (§13)
- Add environment variables: `PROMETHEUS_ENDPOINT`, `GRAFANA_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `JAEGER_ENDPOINT`, `PAGERDUTY_SERVICE_KEY` (§11)
- Update §14 Health Checks: add Prometheus targets check, Grafana dashboard check, Loki log query

### GPU_NODE_STATE.md — Updates This Sprint

- Update GPU fleet management section: add `GPUFleetHealthMonitor`, `ModelWarmupOrchestrator` as operational overlay tools (not models)
- Add §17 CPU ↔ GPU Communication: OTel traces now exported from GPU services to OTel Collector on CPU node
- Update `GPU_NODE_STATE.md` last_updated: Sprint-027

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add Prometheus, Grafana, Loki, Jaeger health checks |
| `deployment/cpu/restore.sh` | Add: deploy `voiceos-ops` namespace services (monitoring stack) |
| `deployment/cpu/.env.example` | Add all observability and alerting variable descriptions |

### DR Validation

**Observability stack rebuild validation:**
```bash
bash deployment/cpu/restore.sh
# Expected: voiceos-ops namespace pods all Running
kubectl get pods -n voiceos-ops
```

**Prometheus targets after rebuild:**
```bash
curl -sf http://prometheus.voiceos-ops.svc.cluster.local:9090/api/v1/targets | \
  python3 -c "import sys,json; t=json.load(sys.stdin); print('UP:', sum(1 for a in t['data']['activeTargets'] if a['health']=='up'))"
# Expected: all scrape targets UP
```

**DR drill compliance:**
The DR drill conducted in Phase 2 of this sprint serves as the rebuild validation.
```bash
# Postgres failover test
bash infra/dr/scripts/trigger-db-failover.sh
bash infra/dr/scripts/verify-recovery.sh
# Expected: recovery within RTO ≤ 30 min; RTO documented in DR_DRILL_REPORT
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
```
