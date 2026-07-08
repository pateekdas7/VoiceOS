"""CampaignLifecycle — the campaign state machine (Sprint-023.md).

DRAFT -> REVIEW -> APPROVED -> ACTIVE -> PAUSED -> COMPLETED -> ARCHIVED

Architecture: V5 Ch6 (Campaign Management).
"""

from __future__ import annotations

from src.libs.contracts.models.campaign import CampaignStatus


class CampaignLifecycleError(ValueError):
    """An invalid campaign lifecycle transition was attempted."""


_ALLOWED_TRANSITIONS: dict[CampaignStatus, frozenset[CampaignStatus]] = {
    CampaignStatus.DRAFT: frozenset({CampaignStatus.REVIEW}),
    CampaignStatus.REVIEW: frozenset({CampaignStatus.APPROVED, CampaignStatus.DRAFT}),
    CampaignStatus.APPROVED: frozenset({CampaignStatus.ACTIVE, CampaignStatus.DRAFT}),
    CampaignStatus.ACTIVE: frozenset({CampaignStatus.PAUSED, CampaignStatus.COMPLETED}),
    CampaignStatus.PAUSED: frozenset({CampaignStatus.ACTIVE, CampaignStatus.COMPLETED}),
    CampaignStatus.COMPLETED: frozenset({CampaignStatus.ARCHIVED}),
    CampaignStatus.ARCHIVED: frozenset(),
}


class CampaignLifecycle:
    """Validates campaign lifecycle transitions (Sprint-023 AC: "cannot be ACTIVE without APPROVED first")."""

    @staticmethod
    def validate_transition(current: CampaignStatus, target: CampaignStatus) -> None:
        """Raise :class:`CampaignLifecycleError` if ``current`` -> ``target`` is not permitted."""
        allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise CampaignLifecycleError(
                f"invalid campaign lifecycle transition: {current.value} -> {target.value} "
                f"(allowed from {current.value}: {sorted(s.value for s in allowed) or 'none'})"
            )

    @staticmethod
    def can_transition(current: CampaignStatus, target: CampaignStatus) -> bool:
        return target in _ALLOWED_TRANSITIONS.get(current, frozenset())
