"""FleetRolloutManager -- progressive version rollout via tenant ring assignment (V5 Ch23).

Rings: 1=CANARY, 2=EARLY, 3=GENERAL, 4=LAGGARD (``RolloutRing``). Assignment
is deterministic (SHA-256 hash of ``tenant_id`` mod 4) and sticky: the first
assignment for a tenant is persisted and never recomputed, so a later change
to the hash algorithm cannot silently reshuffle an already-assigned tenant.

Promotion is ring-by-ring with a health gate between each ring (Sprint-026.md:
"new version promoted ring-by-ring with health gate between each ring") --
``promote_ring()`` refuses to advance past a ring whose health check fails.

Architecture: V5 Ch23 (SaaS Operations Platform -- Fleet Version Rollout Rings).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol

from src.libs.contracts.models.saas_ops import FleetVersion, RolloutRing, TenantRolloutAssignment
from src.libs.contracts.primitives import TenantId

from . import metrics

_RING_ORDER: tuple[RolloutRing, ...] = (
    RolloutRing.CANARY,
    RolloutRing.EARLY,
    RolloutRing.GENERAL,
    RolloutRing.LAGGARD,
)


class RolloutHealthGateFailedError(RuntimeError):
    """Raised by :meth:`FleetRolloutManager.promote_ring` when the health gate rejects a promotion."""


class RolloutRepositoryPort(Protocol):
    def get_ring(self, tenant_id: TenantId) -> TenantRolloutAssignment | None: ...
    def assign_ring(self, tenant_id: TenantId, ring: RolloutRing) -> TenantRolloutAssignment: ...
    def get_version_for_ring(self, ring: RolloutRing) -> FleetVersion | None: ...
    def promote_ring(self, ring: RolloutRing, version: str) -> FleetVersion: ...


class FleetRolloutManager:
    """Assigns tenants to rollout rings and promotes versions ring-by-ring."""

    def __init__(self, repository: RolloutRepositoryPort) -> None:
        self._repo = repository

    def assign_rollout_ring(self, tenant_id: TenantId) -> RolloutRing:
        """Return ``tenant_id``'s ring, assigning it (sticky, idempotent) on first use."""
        existing = self._repo.get_ring(tenant_id)
        if existing is not None:
            return existing.ring

        ring = self._compute_ring(tenant_id)
        assignment = self._repo.assign_ring(tenant_id, ring)
        metrics.record_ring_assignment(ring)
        return assignment.ring

    def get_version_for_ring(self, ring: RolloutRing) -> str | None:
        """Return the current target version for ``ring``, or ``None`` if never promoted."""
        version = self._repo.get_version_for_ring(ring)
        return version.version if version is not None else None

    def promote_ring(
        self,
        ring: RolloutRing,
        version: str,
        *,
        health_check: Callable[[RolloutRing], bool] | None = None,
    ) -> FleetVersion:
        """Promote ``version`` to ``ring``, gated on the previous ring's health.

        Every ring after CANARY requires the immediately-preceding ring
        (in ``_RING_ORDER``) to already be on ``version`` and (if
        ``health_check`` is supplied) to pass its health check -- this is
        the "health gate between each ring" progressive-rollout requirement.
        CANARY itself has no preceding ring and always promotes directly.

        Raises:
            RolloutHealthGateFailedError: if the preceding ring hasn't yet
                reached ``version``, or its health check fails.
        """
        idx = _RING_ORDER.index(ring)
        if idx > 0:
            preceding = _RING_ORDER[idx - 1]
            preceding_version = self.get_version_for_ring(preceding)
            if preceding_version != version:
                raise RolloutHealthGateFailedError(
                    f"cannot promote {ring.name} to {version!r}: preceding ring {preceding.name} "
                    f"is on {preceding_version!r}, not {version!r}"
                )
            if health_check is not None and not health_check(preceding):
                raise RolloutHealthGateFailedError(
                    f"cannot promote {ring.name} to {version!r}: health check for preceding ring "
                    f"{preceding.name} failed"
                )

        result = self._repo.promote_ring(ring, version)
        metrics.record_ring_promotion(ring, version)
        return result

    @staticmethod
    def _compute_ring(tenant_id: TenantId) -> RolloutRing:
        digest = hashlib.sha256(str(tenant_id).encode()).hexdigest()
        bucket = int(digest[:8], 16) % 4
        return _RING_ORDER[bucket]


__all__ = ["FleetRolloutManager", "RolloutHealthGateFailedError", "RolloutRepositoryPort"]
