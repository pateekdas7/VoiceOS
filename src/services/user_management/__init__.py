"""User Management — CRUD, invitation workflow, SSO stub (V5 Ch8).

Architecture: V5 Ch8 (User & Organization Management).
"""

from __future__ import annotations

from .invitation import (
    DEFAULT_INVITATION_TTL_HOURS,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationNotPendingError,
    InvitationService,
    IssuedInvitation,
)
from .service import UserService
from .sso_stub import SSOIntegration, SSONotImplementedError

__all__ = [
    "DEFAULT_INVITATION_TTL_HOURS",
    "InvitationExpiredError",
    "InvitationNotFoundError",
    "InvitationNotPendingError",
    "InvitationService",
    "IssuedInvitation",
    "SSOIntegration",
    "SSONotImplementedError",
    "UserService",
]
