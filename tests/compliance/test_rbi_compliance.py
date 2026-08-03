"""Automated RBI Fair Practice Code compliance validation suite (Sprint-028, V4 Ch2).

Tests the real ``RBIPolicyPack`` rules against ``PolicyRequest`` fixtures — no
live services required.  These are the automated scenarios that must pass 100 %
before the production alpha deploy (Deliverable 5 in Sprint-028).

RBI scenarios covered:
    ✓ Calling hours: 50 test calls outside 08:00-20:00 -> all blocked
    ✓ Calling hours: calls inside 08:00-20:00 -> all permitted
    ✓ Frequency: 3 calls today → 4th call blocked
    ✓ Frequency: ≤ 3 calls today → permitted
    ✓ Abuse prohibition: abusive/threatening utterance → forbidden
    ✓ Identity verification: debt disclosure before identity check → requires verification
    ✓ Disclosure: missing disclosure at call turn 0 → obligations required
    ✓ Recording consent: missing consent on start_call → obligations required
"""

from __future__ import annotations

import pytest

from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.packs.rbi import (
    CALLING_WINDOW_END_HOUR,
    CALLING_WINDOW_START_HOUR,
    MAX_CALLS_PER_DAY,
    RBIPolicyPack,
)
from src.services.policy_engine.rule import PolicyRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUBJECT = "voiceos-agent"
_RESOURCE = "customer:test-001"
_TENANT = "tenant-rbi-test"


def _call_request(
    *,
    hour: int,
    calls_today_count: int = 0,
    action: str = "admit_call",
    **extra_ctx: object,
) -> PolicyRequest:
    return PolicyRequest(
        domain="rbi",
        action=action,
        subject=_SUBJECT,
        resource=_RESOURCE,
        tenant_id=_TENANT,
        context={"hour": hour, "calls_today_count": calls_today_count, **extra_ctx},
    )


# ---------------------------------------------------------------------------
# Calling hours
# ---------------------------------------------------------------------------


_OUTSIDE_HOURS = list(range(0, CALLING_WINDOW_START_HOUR)) + list(range(CALLING_WINDOW_END_HOUR, 24))
_INSIDE_HOURS = list(range(CALLING_WINDOW_START_HOUR, CALLING_WINDOW_END_HOUR))

_FIFTY_OUTSIDE_HOURS = (_OUTSIDE_HOURS * 4)[:50]


class TestRBICallingHours:
    @pytest.mark.parametrize("hour", _FIFTY_OUTSIDE_HOURS)
    def test_call_blocked_outside_hours(self, hour: int) -> None:
        """Sprint-028 AC: 50 test calls outside 08:00-20:00 -> all blocked."""
        request = _call_request(hour=hour)
        outcome = RBIPolicyPack.CALLING_HOURS.decide(request)
        assert outcome == PolicyOutcome.DENY, f"Hour {hour:02d}:00 is outside calling window — call must be blocked"

    @pytest.mark.parametrize("hour", _INSIDE_HOURS)
    def test_call_permitted_inside_hours(self, hour: int) -> None:
        request = _call_request(hour=hour)
        outcome = RBIPolicyPack.CALLING_HOURS.decide(request)
        assert outcome == PolicyOutcome.PERMIT, f"Hour {hour:02d}:00 is inside calling window — call must be permitted"

    def test_boundary_hour_8_is_inside(self) -> None:
        outcome = RBIPolicyPack.CALLING_HOURS.decide(_call_request(hour=8))
        assert outcome == PolicyOutcome.PERMIT

    def test_boundary_hour_20_is_outside(self) -> None:
        outcome = RBIPolicyPack.CALLING_HOURS.decide(_call_request(hour=20))
        assert outcome == PolicyOutcome.DENY

    def test_midnight_blocked(self) -> None:
        outcome = RBIPolicyPack.CALLING_HOURS.decide(_call_request(hour=0))
        assert outcome == PolicyOutcome.DENY

    def test_late_night_blocked(self) -> None:
        outcome = RBIPolicyPack.CALLING_HOURS.decide(_call_request(hour=23))
        assert outcome == PolicyOutcome.DENY

    def test_hard_rule_flag(self) -> None:
        assert RBIPolicyPack.CALLING_HOURS.hard_rule is True


# ---------------------------------------------------------------------------
# Calling frequency
# ---------------------------------------------------------------------------


class TestRBICallingFrequency:
    def test_fourth_call_blocked_when_three_already_placed(self) -> None:
        """Sprint-028 AC: customer with 3 calls today → 4th call blocked."""
        request = _call_request(hour=10, calls_today_count=MAX_CALLS_PER_DAY)
        outcome = RBIPolicyPack.CALLING_FREQUENCY.decide(request)
        assert outcome == PolicyOutcome.DENY

    @pytest.mark.parametrize("count", range(MAX_CALLS_PER_DAY))
    def test_permitted_below_daily_limit(self, count: int) -> None:
        request = _call_request(hour=10, calls_today_count=count)
        outcome = RBIPolicyPack.CALLING_FREQUENCY.decide(request)
        assert outcome == PolicyOutcome.PERMIT

    def test_well_above_limit_is_blocked(self) -> None:
        request = _call_request(hour=10, calls_today_count=MAX_CALLS_PER_DAY + 5)
        outcome = RBIPolicyPack.CALLING_FREQUENCY.decide(request)
        assert outcome == PolicyOutcome.DENY

    def test_hard_rule_flag(self) -> None:
        assert RBIPolicyPack.CALLING_FREQUENCY.hard_rule is True


# ---------------------------------------------------------------------------
# Abuse prohibition
# ---------------------------------------------------------------------------


class TestRBIAbuseProhibition:
    @pytest.mark.parametrize("classification", ["abusive", "threatening"])
    def test_abusive_utterance_forbidden(self, classification: str) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="continue_call",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "utterance_classification": classification},
        )
        outcome = RBIPolicyPack.ABUSE_PROHIBITION.decide(request)
        assert outcome == PolicyOutcome.FORBID

    def test_neutral_utterance_permitted(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="continue_call",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "utterance_classification": "neutral"},
        )
        outcome = RBIPolicyPack.ABUSE_PROHIBITION.decide(request)
        assert outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# Identity verification
# ---------------------------------------------------------------------------


class TestRBIIdentityVerification:
    def test_debt_disclosure_blocked_without_identity_check(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="disclose_debt",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "identity_verified": False},
        )
        outcome = RBIPolicyPack.IDENTITY_VERIFY_FIRST.decide(request)
        assert outcome == PolicyOutcome.REQUIRE

    def test_debt_disclosure_permitted_with_identity_check(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="disclose_debt",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "identity_verified": True},
        )
        outcome = RBIPolicyPack.IDENTITY_VERIFY_FIRST.decide(request)
        assert outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------


class TestRBIDisclosureRequired:
    def test_missing_disclosure_at_turn_zero_requires_obligations(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "turn_index": 0, "disclosure_given": False},
        )
        outcome = RBIPolicyPack.DISCLOSURE_REQUIRED.decide(request)
        assert outcome == PolicyOutcome.REQUIRE

    def test_disclosure_already_given_permits(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "turn_index": 0, "disclosure_given": True},
        )
        outcome = RBIPolicyPack.DISCLOSURE_REQUIRED.decide(request)
        assert outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# Recording consent
# ---------------------------------------------------------------------------


class TestRBIRecordingConsent:
    def test_missing_consent_requires_capture(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "recording_consent": False},
        )
        outcome = RBIPolicyPack.RECORDING_CONSENT.decide(request)
        assert outcome == PolicyOutcome.REQUIRE

    def test_consent_captured_permits(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject=_SUBJECT,
            resource=_RESOURCE,
            context={"hour": 10, "recording_consent": True},
        )
        outcome = RBIPolicyPack.RECORDING_CONSENT.decide(request)
        assert outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# All rules present
# ---------------------------------------------------------------------------


class TestRBIPolicyPackCompleteness:
    def test_all_rules_registered(self) -> None:
        rules = RBIPolicyPack.rules()
        rule_ids = {r.rule_id for r in rules}
        required = {
            "RBI-CALLING-HOURS",
            "RBI-CALLING-FREQUENCY",
            "RBI-ABUSE-PROHIBITION",
            "RBI-IDENTITY-VERIFY-FIRST",
            "RBI-DISCLOSURE-REQUIRED",
            "RBI-RECORDING-CONSENT",
        }
        assert required.issubset(rule_ids)

    def test_all_rules_are_hard_rules(self) -> None:
        for rule in RBIPolicyPack.rules():
            assert rule.hard_rule is True, f"RBI rule {rule.rule_id} must be a hard rule"
