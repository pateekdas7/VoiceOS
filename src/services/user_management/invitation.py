"""InvitationService — email-invite workflow with token-based activation (V5 Ch8).

The raw invitation token is returned exactly once, at issuance (``invite()``)
— only its SHA-256 hash is ever persisted (``invitations.token_hash``),
mirroring ``APIKeyValidator``'s SHA-256-hashed-key-store precedent
(Sprint-018): a leaked database dump never yields a usable token.

Architecture: V5 Ch8 (User & Organization Management) §8.6 (Invitation Workflow).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.user import OrgScope, RoleAssignment, User
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.invitation import Invitation

from .ports import InvitationRepositoryPort, UserRepositoryPort

DEFAULT_INVITATION_TTL_HOURS = 72


class InvitationNotFoundError(Exception):
    """Raised when a token does not match any invitation."""


class InvitationNotPendingError(Exception):
    """Raised when an invitation exists but is not in PENDING status (already
    accepted, expired, or revoked)."""


class InvitationExpiredError(Exception):
    """Raised when a PENDING invitation's ``expires_at`` has already passed."""


class EmailAlreadyRegisteredError(Exception):
    """Raised when a second PENDING invitation for an already-activated email is redeemed.

    Two invitations can be issued for the same (tenant_id, email) before
    either is redeemed (invite() has no such check) -- without this, the
    second activate() call reached the ``uq_user_tenant_email`` unique
    constraint directly, surfacing as an unhandled 500 instead of a clean,
    catchable error (found live: a supervisor re-inviting an address that
    had already accepted crashed the callback)."""


@dataclass(frozen=True)
class IssuedInvitation:
    """Returned once at issuance — ``raw_token`` is never persisted or logged."""

    invitation: Invitation
    raw_token: str


class InvitationService:
    """Issues and activates token-based user invitations."""

    def __init__(
        self,
        invitation_repository: InvitationRepositoryPort,
        user_repository: UserRepositoryPort,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._invitations = invitation_repository
        self._users = user_repository
        self._audit_logger = audit_logger

    def invite(
        self,
        tenant_id: TenantId,
        email: str,
        role_id: str,
        org_scope: OrgScope,
        invited_by: str,
        ttl_hours: int = DEFAULT_INVITATION_TTL_HOURS,
    ) -> IssuedInvitation:
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        invitation = Invitation(
            invitation_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            email=email,
            role_id=role_id,
            org_scope_type=org_scope.scope_type,
            org_scope_id=org_scope.scope_id,
            token_hash=_hash_token(raw_token),
            status="PENDING",
            invited_by=invited_by,
            expires_at=now + timedelta(hours=ttl_hours),
            created_at=now,
        )
        self._invitations.create(invitation)
        if self._audit_logger is not None:
            self._audit_logger.record_user_invited(tenant_id, invited_by, invitation.invitation_id)
        return IssuedInvitation(invitation=invitation, raw_token=raw_token)

    def activate(self, raw_token: str, name: str) -> User:
        """Redeem ``raw_token``: creates the invited User with the invited role/scope.

        Raises:
            InvitationNotFoundError: no invitation matches this token.
            InvitationExpiredError: the invitation's TTL has passed.
            InvitationNotPendingError: already accepted or revoked.
            EmailAlreadyRegisteredError: this tenant already has an active
                user at this email (e.g. a second invitation for the same
                address was issued and is being redeemed after the first).
        """
        invitation = self._invitations.get_by_token_hash(_hash_token(raw_token))
        if invitation is None:
            raise InvitationNotFoundError("no invitation matches this token")
        if invitation.status != "PENDING":
            raise InvitationNotPendingError(f"invitation is {invitation.status}, not PENDING")
        now = datetime.now(UTC)
        if invitation.expires_at < now:
            self._invitations.mark_status(invitation.invitation_id, "EXPIRED")
            raise InvitationExpiredError("invitation has expired")
        if self._users.find_user_by_email(invitation.tenant_id, invitation.email) is not None:
            raise EmailAlreadyRegisteredError(f"{invitation.email} is already registered for this tenant")

        user = User(
            user_id=str(uuid.uuid4()),
            tenant_id=invitation.tenant_id,
            email=invitation.email,
            name=name,
            created_at=now,
            updated_at=now,
        )
        self._users.create_user(user)
        self._users.assign_role(
            RoleAssignment(
                assignment_id=str(uuid.uuid4()),
                user_id=user.user_id,
                role_id=invitation.role_id,
                org_scope=OrgScope(scope_type=invitation.org_scope_type, scope_id=invitation.org_scope_id),
                assigned_by=invitation.invited_by,
                assigned_at=now,
            )
        )
        self._invitations.mark_status(invitation.invitation_id, "ACCEPTED", accepted_at=now)
        if self._audit_logger is not None:
            self._audit_logger.record_user_activated(invitation.tenant_id, user.user_id, invitation.invitation_id)
        return user


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
