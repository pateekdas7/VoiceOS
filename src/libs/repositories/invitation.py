"""InvitationRepository — token-based user invitation workflow (V5 Ch8).

Architecture: V5 Ch8 (User & Organization Management — invitation workflow); AR-8.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..contracts.primitives import TenantId
from .base import BaseRepository

_TABLE = "invitations"

_COLUMNS = (
    "invitation_id",
    "tenant_id",
    "email",
    "role_id",
    "org_scope_type",
    "org_scope_id",
    "token_hash",
    "status",
    "invited_by",
    "expires_at",
    "created_at",
    "accepted_at",
)


@dataclass(frozen=True)
class Invitation:
    """Read-side representation of one ``invitations`` row."""

    invitation_id: str
    tenant_id: TenantId
    email: str
    role_id: str
    org_scope_type: str
    org_scope_id: str
    token_hash: str
    status: str
    invited_by: str
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None = None


class InvitationRepository(BaseRepository):
    """CRUD over the ``invitations`` table."""

    def create(self, invitation: Invitation) -> Invitation:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                invitation_id, tenant_id, email, role_id, org_scope_type, org_scope_id,
                token_hash, status, invited_by, expires_at, created_at, accepted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                invitation.invitation_id,
                invitation.tenant_id,
                invitation.email,
                invitation.role_id,
                invitation.org_scope_type,
                invitation.org_scope_id,
                invitation.token_hash,
                invitation.status,
                invitation.invited_by,
                invitation.expires_at,
                invitation.created_at,
                invitation.accepted_at,
            ),
        )
        self._commit()
        return invitation

    def get_by_token_hash(self, token_hash: str) -> Invitation | None:
        cur = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE token_hash = %s", (token_hash,))
        row = cur.fetchone()
        return self._hydrate(row) if row is not None else None

    def mark_status(self, invitation_id: str, status: str, *, accepted_at: datetime | None = None) -> int:
        cur = self._execute(
            f"UPDATE {_TABLE} SET status = %s, accepted_at = %s WHERE invitation_id = %s",
            (status, accepted_at, invitation_id),
        )
        self._commit()
        rowcount: int = cur.rowcount
        return rowcount

    def list_pending(self, tenant_id: TenantId) -> tuple[Invitation, ...]:
        rows = self._tenant_select(_TABLE, _COLUMNS, tenant_id, extra_where="status = 'PENDING'")
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> Invitation:
        (
            invitation_id,
            tenant_id,
            email,
            role_id,
            org_scope_type,
            org_scope_id,
            token_hash,
            status,
            invited_by,
            expires_at,
            created_at,
            accepted_at,
        ) = row
        return Invitation(
            invitation_id=str(invitation_id),
            tenant_id=TenantId(tenant_id),
            email=email,
            role_id=str(role_id),
            org_scope_type=org_scope_type,
            org_scope_id=org_scope_id,
            token_hash=token_hash,
            status=status,
            invited_by=str(invited_by),
            expires_at=expires_at,
            created_at=created_at,
            accepted_at=accepted_at,
        )
