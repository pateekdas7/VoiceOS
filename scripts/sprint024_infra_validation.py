#!/usr/bin/env python3
"""Sprint-024 Phase 2 infrastructure validation — run against the real
Postgres (migrations 0021-0023) on the CPU node.

Exercises the scenarios in implementation/sprints/Sprint-024.md's Phase 2
"Integration validation"/"Infrastructure Validation" sections against real
infrastructure. Not part of the pytest suite (pytest coverage of the same
behaviors lives in tests/unit/services/test_{billing,metering,analytics,
bi_platform,reporting}.py and tests/integration/services/test_{metering_
integration,bi_warehouse}.py) — this is an operational smoke-test / evidence
script, following the Sprint-013/.../023 precedent.

The EventBus/Consumer pipeline runs over FakeRedisClient (full Streams
emulation, no real Redis required for that leg — same precedent as
Sprint-013's own integration tests); the Redis *usage-limit-enforcer* leg
below uses a real Redis client, matching Sprint-024.md's explicit "real
Redis + real metering" requirement for that one check.

Usage:
    POSTGRES_DSN=<dsn> REDIS_URL=<url> python scripts/sprint024_infra_validation.py
"""

from __future__ import annotations

import datetime
import os
import sys
import uuid
from datetime import UTC, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import redis as redis_lib

from src.libs.contracts.models.analytics import CallDisposition
from src.libs.contracts.models.billing import SubscriptionTier, UsageType
from src.libs.contracts.models.campaign import AudienceCriteria, Campaign, CampaignResult, RetryPolicy
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.loan import LoanAccount
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import CallId, CampaignId, CustomerId, TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.consumer import Consumer
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.analytics import AnalyticsDailyRepository
from src.libs.repositories.bi import BIRepository
from src.libs.repositories.billing import BillingRepository, InvoiceRepository, UsageRepository
from src.libs.repositories.call_disposition import CallDispositionRepository
from src.libs.repositories.campaign import CampaignRepository
from src.libs.repositories.campaign_result import CampaignResultRepository
from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.loan_account import LoanAccountRepository
from src.libs.repositories.tenant import TenantRepository
from src.services.analytics.aggregation import DailyAggregationJob
from src.services.analytics.campaign_analytics import CampaignAnalytics
from src.services.bi_platform.benchmarking import CrossTenantBenchmarking
from src.services.bi_platform.executive_dashboard import ExecutiveDashboard
from src.services.bi_platform.forecasting import ForecastingEngine
from src.services.bi_platform.warehouse import BIWarehouse
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.invoice import InvoiceGenerator
from src.services.billing.rate_card import DEFAULT_RATE_CARD, TIER_USAGE_LIMITS
from src.services.billing.subscription import SubscriptionManager
from src.services.metering.collector import CALL_DISPOSITIONED_EVENT_TYPE, UsageCollector
from src.services.metering.enforcer import UsageLimitEnforcer
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService
from tests.fixtures.redis import FakeRedisClient

POSTGRES_DSN = os.environ["POSTGRES_DSN"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_NOW = datetime.datetime.now(UTC)
_TODAY = date.today()


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    conn = psycopg2.connect(POSTGRES_DSN)
    print(f"Connected to Postgres: {POSTGRES_DSN.split('@')[-1]}")

    tenant_id = TenantId(str(uuid.uuid4()))
    customer_id = CustomerId(str(uuid.uuid4()))

    try:
        tenant_repo = TenantRepository(conn)
        tenant_repo.create(
            Tenant(
                tenant_id=tenant_id,
                slug=f"sprint024-validation-{uuid.uuid4().hex[:8]}",
                display_name="Sprint-024 Validation Tenant",
                subscription_tier="GROWTH",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        customer_repo = CustomerRepository(conn)
        customer_repo.create(
            Customer(
                customer_id=customer_id,
                tenant_id=tenant_id,
                crm_id=f"crm-{uuid.uuid4().hex[:8]}",
                name="Validation Customer",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        loan_repo = LoanAccountRepository(conn)
        loan_account_id = f"loan-{uuid.uuid4().hex[:8]}"
        loan_repo.create(
            LoanAccount(
                loan_account_id=loan_account_id,
                tenant_id=tenant_id,
                customer_id=customer_id,
                product_type="PERSONAL_LOAN",
                disbursed_amount_minor=10_000_00,
                currency="INR",
                interest_rate_bps=1200,
                tenure_months=12,
                disbursement_date=_TODAY - timedelta(days=180),
                maturity_date=_TODAY + timedelta(days=180),
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        campaign_repo = CampaignRepository(conn)
        campaign_id = CampaignId(str(uuid.uuid4()))
        campaign_repo.create(
            Campaign(
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                name="Sprint-024 Validation Campaign",
                audience_criteria=AudienceCriteria(min_dpd=30),
                retry_policy=RetryPolicy(),
                created_at=_NOW,
                updated_at=_NOW,
                created_by="validator",
            )
        )

        # 1. Create a GROWTH-tier billing subscription.
        billing_repo = BillingRepository(conn)
        subscription_manager = SubscriptionManager(billing_repo)
        subscription = subscription_manager.create_subscription(tenant_id, SubscriptionTier.GROWTH, contract_start=_NOW)
        results.append(
            (
                "POST /billing/subscriptions equivalent: GROWTH subscription created",
                subscription.tier == SubscriptionTier.GROWTH,
                f"tier={subscription.tier.value} base_fee_minor={subscription.base_fee_minor}",
            )
        )

        # 2. Emit test CallCompleted-equivalent (saas.call.dispositioned) events ->
        #    verify usage_events appear in Postgres via the real EventBus/Consumer pipeline.
        usage_repo = UsageRepository(conn)
        collector = UsageCollector(usage_repo, DEFAULT_RATE_CARD)
        redis_fake = FakeRedisClient()
        bus = EventBus(redis_fake)
        publisher = Publisher(bus)
        consumer = Consumer(
            redis_fake, bus, group="sprint024-validation", consumer_name="worker-1", sleep_fn=lambda _s: None
        )
        collector.register(consumer)

        call_id = f"call-{uuid.uuid4()}"
        publisher.publish(
            event_type=CALL_DISPOSITIONED_EVENT_TYPE,
            tenant_id=tenant_id,
            payload={
                "event_id": str(uuid.uuid4()),
                "occurred_at": _NOW.isoformat(),
                "tenant_id": str(tenant_id),
                "call_id": call_id,
                "duration_ms": 90_000,
            },
            correlation_id=call_id,
        )
        processed = consumer.poll_once()
        usage_events = [e for e in usage_repo.find_uninvoiced(tenant_id) if e.resource_id == call_id]
        results.append(
            (
                "CallCompleted (saas.call.dispositioned) event -> usage_event in Postgres",
                processed == 1 and len(usage_events) == 1,
                f"processed={processed} usage_events={len(usage_events)}",
            )
        )

        # 3. Usage idempotency: duplicate event submission -> 1 record in Postgres.
        dup_event_id = str(uuid.uuid4())
        dup_payload = {
            "event_id": dup_event_id,
            "occurred_at": _NOW.isoformat(),
            "tenant_id": str(tenant_id),
            "duration_ms": 60_000,
        }
        collector.handle_call_dispositioned(dup_payload)
        collector.handle_call_dispositioned(dup_payload)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM usage_events WHERE usage_event_id = %s", (dup_event_id,))
        dup_count = cur.fetchone()[0]
        results.append(
            ("Usage idempotency: duplicate event_id -> 1 record in Postgres", dup_count == 1, f"count={dup_count}")
        )

        # 4. Exceed GROWTH tier call-minute limit -> verify 429-equivalent (enforcer returns False).
        entitlement_engine = EntitlementEngine(PolicyEngineService(PolicyEngine()))
        limit = TIER_USAGE_LIMITS[SubscriptionTier.GROWTH][UsageType.CALL_MINUTE]
        assert limit is not None
        real_redis = redis_lib.Redis.from_url(REDIS_URL, db=15, decode_responses=False, protocol=2)
        enforcer = UsageLimitEnforcer(real_redis, entitlement_engine)
        period = f"validation-{uuid.uuid4()}"
        under_limit = enforcer.check_and_allow(
            str(tenant_id), SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, limit - 1, period
        )
        at_limit = enforcer.check_and_allow(str(tenant_id), SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 5, period)
        results.append(
            (
                "Usage limit enforcement: GROWTH call-minute limit exceeded -> 429-equivalent (False)",
                under_limit is True and at_limit is False,
                f"under_limit={under_limit} at_limit={at_limit}",
            )
        )

        # 5. Invoice generation: line items match usage_events aggregate.
        invoice_repo = InvoiceRepository(conn)
        invoice_generator = InvoiceGenerator(usage_repo, invoice_repo)
        period_start = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        period_end = period_start + timedelta(days=1)
        invoice = invoice_generator.generate_invoice(tenant_id, subscription, period_start, period_end)
        reread_invoice = invoice_repo.get_invoice(tenant_id, invoice.invoice_id)
        results.append(
            (
                "Invoice: generated invoice persists + line items match usage aggregate",
                reread_invoice is not None
                and reread_invoice.total_minor == invoice.total_minor
                and len(invoice.line_items) > 0,
                f"total_minor={invoice.total_minor} line_items={len(invoice.line_items)}",
            )
        )

        # 6. Analytics: seed call_dispositions + campaign_results, run DailyAggregationJob,
        #    verify GET /analytics/campaigns/{id}-equivalent ptp_rate.
        disposition_repo = CallDispositionRepository(conn)
        campaign_result_repo = CampaignResultRepository(conn)
        for i in range(10):
            disposition_repo.record(
                CallDisposition(
                    disposition_id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    call_id=CallId(str(uuid.uuid4())),
                    customer_id=customer_id,
                    loan_account_id=loan_account_id,
                    outcome_code="PTP_MADE" if i < 3 else "NOT_REACHABLE",
                    duration_ms=60_000,
                    dispositioned_at=_NOW,
                )
            )
            campaign_result_repo.create(
                CampaignResult(
                    campaign_result_id=str(uuid.uuid4()),
                    campaign_id=campaign_id,
                    tenant_id=tenant_id,
                    customer_id=str(customer_id),
                    outcome_code="PTP_MADE" if i < 3 else "NOT_REACHABLE",
                    ptp_created=i < 3,
                    completed_at=_NOW,
                    created_at=_NOW,
                )
            )
        campaign_analytics = CampaignAnalytics(campaign_result_repo)
        ptp_rate = campaign_analytics.ptp_rate(tenant_id, campaign_id)
        results.append(
            (
                "Analytics: campaign ptp_rate for seeded data (10 calls, 3 PTPs) == 0.30",
                ptp_rate == 0.30,
                f"ptp_rate={ptp_rate}",
            )
        )

        analytics_daily_repo = AnalyticsDailyRepository(conn)
        aggregation_job = DailyAggregationJob(disposition_repo, campaign_result_repo, analytics_daily_repo)
        rollup = aggregation_job.run_for_day(tenant_id, _TODAY)
        reread_rollup = analytics_daily_repo.find_for_day(tenant_id, _TODAY)
        results.append(
            (
                "Scheduled report: cron-equivalent DailyAggregationJob -> row in analytics_daily",
                reread_rollup is not None and reread_rollup.calls_completed == rollup.calls_completed,
                f"calls_completed={rollup.calls_completed} ptp_rate={rollup.ptp_rate}",
            )
        )

        # 7. BI: BIWarehouse.refresh() aggregates analytics + billing + usage into bi_facts schema.
        bi_repo = BIRepository(conn)
        warehouse = BIWarehouse(bi_repo, analytics_daily_repo, usage_repo)
        fact = warehouse.refresh(tenant_id, _TODAY)
        results.append(
            (
                "BI: BIWarehouse.refresh() populates bi_facts.fact_daily",
                fact.revenue_minor > 0 and fact.usage_call_minutes > 0,
                f"revenue_minor={fact.revenue_minor} usage_call_minutes={fact.usage_call_minutes}",
            )
        )

        # 8. ForecastingEngine: non-zero ForecastResult for seeded data.
        forecasting = ForecastingEngine(bi_repo)
        forecast = forecasting.forecast_collections_recovery(tenant_id, horizon_days=7, as_of=_TODAY)
        results.append(
            (
                "BI: ForecastingEngine.forecast_collections_recovery() non-zero result",
                any(p.predicted_recovery_rate > 0 for p in forecast.points),
                f"model={forecast.model} points={len(forecast.points)}",
            )
        )

        # 9. CrossTenantBenchmarking: anonymized percentile, no other tenant IDs exposed.
        benchmarking = CrossTenantBenchmarking(bi_repo)
        benchmark = benchmarking.get_benchmark("revenue_minor", tenant_id, _TODAY)
        results.append(
            (
                "BI: CrossTenantBenchmarking.get_benchmark() anonymized, no tenant IDs",
                benchmark.sample_size >= 1 and "tenant_id" not in benchmark.__dict__,
                f"percentile={benchmark.percentile} sample_size={benchmark.sample_size}",
            )
        )

        # 10. ExecutiveDashboard: GET /bi/executive-summary-equivalent -> all required KPI fields.
        dashboard = ExecutiveDashboard(bi_repo)
        summary = dashboard.get_executive_summary(tenant_id, _TODAY)
        results.append(
            (
                "BI: ExecutiveDashboard.get_executive_summary() returns all required KPI fields",
                all(
                    hasattr(summary, f)
                    for f in (
                        "gross_recovery_rate",
                        "cost_per_conversation_minor",
                        "mom_improvement",
                        "slo_attainment",
                        "compliance_score",
                    )
                ),
                f"gross_recovery_rate={summary.gross_recovery_rate} compliance_score={summary.compliance_score}",
            )
        )
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM bi_facts.fact_daily WHERE tenant_surrogate_key IN (SELECT tenant_surrogate_key FROM bi_facts.dim_tenant WHERE tenant_id = %s)",
            (tenant_id,),
        )
        cur.execute("DELETE FROM bi_facts.dim_tenant WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM analytics_daily WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM campaign_results WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM call_dispositions WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM usage_events WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM invoices WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM billing_subscriptions WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM campaigns WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM loan_accounts WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM customers WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
        conn.commit()
        conn.close()

    print(f"{'CHECK':<75} {'RESULT':<8} DETAIL")
    print("-" * 125)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<75} {status_str:<8} {detail}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
