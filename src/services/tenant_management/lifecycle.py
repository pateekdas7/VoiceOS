"""TenantLifecycle — the tenant lifecycle state machine (V5 Ch3).

Valid transition graph (Sprint-021.md, V5 Ch3)::

    TRIAL -> SANDBOX -> PRODUCTION -> SUSPENDED -> CANCELLED -> DELETING -> DELETED
                          ^_________________|
                              (reactivate)

Every forward step in the main chain is a valid transition. The single back
edge, ``SUSPENDED -> PRODUCTION``, is the "reactivate" transition
``TenantSuspender`` uses to un-suspend a tenant. Any other pair (including
same-state and any skip-ahead/skip-back combination not listed here, e.g.
``DELETED -> PRODUCTION``) raises ``InvalidTenantTransitionError``.

Architecture: V5 Ch3 (Tenant Lifecycle).
"""

from __future__ import annotations

from src.libs.contracts.models.tenant import TenantStatus


class InvalidTenantTransitionError(Exception):
    """Raised when a requested tenant status transition is not in the valid graph."""

    def __init__(self, current: TenantStatus, target: TenantStatus) -> None:
        super().__init__(f"invalid tenant lifecycle transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


_VALID_TRANSITIONS: dict[TenantStatus, frozenset[TenantStatus]] = {
    TenantStatus.TRIAL: frozenset({TenantStatus.SANDBOX}),
    TenantStatus.SANDBOX: frozenset({TenantStatus.PRODUCTION}),
    TenantStatus.PRODUCTION: frozenset({TenantStatus.SUSPENDED}),
    TenantStatus.SUSPENDED: frozenset({TenantStatus.CANCELLED, TenantStatus.PRODUCTION}),
    TenantStatus.CANCELLED: frozenset({TenantStatus.DELETING}),
    TenantStatus.DELETING: frozenset({TenantStatus.DELETED}),
    TenantStatus.DELETED: frozenset(),
}


class TenantLifecycle:
    """Enforces the valid tenant status transition graph."""

    @staticmethod
    def is_valid_transition(current: TenantStatus, target: TenantStatus) -> bool:
        return target in _VALID_TRANSITIONS.get(current, frozenset())

    @classmethod
    def transition(cls, current: TenantStatus, target: TenantStatus) -> TenantStatus:
        """Return ``target`` if the transition is valid; otherwise raise.

        Raises:
            InvalidTenantTransitionError: if ``target`` is not reachable
                from ``current`` in one step.
        """
        if not cls.is_valid_transition(current, target):
            raise InvalidTenantTransitionError(current, target)
        return target
