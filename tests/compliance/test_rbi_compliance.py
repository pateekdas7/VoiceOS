"""RBI compliance automated test suite -- Sprint-028 "5. Compliance Validation" (V4 Ch2, Sprint-028).

Runs entirely in-process against a real ``PolicyEngine`` instantiated with
no Redis/Postgres backends (``FakePolicyEngine`` below) -- Sprint-028.md's
own Phase 1 "PolicyEngine: FakePolicyEngine -- Compliance test suite uses
mock policy responses" mock-backend note. This reuses the real,
already-tested RBI rule-evaluation logic (``src.services.policy_engine``)
rather than re-implementing a parallel fake ruleset that could silently
drift from production behavior.

Scenarios (Sprint-028.md literal spec):
  - RBI calling hours: 50 test calls attempted outside 08:00-20:00 -> all blocked
  - RBI frequency: customer with 3 calls today -> 4th call blocked
  - Recording disclosure: verify first utterance includes disclosure phrase
"""

from __future__ import annotations

import pytest

from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.rule import PolicyRequest


def FakePolicyEngine() -> PolicyEngine:  # noqa: N802 -- intentionally PascalCase, a factory standing in for a class
    """A real ``PolicyEngine`` with every backend (Redis/Postgres/EventBus) omitted.

    Falls through directly to the built-in rule packs' deterministic
    condition evaluation -- no network I/O, no test infrastructure
    required, matching Sprint-028.md's own "FakePolicyEngine" mock-backend
    name while never risking rule-logic drift from the real engine.
    """
    return PolicyEngine()


_OUTSIDE_HOURS = (0, 1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
"""RBI permitted calling window is 08:00-20:00 -- every other hour is out of bounds."""


class TestRBICallingHours:
    """50 test calls attempted outside 08:00-20:00 -> all must be blocked."""

    def test_fifty_out_of_hours_calls_all_blocked(self) -> None:
        engine = FakePolicyEngine()
        results: list[PolicyOutcome] = []

        for i in range(50):
            hour = _OUTSIDE_HOURS[i % len(_OUTSIDE_HOURS)]
            request = PolicyRequest(
                domain="rbi",
                action="admit_call",
                subject="compliance_suite",
                resource=f"call-{i}",
                context={"hour": hour, "call_id": f"call-{i}"},
            )
            decision = engine.evaluate(request)
            results.append(decision.outcome)

        assert len(results) == 50
        assert all(outcome in (PolicyOutcome.DENY, PolicyOutcome.FORBID) for outcome in results), (
            f"expected all 50 out-of-hours calls blocked, got outcomes: {results}"
        )

    @pytest.mark.parametrize("hour", range(8, 20))
    def test_in_hours_calls_are_permitted(self, hour: int) -> None:
        """Sanity check: the calling-hours rule is genuinely hour-scoped, not a blanket deny."""
        engine = FakePolicyEngine()
        request = PolicyRequest(
            domain="rbi", action="admit_call", subject="compliance_suite", resource="call-x", context={"hour": hour}
        )
        decision = engine.evaluate(request)
        assert decision.outcome == PolicyOutcome.PERMIT


class TestRBICallingFrequency:
    """A customer with 3 calls already made today -> the 4th call attempt must be blocked."""

    def test_fourth_call_today_is_blocked(self) -> None:
        engine = FakePolicyEngine()
        request = PolicyRequest(
            domain="rbi",
            action="admit_call",
            subject="compliance_suite",
            resource="call-4",
            context={"hour": 12, "calls_today_count": 3},
        )
        decision = engine.evaluate(request)
        assert decision.outcome in (PolicyOutcome.DENY, PolicyOutcome.FORBID)

    @pytest.mark.parametrize("calls_today_count", [0, 1, 2])
    def test_calls_under_the_daily_cap_are_permitted(self, calls_today_count: int) -> None:
        engine = FakePolicyEngine()
        request = PolicyRequest(
            domain="rbi",
            action="admit_call",
            subject="compliance_suite",
            resource="call-n",
            context={"hour": 12, "calls_today_count": calls_today_count},
        )
        decision = engine.evaluate(request)
        assert decision.outcome == PolicyOutcome.PERMIT


class TestRBIRecordingDisclosure:
    """The call-opening script must disclose recording/agent-identity before any debt discussion."""

    def test_disclosure_required_before_disclosure_given(self) -> None:
        engine = FakePolicyEngine()
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject="compliance_suite",
            resource="call-1",
            context={"turn_index": 0, "disclosure_given": False},
        )
        decision = engine.evaluate(request)
        assert decision.outcome != PolicyOutcome.PERMIT

    def test_disclosure_satisfied_once_given(self) -> None:
        engine = FakePolicyEngine()
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject="compliance_suite",
            resource="call-1",
            context={"turn_index": 0, "disclosure_given": True, "recording_consent": True},
        )
        decision = engine.evaluate(request)
        assert decision.outcome == PolicyOutcome.PERMIT

    def test_recording_consent_required_when_missing(self) -> None:
        engine = FakePolicyEngine()
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject="compliance_suite",
            resource="call-1",
            context={"turn_index": 0, "disclosure_given": True, "recording_consent": False},
        )
        decision = engine.evaluate(request)
        assert decision.outcome != PolicyOutcome.PERMIT
