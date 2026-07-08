"""AuthorizationRequest and AuthorizationResult (V4 Ch6 §6.4 "Authorization API").

Architecture: V4 Ch6 (Authorization/RBAC).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.services.auth.models import AuthContext


class AuthorizationOutcome(StrEnum):
    """The AuthzService's binary decision (V4 Ch6 §6.4)."""

    PERMIT = "PERMIT"
    DENY = "DENY"


@dataclass(frozen=True)
class AuthorizationRequest:
    """A single authorization check (V4 Ch6 §6.4 ``authorize(...)`` args)."""

    auth_context: AuthContext
    action: str
    """HTTP method (e.g. 'POST') or a permission string (see :mod:`roles`)."""

    resource_tenant_id: str
    """Tenant scope owning the resource being accessed — checked by TenantIsolationGuard."""

    resource_attributes: dict[str, Any] = field(default_factory=dict)
    """Resource organizational attributes consulted by ABACEvaluator (e.g. branch_id)."""

    subject_attributes: dict[str, Any] = field(default_factory=dict)
    """Subject organizational attributes consulted by ABACEvaluator."""


@dataclass(frozen=True)
class AuthorizationResult:
    """The result of a single :meth:`~.service.AuthzService.authorize` call."""

    outcome: AuthorizationOutcome
    reason: str = ""
