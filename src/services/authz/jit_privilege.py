"""JITPrivilege — time-boxed, dual-approval temporary role escalation
(V4 Ch6 §6.7 "Just-In-Time Privileges").

Mirrors the Sprint-017 ``BreakGlassPolicy`` pattern (dual-approval,
time-boxed emergency override) for the authorization domain: a subject may
be granted a higher role for a bounded window, with mandatory multi-party
approval, rather than holding elevated privileges permanently.

Architecture: V4 Ch6 (Authorization/RBAC — JIT privileges).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from src.services.auth.models import AuthorizationDeniedError

from .roles import Role

DEFAULT_TTL_SECONDS = 3600
DEFAULT_REQUIRED_APPROVALS = 2


@dataclass(frozen=True)
class JITGrant:
    """A single, time-boxed role escalation grant."""

    grant_id: str
    subject: str
    role: Role
    reason: str
    approvers: tuple[str, ...]
    granted_at: datetime
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    def is_expired(self, now: datetime | None = None) -> bool:
        """True once ``now`` is past ``granted_at + ttl_seconds``."""
        current = now or datetime.now(UTC)
        return current > self.granted_at + timedelta(seconds=self.ttl_seconds)


@dataclass(frozen=True)
class JITApprovalRequest:
    """A pending request for JIT escalation, awaiting approvals."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    subject: str = ""
    role: Role = Role.AGENT
    reason: str = ""


class JITPrivilege:
    """Grants time-boxed role escalations, gated on multi-approver sign-off.

    Args:
        required_approvals: Minimum distinct approvers required (V4 Ch6 §6.7
            default: 2).
    """

    def __init__(self, required_approvals: int = DEFAULT_REQUIRED_APPROVALS) -> None:
        self._required_approvals = required_approvals

    def grant(
        self,
        subject: str,
        role: Role,
        reason: str,
        approvers: tuple[str, ...],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        granted_at: datetime | None = None,
    ) -> JITGrant:
        """Issue a JIT grant once enough distinct approvers have signed off.

        Raises:
            AuthorizationDeniedError: Fewer than ``required_approvals``
                distinct approvers were supplied.
        """
        distinct_approvers = set(approvers)
        if len(distinct_approvers) < self._required_approvals:
            raise AuthorizationDeniedError(
                f"JIT privilege escalation requires {self._required_approvals} distinct approvers, "
                f"got {len(distinct_approvers)}"
            )
        return JITGrant(
            grant_id=str(uuid.uuid4()),
            subject=subject,
            role=role,
            reason=reason,
            approvers=approvers,
            granted_at=granted_at or datetime.now(UTC),
            ttl_seconds=ttl_seconds,
        )

    def is_active(self, grant: JITGrant, now: datetime | None = None) -> bool:
        """True iff ``grant`` has not yet expired."""
        return not grant.is_expired(now)
