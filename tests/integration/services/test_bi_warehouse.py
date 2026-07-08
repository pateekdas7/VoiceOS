"""Integration test for BIWarehouse.refresh() against real Postgres (Sprint-024, V5 Ch21).

Required test (Sprint-024.md): ``BIWarehouse.refresh()`` aggregates
analytics + billing + usage into ``bi_facts`` schema.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.models.billing import UsageEvent, UsageType
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.analytics import AnalyticsDailyRepository
from src.libs.repositories.bi import BIRepository
from src.libs.repositories.billing import UsageRepository
from src.libs.repositories.tenant import TenantRepository
from src.services.bi_platform.warehouse import BIWarehouse
from src.services.metering.aggregator import hour_bucket
from tests.integration.conftest import requires_postgres

# `pg_conn` is a fixture from tests/integration/services/conftest.py — pytest
# auto-discovers it from the same-directory conftest.py, no import needed.


@pytest.fixture
def tenant_id(pg_conn: object) -> Iterator[TenantId]:
    """A real ``tenants`` row — ``usage_events``/``bi_facts.dim_tenant`` are real UUID FKs to it."""
    now = datetime.now(UTC)
    new_tenant_id = TenantId(str(uuid.uuid4()))
    TenantRepository(pg_conn).create(
        Tenant(
            tenant_id=new_tenant_id,
            slug=f"bi-warehouse-it-{uuid.uuid4().hex[:8]}",
            display_name="BI Warehouse Integration Test Tenant",
            subscription_tier="GROWTH",
            created_at=now,
            updated_at=now,
        )
    )
    yield new_tenant_id
    cur = pg_conn.cursor()  # type: ignore[attr-defined]
    cur.execute(
        "DELETE FROM bi_facts.fact_daily WHERE tenant_surrogate_key IN "
        "(SELECT tenant_surrogate_key FROM bi_facts.dim_tenant WHERE tenant_id = %s)",
        (new_tenant_id,),
    )
    cur.execute("DELETE FROM bi_facts.dim_tenant WHERE tenant_id = %s", (new_tenant_id,))
    cur.execute("DELETE FROM analytics_daily WHERE tenant_id = %s", (new_tenant_id,))
    cur.execute("DELETE FROM usage_events WHERE tenant_id = %s", (new_tenant_id,))
    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (new_tenant_id,))
    pg_conn.commit()  # type: ignore[attr-defined]


@requires_postgres
class TestBIWarehouseRefresh:
    def test_refresh_aggregates_into_bi_facts_schema(self, pg_conn: object, tenant_id: TenantId) -> None:
        day = date(2026, 7, 1)

        usage_repo = UsageRepository(pg_conn)
        usage_repo.record_usage(
            UsageEvent(
                usage_event_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                usage_type=UsageType.CALL_MINUTE,
                quantity=100,
                unit_cost_minor=200,
                total_cost_minor=20_000,
                currency="INR",
                occurred_at_bucket=hour_bucket(datetime(2026, 7, 1, 10, tzinfo=UTC)),
            )
        )

        analytics_daily_repo = AnalyticsDailyRepository(pg_conn)
        analytics_daily_repo.upsert(
            AnalyticsDailyRollup(
                analytics_daily_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                day=day,
                calls_completed=10,
                ptp_count=3,
                ptp_rate=0.3,
                recovery_rate=0.4,
                computed_at=datetime.now(UTC),
            )
        )

        bi_repo = BIRepository(pg_conn)
        warehouse = BIWarehouse(bi_repo, analytics_daily_repo, usage_repo)

        fact = warehouse.refresh(tenant_id, day)

        assert fact.revenue_minor == 20_000
        assert fact.usage_call_minutes == 100
        assert fact.ptp_rate == 0.3
        assert fact.recovery_rate == 0.4

        # The row genuinely lives in the bi_facts schema, not just returned in-process.
        reread = bi_repo.find_fact_for_tenant(tenant_id, day)
        assert reread is not None
        assert reread.fact_daily_id == fact.fact_daily_id

    def test_refresh_twice_upserts_not_duplicates(self, pg_conn: object, tenant_id: TenantId) -> None:
        day = date(2026, 7, 2)
        bi_repo = BIRepository(pg_conn)
        warehouse = BIWarehouse(bi_repo, AnalyticsDailyRepository(pg_conn), UsageRepository(pg_conn))

        warehouse.refresh(tenant_id, day)
        warehouse.refresh(tenant_id, day)

        surrogate_key = bi_repo.get_or_create_surrogate_key(tenant_id)
        facts_for_day = [f for f in bi_repo.find_all_facts_for_day(day) if f.tenant_surrogate_key == surrogate_key]
        assert len(facts_for_day) == 1
