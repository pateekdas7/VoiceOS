"""UserRepository — users, roles, and hierarchy-scoped role assignments (V5 Ch8; V4 Ch6).

Architecture: V5 Ch8 (User & Organization Management); V4 Ch6 (RBAC); AR-8.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..contracts.models.user import OrgScope, Role, RoleAssignment, User
from ..contracts.primitives import TenantId
from .base import BaseRepository

_USERS_TABLE = "users"
_ROLES_TABLE = "roles"
_ASSIGNMENTS_TABLE = "role_assignments"

_USER_COLUMNS = (
    "user_id",
    "tenant_id",
    "email",
    "name",
    "is_active",
    "is_service_account",
    "mfa_enabled",
    "last_login_at",
    "created_at",
    "updated_at",
)
_ROLE_COLUMNS = (
    "role_id",
    "tenant_id",
    "name",
    "description",
    "permissions",
    "is_system_role",
    "created_at",
    "updated_at",
)
_ASSIGNMENT_COLUMNS = (
    "assignment_id",
    "user_id",
    "role_id",
    "scope_type",
    "scope_id",
    "assigned_by",
    "assigned_at",
    "expires_at",
)


class UserRepository(BaseRepository):
    """CRUD for ``users``/``roles``/``role_assignments``."""

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(self, user: User) -> User:
        self._execute(
            f"""
            INSERT INTO {_USERS_TABLE} (
                user_id, tenant_id, email, name, is_active, is_service_account,
                mfa_enabled, last_login_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user.user_id,
                user.tenant_id,
                user.email,
                user.name,
                user.is_active,
                user.is_service_account,
                user.mfa_enabled,
                user.last_login_at,
                user.created_at,
                user.updated_at,
            ),
        )
        self._commit()
        return user

    def get_user(self, tenant_id: TenantId, user_id: str) -> User | None:
        row = self._tenant_select_one(
            _USERS_TABLE, _USER_COLUMNS, tenant_id, extra_where="user_id = %s", extra_params=(user_id,)
        )
        if row is None:
            return None
        return self._hydrate_user(row, self.get_role_assignments(tenant_id, user_id))

    def find_user_by_email(self, tenant_id: TenantId, email: str) -> User | None:
        row = self._tenant_select_one(
            _USERS_TABLE, _USER_COLUMNS, tenant_id, extra_where="email = %s", extra_params=(email,)
        )
        if row is None:
            return None
        user_id = str(row[0])
        return self._hydrate_user(row, self.get_role_assignments(tenant_id, user_id))

    def list_users(self, tenant_id: TenantId) -> tuple[User, ...]:
        rows = self._tenant_select(_USERS_TABLE, _USER_COLUMNS, tenant_id, order_by="created_at")
        return tuple(self._hydrate_user(row, self.get_role_assignments(tenant_id, str(row[0]))) for row in rows)

    def set_active_status(self, tenant_id: TenantId, user_id: str, *, is_active: bool) -> int:
        return self._tenant_update(
            _USERS_TABLE,
            ["is_active", "updated_at"],
            [is_active, datetime.now(UTC)],
            tenant_id,
            extra_where="user_id = %s",
            extra_params=(user_id,),
        )

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------

    def create_role(self, role: Role) -> Role:
        self._execute(
            f"""
            INSERT INTO {_ROLES_TABLE} (
                role_id, tenant_id, name, description, permissions, is_system_role, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                role.role_id,
                role.tenant_id,
                role.name,
                role.description,
                list(role.permissions),
                role.is_system_role,
                role.created_at,
                role.updated_at,
            ),
        )
        self._commit()
        return role

    def get_role_by_name(self, tenant_id: TenantId, name: str) -> Role | None:
        row = self._tenant_select_one(
            _ROLES_TABLE, _ROLE_COLUMNS, tenant_id, extra_where="name = %s", extra_params=(name,)
        )
        return self._hydrate_role(row) if row is not None else None

    # ------------------------------------------------------------------
    # Role assignments
    # ------------------------------------------------------------------

    def assign_role(self, assignment: RoleAssignment) -> RoleAssignment:
        self._execute(
            f"""
            INSERT INTO {_ASSIGNMENTS_TABLE} (
                assignment_id, user_id, role_id, scope_type, scope_id, assigned_by, assigned_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                assignment.assignment_id,
                assignment.user_id,
                assignment.role_id,
                assignment.org_scope.scope_type,
                assignment.org_scope.scope_id,
                assignment.assigned_by,
                assignment.assigned_at,
                assignment.expires_at,
            ),
        )
        self._commit()
        return assignment

    def get_role_assignments(self, tenant_id: TenantId, user_id: str) -> tuple[RoleAssignment, ...]:
        cur = self._execute(
            f"SELECT {', '.join(_ASSIGNMENT_COLUMNS)} FROM {_ASSIGNMENTS_TABLE} WHERE user_id = %s",
            (user_id,),
        )
        rows: list[tuple[Any, ...]] = cur.fetchall()
        return tuple(self._hydrate_assignment(row) for row in rows)

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    def _hydrate_user(self, row: tuple[Any, ...], role_assignments: tuple[RoleAssignment, ...]) -> User:
        (
            user_id,
            tenant_id,
            email,
            name,
            is_active,
            is_service_account,
            mfa_enabled,
            last_login_at,
            created_at,
            updated_at,
        ) = row
        return User(
            user_id=str(user_id),
            tenant_id=TenantId(tenant_id),
            email=email,
            name=name,
            is_active=is_active,
            is_service_account=is_service_account,
            mfa_enabled=mfa_enabled,
            role_assignments=role_assignments,
            last_login_at=last_login_at,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _hydrate_role(self, row: tuple[Any, ...]) -> Role:
        role_id, tenant_id, name, description, permissions, is_system_role, created_at, updated_at = row
        return Role(
            role_id=str(role_id),
            tenant_id=TenantId(tenant_id),
            name=name,
            description=description,
            permissions=tuple(permissions or ()),
            is_system_role=is_system_role,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _hydrate_assignment(self, row: tuple[Any, ...]) -> RoleAssignment:
        assignment_id, user_id, role_id, scope_type, scope_id, assigned_by, assigned_at, expires_at = row
        return RoleAssignment(
            assignment_id=str(assignment_id),
            user_id=str(user_id),
            role_id=str(role_id),
            org_scope=OrgScope(scope_type=scope_type, scope_id=scope_id),
            assigned_by=str(assigned_by),
            assigned_at=assigned_at,
            expires_at=expires_at,
        )
