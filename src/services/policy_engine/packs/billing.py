"""BillingPolicyPack — entitlement/usage-limit admission rules (V5 Ch9, Sprint-024).

Routes every feature-access/usage check through the same PERMIT/DENY DSL
every other domain uses (V4 Ch4 §4.10), rather than ``EntitlementEngine``
hand-rolling its own if/else limit checks — this is what "entitlement
enforcement is performed exclusively through PolicyEngine" (Sprint-024 DoD)
means mechanically.

Context keys consumed:
    usage_quantity / usage_limit (int | None): the caller (``EntitlementEngine``/
        ``UsageLimitEnforcer``) resolves current-period usage and the
        subscription tier's limit for that usage dimension *before* calling
        the PDP — the Policy Engine itself has no direct dependency on
        ``src.services.billing`` or ``src.services.metering`` (same
        caller-resolves-the-fact precedent as ``calls_today_count`` in
        ``packs/rbi.py`` and ``tenant_active`` in ``packs/saas.py``).
        ``usage_limit=None`` means unlimited (e.g. ENTERPRISE contract tiers).
    tier (str): the subscription's ``SubscriptionTier`` value.
    trial_expired (bool): whether a TRIAL subscription's 30-day window has elapsed.

Architecture: V5 Ch9 (Billing Platform — entitlements via Policy Engine).
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule


def _usage_limit_exceeded(request: PolicyRequest) -> bool:
    quantity = request.context.get("usage_quantity")
    limit = request.context.get("usage_limit")
    if quantity is None or limit is None:
        return False
    return bool(quantity >= limit)


def _trial_expired(request: PolicyRequest) -> bool:
    return request.context.get("tier") == "TRIAL" and request.context.get("trial_expired", False) is True


class BillingPolicyPack:
    """Entitlement/usage-limit admission rules (V5 Ch9)."""

    USAGE_LIMIT_EXCEEDED: PolicyRule = PolicyRule(
        rule_id="BILLING-USAGE-LIMIT-EXCEEDED",
        pack="billing",
        domain="billing",
        condition=PolicyCondition(
            "current-period usage has reached the subscription tier's limit",
            _usage_limit_exceeded,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="A tenant may not consume a billable feature beyond its tier's usage limit.",
    )

    TRIAL_EXPIRED: PolicyRule = PolicyRule(
        rule_id="BILLING-TRIAL-EXPIRED",
        pack="billing",
        domain="billing",
        condition=PolicyCondition(
            "TRIAL subscription's 30-day window has elapsed",
            _trial_expired,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="A TRIAL tenant may not use billable features once the trial period has expired.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (USAGE_LIMIT_EXCEEDED, TRIAL_EXPIRED)

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All billing entitlement rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
