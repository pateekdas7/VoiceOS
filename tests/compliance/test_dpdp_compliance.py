"""Automated DPDP (Digital Personal Data Protection Act) compliance validation suite
(Sprint-028, V4 Ch2).

Tests the real ``DPDPPolicyPack`` rules against ``PolicyRequest`` fixtures — no
live services required.  These are the automated scenarios that must pass 100 %
before the production alpha deploy (Deliverable 5 in Sprint-028).

DPDP scenarios covered:
    ✓ Consent gate: 10 customers without consent → data processing blocked for all
    ✓ Consent gate: customers with consent → data processing permitted
    ✓ Purpose scope: processing for non-consented purpose → blocked
    ✓ Purpose scope: consented purpose → permitted
    ✓ Data retention: data past retention limit → processing blocked
    ✓ Data retention: data within limit → permitted
    ✓ Right to erasure: erasure requested → all processing blocked
"""

from __future__ import annotations

import pytest

from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.packs.dpdp import DEFAULT_RETENTION_LIMIT_DAYS, DPDPPolicyPack
from src.services.policy_engine.rule import PolicyRequest

_SUBJECT = "voiceos-agent"
_TENANT = "tenant-dpdp-test"

# 10 test customer IDs (Sprint-028 AC: 10 test customers without consent → all blocked)
_TEST_CUSTOMERS = [f"customer:dpdp-test-{i:03d}" for i in range(1, 11)]


def _data_request(
    *,
    resource: str = "customer:dpdp-test-001",
    has_consent: bool = True,
    purpose: str = "collections_call",
    consented_purposes: tuple[str, ...] = ("collections_call",),
    data_age_days: int = 0,
    retention_limit_days: int = DEFAULT_RETENTION_LIMIT_DAYS,
    erasure_requested: bool = False,
    action: str = "process_customer_data",
) -> PolicyRequest:
    return PolicyRequest(
        domain="dpdp",
        action=action,
        subject=_SUBJECT,
        resource=resource,
        tenant_id=_TENANT,
        context={
            "has_consent": has_consent,
            "purpose": purpose,
            "consented_purposes": consented_purposes,
            "data_age_days": data_age_days,
            "retention_limit_days": retention_limit_days,
            "erasure_requested": erasure_requested,
        },
    )


# ---------------------------------------------------------------------------
# Consent gate
# ---------------------------------------------------------------------------


class TestDPDPConsentGate:
    @pytest.mark.parametrize("resource", _TEST_CUSTOMERS)
    def test_10_customers_without_consent_all_blocked(self, resource: str) -> None:
        """Sprint-028 AC: 10 test customers without consent → all calls blocked."""
        request = _data_request(resource=resource, has_consent=False)
        outcome = DPDPPolicyPack.CONSENT_REQUIRED_FOR_PROCESSING.decide(request)
        assert outcome == PolicyOutcome.DENY, f"Customer {resource!r} has no consent — data processing must be denied"

    @pytest.mark.parametrize("resource", _TEST_CUSTOMERS)
    def test_customers_with_consent_permitted(self, resource: str) -> None:
        request = _data_request(resource=resource, has_consent=True)
        outcome = DPDPPolicyPack.CONSENT_REQUIRED_FOR_PROCESSING.decide(request)
        assert outcome == PolicyOutcome.PERMIT

    def test_hard_rule_flag(self) -> None:
        assert DPDPPolicyPack.CONSENT_REQUIRED_FOR_PROCESSING.hard_rule is True


# ---------------------------------------------------------------------------
# Purpose scope
# ---------------------------------------------------------------------------


class TestDPDPPurposeScope:
    def test_non_consented_purpose_blocked(self) -> None:
        request = _data_request(
            has_consent=True,
            purpose="marketing_call",
            consented_purposes=("collections_call",),
            action="process_customer_data",
        )
        outcome = DPDPPolicyPack.PURPOSE_LIMITATION.decide(request)
        assert outcome == PolicyOutcome.DENY

    def test_consented_purpose_permitted(self) -> None:
        request = _data_request(
            has_consent=True,
            purpose="collections_call",
            consented_purposes=("collections_call", "payment_reminder"),
            action="process_customer_data",
        )
        outcome = DPDPPolicyPack.PURPOSE_LIMITATION.decide(request)
        assert outcome == PolicyOutcome.PERMIT

    def test_multiple_consented_purposes_all_permitted(self) -> None:
        for purpose in ("collections_call", "payment_reminder", "account_update"):
            request = _data_request(
                has_consent=True,
                purpose=purpose,
                consented_purposes=("collections_call", "payment_reminder", "account_update"),
            )
            outcome = DPDPPolicyPack.PURPOSE_LIMITATION.decide(request)
            assert outcome == PolicyOutcome.PERMIT, f"Purpose {purpose!r} must be permitted"

    def test_no_purpose_specified_permits(self) -> None:
        """If ``purpose`` key is missing from context, the rule does not fire."""
        request = PolicyRequest(
            domain="dpdp",
            action="process_customer_data",
            subject=_SUBJECT,
            resource="customer:x",
            context={"has_consent": True, "consented_purposes": ("collections_call",)},
        )
        outcome = DPDPPolicyPack.PURPOSE_LIMITATION.decide(request)
        assert outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# Data retention
# ---------------------------------------------------------------------------


class TestDPDPRetentionLimit:
    def test_data_past_retention_limit_blocked(self) -> None:
        request = _data_request(
            data_age_days=DEFAULT_RETENTION_LIMIT_DAYS + 1,
            retention_limit_days=DEFAULT_RETENTION_LIMIT_DAYS,
        )
        outcome = DPDPPolicyPack.RETENTION_SCHEDULE.decide(request)
        assert outcome == PolicyOutcome.REQUIRE

    def test_data_within_limit_permitted(self) -> None:
        request = _data_request(
            data_age_days=DEFAULT_RETENTION_LIMIT_DAYS - 1,
            retention_limit_days=DEFAULT_RETENTION_LIMIT_DAYS,
        )
        outcome = DPDPPolicyPack.RETENTION_SCHEDULE.decide(request)
        assert outcome == PolicyOutcome.PERMIT

    def test_data_at_exact_limit_permitted(self) -> None:
        request = _data_request(
            data_age_days=DEFAULT_RETENTION_LIMIT_DAYS,
            retention_limit_days=DEFAULT_RETENTION_LIMIT_DAYS,
        )
        outcome = DPDPPolicyPack.RETENTION_SCHEDULE.decide(request)
        assert outcome == PolicyOutcome.PERMIT

    def test_custom_retention_limit_respected(self) -> None:
        custom_limit = 365
        request = _data_request(data_age_days=custom_limit + 1, retention_limit_days=custom_limit)
        outcome = DPDPPolicyPack.RETENTION_SCHEDULE.decide(request)
        assert outcome == PolicyOutcome.REQUIRE

    def test_fresh_data_always_permitted(self) -> None:
        request = _data_request(data_age_days=0)
        outcome = DPDPPolicyPack.RETENTION_SCHEDULE.decide(request)
        assert outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# Right to erasure
# ---------------------------------------------------------------------------


class TestDPDPRightToErasure:
    def test_erasure_requested_denies_processing(self) -> None:
        request = _data_request(erasure_requested=True)
        outcome = DPDPPolicyPack.ERASURE_HONOR.decide(request)
        assert outcome == PolicyOutcome.REQUIRE

    def test_no_erasure_request_permits(self) -> None:
        request = _data_request(erasure_requested=False)
        outcome = DPDPPolicyPack.ERASURE_HONOR.decide(request)
        assert outcome == PolicyOutcome.PERMIT

    def test_hard_rule_flag(self) -> None:
        assert DPDPPolicyPack.ERASURE_HONOR.hard_rule is True


# ---------------------------------------------------------------------------
# DPDP pack completeness
# ---------------------------------------------------------------------------


class TestDPDPPolicyPackCompleteness:
    def test_all_required_rules_registered(self) -> None:
        rules = DPDPPolicyPack.rules()
        rule_ids = {r.rule_id for r in rules}
        required = {
            "DPDP-CONSENT-REQUIRED",
            "DPDP-PURPOSE-LIMITATION",
            "DPDP-RETENTION-SCHEDULE",
            "DPDP-ERASURE-HONOR",
        }
        assert required.issubset(rule_ids)

    def test_all_rules_are_hard_rules(self) -> None:
        for rule in DPDPPolicyPack.rules():
            assert rule.hard_rule is True, f"DPDP rule {rule.rule_id} must be a hard rule"

    def test_default_retention_limit_is_7_years(self) -> None:
        assert DEFAULT_RETENTION_LIMIT_DAYS >= 365 * 7
