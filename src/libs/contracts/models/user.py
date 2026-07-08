"""Persistent data models for users, roles, and RBAC assignments.

VoiceOS uses role-based access control (RBAC) scoped to Organisation,
BusinessUnit, or Branch. Roles are assigned at the OrgScope level,
allowing fine-grained access control for multi-branch enterprises.

Architecture: V4 Ch6 (RBAC); V4 Ch7 (Authentication);
              V5 Ch2 (Multi-tenancy); V5 Ch9 (Admin Portal).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId


class OrgScope(BaseModel):
    """The organisational scope of an RBAC role assignment (V4 Ch6.3).

    A user may have different roles at different org scopes within
    the same tenant. More specific scopes override broader ones.
    """

    model_config = ConfigDict(frozen=True)

    scope_type: str
    """Scope level: 'TENANT' | 'ORG' | 'BUSINESS_UNIT' | 'BRANCH'."""
    scope_id: str
    """ID of the scoped resource (tenant_id, org_id, bu_id, or branch_id)."""


class Role(BaseModel):
    """A named role with a set of permissions (V4 Ch6.2).

    Roles are tenant-scoped. The ``permissions`` tuple contains stable
    permission codes (e.g. 'campaign:create', 'ptp:view', 'audit:read').
    """

    model_config = ConfigDict(frozen=True)

    role_id: str = Field(min_length=1)
    tenant_id: TenantId
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    permissions: tuple[str, ...] = Field(default=())
    """Stable permission codes granted by this role."""
    is_system_role: bool = False
    """System roles are managed by VoiceOS and cannot be deleted."""
    created_at: datetime
    updated_at: datetime


class RoleAssignment(BaseModel):
    """Assigns a Role to a User at a specific OrgScope (V4 Ch6.4)."""

    model_config = ConfigDict(frozen=True)

    assignment_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    org_scope: OrgScope
    assigned_by: str = Field(min_length=1)
    """User ID of the admin who created this assignment."""
    assigned_at: datetime
    expires_at: datetime | None = None
    """Optional expiry for time-limited role grants."""


class User(BaseModel):
    """A human user or service account in the VoiceOS Admin Portal (V5 Ch9).

    Service accounts (``is_service_account=True``) are used for
    API integrations (CRM sync, webhook endpoints). They authenticate
    via API key rather than username/password.

    Architecture: V4 Ch7 (Authentication); V4 Ch6 (RBAC).
    """

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    tenant_id: TenantId
    email: str = Field(min_length=5, max_length=254)
    """User's email address (used as login identifier)."""
    name: str = Field(min_length=1, max_length=200)
    is_active: bool = True
    is_service_account: bool = False
    """Service accounts authenticate via API key, not password."""
    mfa_enabled: bool = False
    """Whether multi-factor authentication is enabled for this user."""
    role_assignments: tuple[RoleAssignment, ...] = Field(default=())
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "OrgScope",
    "Role",
    "RoleAssignment",
    "User",
]
