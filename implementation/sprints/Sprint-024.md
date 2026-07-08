# Sprint-024 — Billing Platform, Usage Metering & Analytics

**Epic:** E6 — SaaS Platform  
**Status:** ⬜ Pending  
**Depends on:** Sprint-021, Sprint-022  
**Blocks:** Sprint-025  

---

## Objective

Implement the billing platform (subscription tiers + usage-based billing + enterprise contracts), real-time usage metering with entitlement enforcement, and the analytics and reporting platform (per-call, per-campaign analytics, scheduled reports, export).

---

## Architecture References

- Volume 5: Ch9 (Billing Platform — subscription + usage + enterprise, entitlements via Policy Engine), Ch10 (Usage Metering — real-time meter events, aggregation, limit enforcement), Ch11 (Analytics Platform), Ch12 (Reporting Platform), Ch21 (Business Intelligence Platform — BI warehouse, forecasting models, cross-tenant benchmarking, executive dashboards)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### `src/services/billing/`

```
src/services/billing/
├── __init__.py
├── service.py              (BillingService: subscription management, invoice generation)
├── subscription.py         (SubscriptionManager: TRIAL/GROWTH/ENTERPRISE tier management)
├── entitlement.py          (EntitlementEngine: checks limits via PolicyEngine)
├── invoice.py              (InvoiceGenerator: monthly invoice calculation)
├── payment.py              (PaymentProcessor: integration stub — Stripe/Razorpay)
└── metrics.py              (mrr_total, invoices_issued, payment_failures)
```

**SubscriptionManager:**
- Tiers: TRIAL (30 days, 100 calls, no SLA), GROWTH (usage-based, 99.9% SLA), ENTERPRISE (contract, 99.95% SLA, dedicated infra)
- Tier features stored in PolicyEngine as entitlement policies (not hardcoded)
- Entitlement check: `PolicyEngine.check(domain=BILLING, action=USE_FEATURE, resource=feature_name)` → PERMIT | DENY (limit exceeded)

**InvoiceGenerator:**
- Monthly invoice calculation: base subscription + usage overage
- Usage overage: `usage_events` table aggregation by usage_type for billing period
- Line items: call-minutes, STT tokens (per 1000), LLM tokens (per 1000), GPU-seconds (per 100), storage-GB (per GB/month)
- Invoice stored as JSONB in Postgres `billing_invoices` table

### `src/services/metering/`

```
src/services/metering/
├── __init__.py
├── service.py              (MeteringService: records usage events)
├── collector.py            (UsageCollector: subscribes to domain events, emits usage_events)
├── aggregator.py           (UsageAggregator: aggregates for billing period)
└── enforcer.py             (UsageLimitEnforcer: blocks service when entitlement exhausted)
```

**UsageCollector:**
- Subscribes to: `CallCompleted` (→ call_minutes), `STTTranscribed` (→ stt_tokens), `LLMGenerated` (→ llm_tokens), `GPUAllocated` (→ gpu_seconds)
- Emits `UsageEvent` for each (idempotent by event_id)

**UsageLimitEnforcer:**
- `check_and_allow(tenant_id, usage_type, quantity) -> bool`
- Sums current period usage from Redis (fast path) + validates against entitlement
- Returns False → service call is blocked; caller must return 429 Too Many Requests

### `src/services/analytics/`

```
src/services/analytics/
├── __init__.py
├── service.py              (AnalyticsService: query API)
├── call_analytics.py       (CallAnalytics: per-call metrics — outcome, duration, intent sequence, negotiation result, sentiment arc)
├── campaign_analytics.py   (CampaignAnalytics: per-campaign — conversion, contactability, promise rate, avg DPD, amount collected)
├── realtime.py             (RealtimeAnalytics: live dashboard data via Server-Sent Events or WebSocket)
└── aggregation.py          (DailyAggregationJob: pre-computes daily rollups for fast dashboard)
```

### `src/services/reporting/`

```
src/services/reporting/
├── __init__.py
├── service.py              (ReportingService: report definition + scheduling)
├── scheduler.py            (ReportScheduler: cron-based, runs DailyAggregationJob)
├── exporter.py             (ExportService: CSV/XLSX/PDF generation)
└── templates/              (Report templates: Campaign Summary, Collections Performance, Compliance Audit)
```

### `src/services/bi-platform/` (V5 Ch21 — Business Intelligence Platform)

```
src/services/bi-platform/
├── __init__.py
├── warehouse.py            (BIWarehouse: aggregates from analytics + revenue + usage into BI layer)
├── forecasting.py          (ForecastingEngine: time-series models for collections recovery prediction)
├── benchmarking.py         (CrossTenantBenchmarking: anonymized performance comparisons across tenants)
├── executive_dashboard.py  (ExecutiveDashboard: top-level KPI API for admin portal)
└── models.py               (BIDataModel: fact tables and dimension tables for BI layer)
```

**BIWarehouse:**
- Aggregates: CallAnalytics (collections outcomes) + BillingService (revenue) + MeteringService (usage) + ComplianceMonitoring (compliance posture)
- Separate from operational analytics in Sprint-024 `AnalyticsService` — this is a BI layer (slower refresh, broader aggregation)
- Daily refresh job: `BIWarehouse.refresh()` runs at 01:00 UTC
- Stores aggregates in a dedicated `bi_facts` schema in Postgres (separate from operational tables)

**ForecastingEngine:**
- `forecast_collections_recovery(tenant_id, horizon_days: int) -> ForecastResult` — time-series prediction
- Models: simple exponential smoothing as baseline; ARIMA for tenants with ≥90 days of history
- Input: historical PTP conversion rates, payment outcomes from `CallAnalytics`

**CrossTenantBenchmarking:**
- Anonymized cross-tenant performance percentile data (no tenant can identify another)
- `get_benchmark(metric: str, tenant_id: str) -> BenchmarkResult` — returns tenant's percentile vs. platform-wide distribution

**ExecutiveDashboard:**
- `get_executive_summary(tenant_id) -> ExecutiveSummary` — returns top-level KPIs: gross recovery rate, cost per conversation, MoM improvement, SLO attainment, compliance score

---

## Files Expected to Change

**New:** `src/services/billing/`, `src/services/metering/`, `src/services/analytics/`, `src/services/reporting/`, `src/services/bi-platform/`  
**New:** `tests/unit/services/test_billing.py`, `test_metering.py`, `test_analytics.py`, `test_bi_platform.py`  
**New:** `tests/integration/services/test_metering_integration.py`, `test_bi_warehouse.py`

---

## Acceptance Criteria

- [ ] Invoice calculation: 500 call-minutes + 1M STT tokens + 500K LLM tokens → correct line item amounts for GROWTH tier pricing
- [ ] Usage limit enforcement: GROWTH tier call-minute limit exceeded → `UsageLimitEnforcer.check_and_allow()` returns False
- [ ] Usage events are idempotent (same `event_id` submitted twice → one usage_event record)
- [ ] `CampaignAnalytics.ptp_rate` correctly calculates (PTPs created / calls completed) for test dataset
- [ ] Scheduled report: cron runs DailyAggregationJob → aggregated rows in analytics table
- [ ] `BIWarehouse.refresh()` aggregates analytics + billing + usage into `bi_facts` schema (integration test)
- [ ] `ForecastingEngine.forecast_collections_recovery()` returns a non-zero ForecastResult for test data
- [ ] `CrossTenantBenchmarking.get_benchmark()` returns anonymized percentile without exposing other tenant IDs
- [ ] `ExecutiveDashboard.get_executive_summary()` returns all required top-level KPI fields

---

## Required Tests

**Unit:**
- `test_invoice_calculation` — 500 call-minutes → correct invoice total
- `test_entitlement_blocks_on_limit` — usage at limit → DENY from PolicyEngine
- `test_usage_event_idempotent` — same event_id twice → one record
- `test_campaign_ptp_rate_calculation` — 10 calls, 3 PTPs → ptp_rate = 0.30

**Integration:**
- `test_metering_event_consumption` — CallCompleted event → usage_event in Postgres
- `test_usage_limit_enforcer` — real Redis + real metering → enforcer blocks at limit

---

## Definition of Done

- [ ] All AC items checked
- [ ] Invoice calculation correct for all tier test cases
- [ ] Usage idempotency verified
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-025

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Billing uses `FakePolicyEngine` for entitlement checks; metering uses `FakeRedisClient` for real-time counters; analytics uses `TestPostgres` Docker fixture.

### Files Created

- `src/services/billing/__init__.py`, `service.py`, `subscription.py`, `entitlement.py`, `invoice.py`, `payment.py`, `metrics.py`
- `src/services/metering/__init__.py`, `service.py`, `collector.py`, `aggregator.py`, `enforcer.py`
- `src/services/analytics/__init__.py`, `service.py`, `call_analytics.py`, `campaign_analytics.py`, `realtime.py`, `aggregation.py`
- `src/services/reporting/__init__.py`, `service.py`, `scheduler.py`, `exporter.py`, `templates/`
- `src/services/bi-platform/__init__.py`, `warehouse.py`, `forecasting.py`, `benchmarking.py`, `executive_dashboard.py`, `models.py`
- `tests/unit/services/test_billing.py`, `test_metering.py`, `test_analytics.py`, `test_bi_platform.py`
- `tests/integration/services/test_metering_integration.py`, `test_bi_warehouse.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| PolicyEngine | `FakePolicyEngine` | Returns DENY when usage at limit; PERMIT otherwise |
| Redis (usage counters) | `FakeRedisClient` | Real-time usage counter without real Redis |
| Postgres | `TestPostgres` Docker fixture | Usage events, analytics, bi_facts schema |
| EventBus | `FakeEventBus` | Publishes `CallCompleted`, `STTTranscribed`, `LLMGenerated` events |
| Payment gateway | `FakePaymentProcessor` | Returns SUCCESS without real Stripe/Razorpay |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/services/test_billing.py tests/unit/services/test_metering.py tests/unit/services/test_analytics.py tests/unit/services/test_bi_platform.py` | All pass |
| Metering integration | `pytest tests/integration/services/test_metering_integration.py` | `CallCompleted` → 1 usage_event in Postgres |
| BI warehouse test | `pytest tests/integration/services/test_bi_warehouse.py` | `refresh()` aggregates into `bi_facts` |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `InvoiceGenerator`: 500 call-minutes + 1M STT tokens + 500K LLM tokens → correct GROWTH tier invoice total
- `UsageLimitEnforcer`: GROWTH tier limit exceeded → returns False → caller returns 429
- Usage idempotency: same `event_id` twice → 1 usage_event record in Postgres
- `CampaignAnalytics`: 10 calls, 3 PTPs → `ptp_rate = 0.30`
- `BIWarehouse.refresh()`: aggregates into `bi_facts` schema
- `ForecastingEngine`: test data → non-zero ForecastResult
- `CrossTenantBenchmarking`: anonymized percentile returned, no other tenant IDs exposed

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| BillingService | K8s Deployment — `voiceos-platform` | Subscription management, invoice generation |
| MeteringService | K8s Deployment — `voiceos-platform` | Real-time usage metering + limit enforcement |
| AnalyticsService | K8s Deployment — `voiceos-platform` | Per-call and per-campaign analytics |
| ReportingService | K8s Deployment — `voiceos-platform` | Scheduled reports + CSV/XLSX/PDF export |
| BIPlatformService | K8s Deployment — `voiceos-platform` | BI warehouse, forecasting, executive dashboard |

**Previously deployed services that remain running:**
- All Sprint-004–023 services

**Deployment procedure:**
1. Deploy all 5 services; confirm Postgres, Redis, EventBus, PolicyEngineService connectivity
2. Create test GROWTH tier subscription via `POST /billing/subscriptions`
3. Emit test `CallCompleted` events → verify usage_events appear in Postgres
4. Exceed GROWTH tier call-minute limit → verify 429 response from UsageLimitEnforcer
5. Run `BIWarehouse.refresh()` → verify `bi_facts` table populated

**Health checks:**
- All 5 services: `GET /health/ready` → 200
- MeteringService: `mrr_total` and `invoices_issued` Prometheus gauges exposed
- AnalyticsService: `DailyAggregationJob` completes without error (check logs)

**Integration validation:**
- Invoice: generate monthly invoice for test tenant → line items match usage_events aggregate
- Usage enforcement: call at limit → 429; call under limit → proceed
- Analytics: `GET /analytics/campaigns/{id}` → correct ptp_rate for seeded test data
- BI: `GET /bi/executive-summary` → all required KPI fields present

**Rollback procedure:**
- `kubectl rollout undo deployment/<service> -n voiceos-platform` per service
- Billing data in Postgres unaffected; usage counters in Redis reset on restart (re-aggregate from events)

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- Invoice calculation: GROWTH tier invoice for test usage period → correct totals
- Usage limit enforcement: 429 response when entitlement exhausted (verified on live API)
- Usage idempotency: duplicate event submission → 1 record in Postgres
- BI refresh: `bi_facts` rows populated after `BIWarehouse.refresh()` call

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- MeteringService → Redis: counter increment latency < 5ms
- MeteringService → PolicyEngineService: entitlement check < 10ms
- AnalyticsService → Postgres: daily aggregation query completes < 30s for test dataset

### Regression Validation

- Walking skeleton e2e test: passes (call now generates usage events)
- HITL queue: unaffected by billing service deployment
- CRM + Collections: PTP creation still metered correctly

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] BillingService (subscription, invoice, entitlement) implemented
- [ ] MeteringService (collector, aggregator, enforcer) implemented
- [ ] AnalyticsService + ReportingService implemented
- [ ] BIPlatformService (warehouse, forecasting, benchmarking, executive dashboard) implemented
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Invoice calculation correct for GROWTH tier test cases
- [ ] Usage idempotency verified
- [ ] BIWarehouse.refresh() integration test passes
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All 5 services deployed and healthy
- [ ] Usage metering live: CallCompleted → usage_event in Postgres
- [ ] Usage limit enforcement returns 429 when entitlement exhausted
- [ ] Analytics API returns correct ptp_rate for test campaign
- [ ] BI warehouse populated after refresh
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-025

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `BillingService`, `MeteringService`, `AnalyticsService`, `ReportingService`, `BIPlatformService` to Services table (§8.1) — namespace: `voiceos-platform`
- Update §12 Database Schema: add `billing_subscriptions`, `billing_invoices`, `usage_events`, `analytics_daily`, `bi_facts` tables
- Add environment variables: `BILLING_SERVICE_URL`, `METERING_SERVICE_URL`, `STRIPE_SECRET_KEY` (placeholder), `RAZORPAY_KEY_ID` (placeholder) (§11)
- Add health check commands for all 5 new services (§14)
- Update Prometheus metrics: add `mrr_total`, `invoices_issued`, `usage_limit_enforced_total` gauges

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-019.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add all 5 billing/analytics services to SERVICE_PORTS map |
| `deployment/cpu/.env.example` | Add billing, payment gateway, analytics variable descriptions |

### DR Validation

**Usage metering after rebuild:**
```bash
# Emit test CallCompleted event; verify usage_event created
python3 scripts/validate/usage_metering.py
# Expected: usage_event in Postgres; Redis counter incremented
```

**Usage limit enforcement:**
```bash
python3 scripts/validate/usage_limit.py --exceed-limit
# Expected: 429 response when GROWTH tier limit exceeded
```

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
```
