"""Unit tests for the Usage Metering Platform (Sprint-024, V5 Ch10).

All tests run fully in-process — no live Postgres/Redis required (Phase 1);
Redis interactions use ``FakeRedisClient`` (Sprint-013 fixture).

Required named tests (Sprint-024.md):
    test_usage_event_idempotent — same event_id twice -> one record
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.contracts.models.billing import SubscriptionTier, UsageEvent, UsageType
from src.libs.contracts.primitives import TenantId
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.rate_card import DEFAULT_RATE_CARD, TIER_USAGE_LIMITS
from src.services.metering.aggregator import UsageAggregator, hour_bucket
from src.services.metering.collector import UsageCollector
from src.services.metering.enforcer import UsageLimitEnforcer
from src.services.metering.service import MeteringService
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService
from tests.fixtures.redis import FakeRedisClient

_TENANT = TenantId("tenant-a")


class _FakeUsageRepository:
    def __init__(self) -> None:
        self.events: dict[str, UsageEvent] = {}

    def record_usage(self, event: UsageEvent) -> UsageEvent:
        self.events.setdefault(event.usage_event_id, event)
        return self.events[event.usage_event_id]

    def find_uninvoiced(
        self, tenant_id: TenantId, *, since_bucket: str = "", until_bucket: str = ""
    ) -> tuple[UsageEvent, ...]:
        return tuple(
            e
            for e in self.events.values()
            if not e.invoice_id
            and (not since_bucket or e.occurred_at_bucket >= since_bucket)
            and (not until_bucket or e.occurred_at_bucket <= until_bucket)
        )


class _FakeConsumer:
    """Minimal ``ConsumerPort`` double — records subscriptions, dispatches manually."""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def subscribe(self, event_type: str, handler: object) -> None:
        self.handlers[event_type] = handler

    def dispatch(self, event_type: str, payload: dict[str, object]) -> None:
        self.handlers[event_type](payload)  # type: ignore[operator]


def _domain_event_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime(2026, 7, 1, 14, 30, tzinfo=UTC).isoformat(),
        "tenant_id": str(_TENANT),
        "call_id": "call-1",
    }
    base.update(overrides)
    return base


class TestUsageCollector:
    def test_call_dispositioned_rounds_up_to_whole_minutes(self) -> None:
        repo = _FakeUsageRepository()
        collector = UsageCollector(repo, DEFAULT_RATE_CARD)

        collector.handle_call_dispositioned(_domain_event_payload(duration_ms=90_000))  # 1.5 min -> 2

        assert len(repo.events) == 1
        event = next(iter(repo.events.values()))
        assert event.usage_type == UsageType.CALL_MINUTE
        assert event.quantity == 2

    def test_stt_transcribed_records_stt_tokens(self) -> None:
        repo = _FakeUsageRepository()
        collector = UsageCollector(repo, DEFAULT_RATE_CARD)

        collector.handle_stt_transcribed(_domain_event_payload(token_count=5000))

        event = next(iter(repo.events.values()))
        assert event.usage_type == UsageType.STT_TOKEN
        assert event.quantity == 5000
        assert event.total_cost_minor == DEFAULT_RATE_CARD.cost_minor(UsageType.STT_TOKEN, 5000)

    def test_gpu_allocated_rounds_up_seconds(self) -> None:
        repo = _FakeUsageRepository()
        collector = UsageCollector(repo, DEFAULT_RATE_CARD)

        collector.handle_gpu_allocated(_domain_event_payload(allocated_seconds=12.4))

        event = next(iter(repo.events.values()))
        assert event.usage_type == UsageType.GPU_SECOND
        assert event.quantity == 13

    def test_usage_event_idempotent(self) -> None:
        """Same event_id submitted twice (e.g. an at-least-once redelivery) -> one record."""
        repo = _FakeUsageRepository()
        collector = UsageCollector(repo, DEFAULT_RATE_CARD)
        payload = _domain_event_payload(token_count=1000)

        collector.handle_llm_generated(payload)
        collector.handle_llm_generated(payload)

        assert len(repo.events) == 1

    def test_zero_quantity_is_not_recorded(self) -> None:
        repo = _FakeUsageRepository()
        collector = UsageCollector(repo, DEFAULT_RATE_CARD)

        collector.handle_call_dispositioned(_domain_event_payload(duration_ms=0))

        assert len(repo.events) == 0

    def test_register_subscribes_all_four_handlers(self) -> None:
        repo = _FakeUsageRepository()
        collector = UsageCollector(repo, DEFAULT_RATE_CARD)
        consumer = _FakeConsumer()

        collector.register(consumer)

        assert set(consumer.handlers) == {
            "saas.call.dispositioned",
            "saas.stt.transcribed",
            "saas.llm.generated",
            "saas.gpu.allocated",
        }


class TestUsageAggregator:
    def test_aggregate_period_sums_by_usage_type(self) -> None:
        repo = _FakeUsageRepository()
        bucket = "2026-07-01T10:00:00Z"
        repo.record_usage(
            UsageEvent(
                usage_event_id="u1",
                tenant_id=_TENANT,
                usage_type=UsageType.CALL_MINUTE,
                quantity=100,
                unit_cost_minor=200,
                total_cost_minor=20_000,
                currency="INR",
                occurred_at_bucket=bucket,
            )
        )
        repo.record_usage(
            UsageEvent(
                usage_event_id="u2",
                tenant_id=_TENANT,
                usage_type=UsageType.CALL_MINUTE,
                quantity=50,
                unit_cost_minor=200,
                total_cost_minor=10_000,
                currency="INR",
                occurred_at_bucket=bucket,
            )
        )
        aggregator = UsageAggregator(repo)

        totals = aggregator.aggregate_period(
            _TENANT, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 2, tzinfo=UTC)
        )

        assert totals[UsageType.CALL_MINUTE] == 150

    def test_hour_bucket_floors_to_the_hour(self) -> None:
        assert hour_bucket(datetime(2026, 7, 1, 14, 37, 22, tzinfo=UTC)) == "2026-07-01T14:00:00Z"


class TestUsageLimitEnforcer:
    def _enforcer(self) -> UsageLimitEnforcer:
        entitlement_engine = EntitlementEngine(PolicyEngineService(PolicyEngine()))
        return UsageLimitEnforcer(FakeRedisClient(), entitlement_engine)

    def test_allows_under_limit(self) -> None:
        enforcer = self._enforcer()
        assert enforcer.check_and_allow(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 10, "2026-07") is True

    def test_blocks_at_limit(self) -> None:
        """GROWTH tier call-minute limit exceeded -> check_and_allow() returns False."""
        enforcer = self._enforcer()
        limit = TIER_USAGE_LIMITS[SubscriptionTier.GROWTH][UsageType.CALL_MINUTE]
        assert limit is not None

        allowed = enforcer.check_and_allow(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, limit, "2026-07")

        assert allowed is False

    def test_running_total_accumulates_across_calls(self) -> None:
        enforcer = self._enforcer()
        limit = TIER_USAGE_LIMITS[SubscriptionTier.GROWTH][UsageType.CALL_MINUTE]
        assert limit is not None
        period = "2026-07"

        first = enforcer.check_and_allow(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, limit - 5, period)
        second = enforcer.check_and_allow(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 10, period)

        assert first is True
        assert second is False  # (limit - 5) + 10 > limit
        assert enforcer.current_usage(_TENANT, UsageType.CALL_MINUTE, period) == limit - 5


class TestMeteringService:
    def test_check_and_allow_delegates_to_enforcer(self) -> None:
        entitlement_engine = EntitlementEngine(PolicyEngineService(PolicyEngine()))
        enforcer = UsageLimitEnforcer(FakeRedisClient(), entitlement_engine)
        usage_repo = _FakeUsageRepository()
        collector = UsageCollector(usage_repo, DEFAULT_RATE_CARD)
        aggregator = UsageAggregator(usage_repo)
        service = MeteringService(collector, aggregator, enforcer)

        assert service.check_and_allow(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 1, "2026-07") is True

    def test_start_collecting_registers_handlers(self) -> None:
        usage_repo = _FakeUsageRepository()
        collector = UsageCollector(usage_repo, DEFAULT_RATE_CARD)
        aggregator = UsageAggregator(usage_repo)
        enforcer = UsageLimitEnforcer(FakeRedisClient(), EntitlementEngine(PolicyEngineService(PolicyEngine())))
        service = MeteringService(collector, aggregator, enforcer)
        consumer = _FakeConsumer()

        service.start_collecting(consumer)

        assert len(consumer.handlers) == 4
