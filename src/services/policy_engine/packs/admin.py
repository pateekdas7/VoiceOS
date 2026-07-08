"""AdminPolicyPack — Administration Portal mutation-gating rules (V5 Ch13, Sprint-025).

Routes the Admin Portal's "is PolicyEngine validated where applicable"
requirement through the same PERMIT/DENY DSL every other domain uses,
rather than the campaign-approval workflow hand-rolling its own state check.
Sprint-025.md's own workflow is "submit_for_review then approve" — this
pack is what makes "approve" refuse a campaign that skipped review a PDP
decision rather than an ad hoc `if` in ``CampaignAdminController``.

Context keys consumed:
    campaign_status (str): the campaign's ``CampaignStatus`` value at the
        moment approval is requested. Resolved by the caller
        (``CampaignAdminController.approve``) — the Policy Engine has no
        direct dependency on ``src.services.campaign_management`` (same
        caller-resolves-the-fact precedent as ``tenant_active`` in
        ``packs/saas.py``).

Architecture: V5 Ch13 (Administration Portal); V4 Ch4 (Policy Engine).
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule

_REVIEWED_STATUS = "REVIEW"


def _campaign_not_reviewed(request: PolicyRequest) -> bool:
    return request.context.get("campaign_status") != _REVIEWED_STATUS


class AdminPolicyPack:
    """Administration Portal mutation-gating rules (V5 Ch13)."""

    CAMPAIGN_APPROVAL_REQUIRES_REVIEW: PolicyRule = PolicyRule(
        rule_id="ADMIN-CAMPAIGN-APPROVAL-REQUIRES-REVIEW",
        pack="admin",
        domain="campaign_admin",
        condition=PolicyCondition(
            "campaign has not been submitted for review",
            _campaign_not_reviewed,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="An admin may not approve a campaign that has not first been submitted for review.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (CAMPAIGN_APPROVAL_REQUIRES_REVIEW,)

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All Admin Portal mutation-gating rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
