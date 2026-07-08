"""RBI scheduling compliance suite (Sprint-023): ``ScheduleEngine`` against the
*real* ``PolicyEngine``/``RBIPolicyPack`` (in-process — no Postgres/Redis
required, since ``PolicyEngine()`` with no backends wired evaluates every
built-in rule globally, per its own docstring). Placed under
``tests/integration/`` per Sprint-023.md's file layout, even though it needs
no external infrastructure — it is a genuine integration test of
``ScheduleEngine`` + the real Policy Engine + the real RBI pack working
together, not a unit test against a fake.

Required named test: ``test_rbi_scheduling_compliance_suite`` — 20 scenarios,
all fail/pass as expected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.libs.contracts.models.campaign import RetryPolicy
from src.libs.contracts.primitives import TenantId
from src.services.campaign_management.scheduler import ScheduleEngine
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService

TENANT = TenantId("tenant-a")


def _engine() -> ScheduleEngine:
    return ScheduleEngine(PolicyEngineService(PolicyEngine()))


# Each scenario: (hour, calls_today_count, attempt_count, retry_policy, expect_permitted)
_SCENARIOS: tuple[tuple[int, int, int, RetryPolicy | None, bool], ...] = (
    (8, 0, 0, None, True),  # window open edge
    (19, 0, 0, None, True),  # window close edge (exclusive of 20)
    (12, 0, 0, None, True),  # midday
    (7, 0, 0, None, False),  # before window
    (20, 0, 0, None, False),  # exactly at window end -> denied
    (21, 0, 0, None, False),  # AC: 21:00 -> None
    (0, 0, 0, None, False),  # midnight
    (23, 0, 0, None, False),  # late night
    (10, 3, 0, None, False),  # AC: 3 calls already today -> None
    (10, 4, 0, None, False),  # over frequency cap
    (10, 2, 0, None, True),  # just under frequency cap
    (10, 0, 0, None, True),  # first call of day
    (9, 1, 0, None, True),
    (18, 2, 0, None, True),
    (19, 3, 0, None, False),  # both hour-ok and frequency-denied -> still denied
    (10, 0, 3, RetryPolicy(max_attempts=3), False),  # max attempts exhausted
    (10, 0, 2, RetryPolicy(max_attempts=3), True),  # one attempt remaining
    (10, 0, 0, RetryPolicy(max_attempts=1), True),  # first of a single-attempt policy
    (10, 0, 1, RetryPolicy(max_attempts=1), False),  # single-attempt policy exhausted
    (13, 1, 1, RetryPolicy(max_attempts=5), True),
)


class TestRBISchedulingComplianceSuite:
    def test_rbi_scheduling_compliance_suite(self) -> None:
        """20 scheduling scenarios against the real Policy Engine, all fail/pass as expected."""
        assert len(_SCENARIOS) == 20
        engine = _engine()
        failures: list[str] = []
        for hour, calls_today, attempt_count, retry_policy, expect_permitted in _SCENARIOS:
            result = engine.schedule_next_call(
                TENANT,
                "cust-1",
                "camp-1",
                hour=hour,
                calls_today_count=calls_today,
                attempt_count=attempt_count,
                retry_policy=retry_policy,
            )
            permitted = result is not None
            if permitted != expect_permitted:
                failures.append(
                    f"hour={hour} calls_today={calls_today} attempt_count={attempt_count} "
                    f"retry_policy={retry_policy}: expected permitted={expect_permitted}, got {permitted}"
                )
        assert not failures, "RBI compliance suite failures:\n" + "\n".join(failures)

    @pytest.mark.parametrize("hour,calls_today,attempt_count,retry_policy,expect_permitted", _SCENARIOS)
    def test_each_scenario_individually(
        self, hour: int, calls_today: int, attempt_count: int, retry_policy: RetryPolicy | None, expect_permitted: bool
    ) -> None:
        engine = _engine()
        result = engine.schedule_next_call(
            TENANT,
            "cust-1",
            "camp-1",
            hour=hour,
            calls_today_count=calls_today,
            attempt_count=attempt_count,
            retry_policy=retry_policy,
        )
        assert (result is not None) == expect_permitted

    def test_retry_eligible_at_respects_interval(self) -> None:
        engine = _engine()
        policy = RetryPolicy(max_attempts=3, retry_interval_hours=24)
        last_attempt = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
        result = engine.schedule_next_call(
            TENANT,
            "cust-1",
            "camp-1",
            hour=10,
            attempt_count=1,
            retry_policy=policy,
            last_attempt_at=last_attempt,
        )
        assert result == last_attempt + timedelta(hours=24)
