"""Integration tests for the Policy Engine (Sprint-017, V4 Ch4).

These exercise the full evaluation pipeline — Redis rule-set caching,
deny-override composition across the real RBI + DPDP packs, and audit event
emission via a real EventBus/Publisher pair — against `FakeRedisClient`
(no live infrastructure required for these two named tests; see
Sprint-017.md Phase 1 table). Live Postgres/Redis validation against the
CPU node happens in Phase 2 via `scripts/sprint017_infra_validation.py`.
"""

from __future__ import annotations

import time

from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.engine import POLICY_DECISION_MADE_EVENT_TYPE, PolicyEngine
from src.services.policy_engine.rule import PolicyRequest
from tests.fixtures.redis import FakeRedisClient


class _CountingPolicyRepository:
    """Postgres-fallback stub that counts calls, to prove the Redis cache tier
    prevents redundant "Postgres" loads on repeated evaluations."""

    def __init__(self, rule_ids: tuple[str, ...]) -> None:
        self._rule_ids = rule_ids
        self.call_count = 0

    def load_active_rule_ids(self, scope: str, scope_id: str | None) -> tuple[str, ...]:
        self.call_count += 1
        return self._rule_ids if scope == "global" else ()


class TestPolicyEngineCachesRulesInRedis:
    def test_policy_engine_caches_rules_in_redis(self, fake_redis: FakeRedisClient) -> None:
        """Load rules once (Postgres fallback on cache miss), subsequent
        evaluations are served from the Redis cache — no repeated Postgres load.
        """
        repository = _CountingPolicyRepository(("RBI-CALLING-HOURS", "RBI-CALLING-FREQUENCY"))
        engine = PolicyEngine(redis=fake_redis, policy_repository=repository)

        request = PolicyRequest(
            domain="rbi", action="start_call", subject="agent-1", resource="call-1", context={"hour": 21}
        )

        first = engine.evaluate(request)
        assert first.outcome == PolicyOutcome.DENY
        assert repository.call_count == 1

        for _ in range(5):
            repeated = engine.evaluate(request)
            assert repeated.outcome == PolicyOutcome.DENY

        assert repository.call_count == 1, "expected every repeat evaluation to hit the Redis cache, not Postgres"

        # The compiled rule-id list really is in Redis, independently of the engine.
        cached_raw = fake_redis.get("policy:ruleset:global:global")
        assert cached_raw is not None


class TestPolicyEngineFullRBIComplianceSuite:
    def test_policy_engine_full_rbi_compliance_suite(self, fake_redis: FakeRedisClient) -> None:
        """Run every RBI rule scenario through the full PolicyEngine pipeline; 100% pass."""
        bus = EventBus(fake_redis, stream="voiceos-events")
        publisher = Publisher(bus)
        engine = PolicyEngine(redis=fake_redis, publisher=publisher)

        scenarios: list[tuple[PolicyRequest, PolicyOutcome]] = [
            # CALLING_HOURS
            (
                PolicyRequest(
                    domain="rbi",
                    action="start_call",
                    subject="agent-1",
                    resource="call-1",
                    tenant_id="tenant-1",
                    context={"hour": 21, "call_id": "call-1"},
                ),
                PolicyOutcome.DENY,
            ),
            (
                PolicyRequest(
                    domain="rbi",
                    action="start_call",
                    subject="agent-1",
                    resource="call-2",
                    tenant_id="tenant-1",
                    context={
                        "hour": 10,
                        "call_id": "call-2",
                        "recording_consent": True,
                        "disclosure_given": True,
                        "turn_index": 1,
                    },
                ),
                PolicyOutcome.PERMIT,
            ),
            # CALLING_FREQUENCY
            (
                PolicyRequest(
                    domain="rbi",
                    action="start_call",
                    subject="agent-1",
                    resource="call-3",
                    tenant_id="tenant-1",
                    context={"hour": 10, "calls_today_count": 3, "call_id": "call-3"},
                ),
                PolicyOutcome.DENY,
            ),
            # ABUSE_PROHIBITION
            (
                PolicyRequest(
                    domain="rbi",
                    action="say",
                    subject="agent-1",
                    resource="call-4",
                    tenant_id="tenant-1",
                    context={"hour": 10, "utterance_classification": "threatening", "call_id": "call-4"},
                ),
                PolicyOutcome.FORBID,
            ),
            # IDENTITY_VERIFY_FIRST
            (
                PolicyRequest(
                    domain="rbi",
                    action="disclose_debt",
                    subject="agent-1",
                    resource="call-5",
                    tenant_id="tenant-1",
                    context={"hour": 10, "identity_verified": False, "call_id": "call-5"},
                ),
                PolicyOutcome.REQUIRE,
            ),
            # DISCLOSURE_REQUIRED
            (
                PolicyRequest(
                    domain="rbi",
                    action="start_call",
                    subject="agent-1",
                    resource="call-6",
                    tenant_id="tenant-1",
                    context={"hour": 10, "turn_index": 0, "disclosure_given": False, "call_id": "call-6"},
                ),
                PolicyOutcome.REQUIRE,
            ),
            # RECORDING_CONSENT
            (
                PolicyRequest(
                    domain="rbi",
                    action="start_call",
                    subject="agent-1",
                    resource="call-7",
                    tenant_id="tenant-1",
                    context={"hour": 10, "recording_consent": False, "call_id": "call-7"},
                ),
                PolicyOutcome.REQUIRE,
            ),
        ]

        results = [(request, engine.evaluate(request)) for request, _expected in scenarios]

        passed = sum(
            1 for (_, expected), (_, decision) in zip(scenarios, results, strict=True) if decision.outcome == expected
        )
        assert passed == len(scenarios), f"RBI compliance suite: {passed}/{len(scenarios)} scenarios passed"

        # Every non-PERMIT decision above must have produced an audit event.
        replayed = bus.replay_from()
        non_permit_count = sum(1 for _, expected in scenarios if expected != PolicyOutcome.PERMIT)
        assert len(replayed) == non_permit_count
        assert all(envelope.event_type == POLICY_DECISION_MADE_EVENT_TYPE for _entry_id, envelope in replayed)


class TestPolicyEngineLatency:
    def test_policy_evaluation_p99_under_10ms_cached(self, fake_redis: FakeRedisClient) -> None:
        """V4 Ch4 §4.14 / Sprint-017 AC: p99 evaluation latency < 10ms with cached rules."""
        engine = PolicyEngine(redis=fake_redis)
        request = PolicyRequest(
            domain="rbi", action="start_call", subject="agent-1", resource="call-1", context={"hour": 10}
        )

        engine.evaluate(request)  # warm the cache

        durations_ms: list[float] = []
        for _ in range(200):
            start = time.perf_counter()
            engine.evaluate(request)
            durations_ms.append((time.perf_counter() - start) * 1000)

        durations_ms.sort()
        p99 = durations_ms[int(len(durations_ms) * 0.99) - 1]
        assert p99 < 10.0, f"p99 evaluation latency {p99:.3f}ms exceeds the 10ms budget"
