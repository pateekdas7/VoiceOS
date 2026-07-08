"""DPDPPolicyPack — Digital Personal Data Protection Act rules (V4 Ch2 §"DPDP").

"consent before PII processing (purpose-bound), data-principal rights
(access/correction/erasure), retention limits, breach notification."

Every rule here is a hard rule (``hard_rule=True``) per V4 Ch4 §4.13
``hard_rules_unweakenable: [dpdp, ...]``.

Context keys consumed:
    has_consent (bool): whether a valid consent record exists for the customer.
    erasure_requested (bool): whether a right-to-erasure request is pending.
    purpose (str | None): the purpose this processing action is being performed for.
    consented_purposes (tuple[str, ...]): purposes the customer has consented to.
    data_age_days (int): age of the data record in days.
    retention_limit_days (int): the regulatory/tenant-configured retention window.

Architecture: V4 Ch2 (Regulatory Compliance — DPDP); V4 Ch4; V4 Ch5 (Privacy).
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule

DEFAULT_RETENTION_LIMIT_DAYS = 2555
"""~7 years — the regulated audit retention window (V4 Ch11 §"7y")."""


def _processing_without_consent(request: PolicyRequest) -> bool:
    return request.action == "process_customer_data" and not request.context.get("has_consent", False)


def _erasure_requested(request: PolicyRequest) -> bool:
    return bool(request.context.get("erasure_requested", False))


def _purpose_not_consented(request: PolicyRequest) -> bool:
    purpose = request.context.get("purpose")
    if purpose is None:
        return False
    consented_purposes = request.context.get("consented_purposes", ())
    return purpose not in consented_purposes


def _retention_period_exceeded(request: PolicyRequest) -> bool:
    limit = request.context.get("retention_limit_days", DEFAULT_RETENTION_LIMIT_DAYS)
    return bool(request.context.get("data_age_days", 0) > limit)


class DPDPPolicyPack:
    """DPDP Act consent and data-protection rules (V4 Ch2, V4 Ch4)."""

    CONSENT_REQUIRED_FOR_PROCESSING: PolicyRule = PolicyRule(
        rule_id="DPDP-CONSENT-REQUIRED",
        pack="dpdp",
        domain="dpdp",
        condition=PolicyCondition(
            "customer data processing attempted without a valid consent record",
            _processing_without_consent,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="DPDP: customer data may not be processed without valid, current consent.",
        hard_rule=True,
    )

    ERASURE_HONOR: PolicyRule = PolicyRule(
        rule_id="DPDP-ERASURE-HONOR",
        pack="dpdp",
        domain="dpdp",
        condition=PolicyCondition(
            "a right-to-erasure request is pending",
            _erasure_requested,
        ),
        effect=PolicyEffect(PolicyOutcome.REQUIRE, obligations=("process_erasure_request",)),
        description="DPDP §13: a pending erasure request must be honored within the regulatory window.",
        hard_rule=True,
    )

    PURPOSE_LIMITATION: PolicyRule = PolicyRule(
        rule_id="DPDP-PURPOSE-LIMITATION",
        pack="dpdp",
        domain="dpdp",
        condition=PolicyCondition(
            "customer data used for a purpose not covered by consent",
            _purpose_not_consented,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="DPDP: customer data may only be used for consented purposes.",
        hard_rule=True,
    )

    RETENTION_SCHEDULE: PolicyRule = PolicyRule(
        rule_id="DPDP-RETENTION-SCHEDULE",
        pack="dpdp",
        domain="dpdp",
        condition=PolicyCondition(
            "data record is past its retention period",
            _retention_period_exceeded,
        ),
        effect=PolicyEffect(PolicyOutcome.REQUIRE, obligations=("flag_for_deletion",)),
        description="DPDP: data past its retention period must be flagged for deletion.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (
        CONSENT_REQUIRED_FOR_PROCESSING,
        ERASURE_HONOR,
        PURPOSE_LIMITATION,
        RETENTION_SCHEDULE,
    )

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All DPDP rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
