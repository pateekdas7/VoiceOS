"""PlatformUserRepository -- CRUD for ``platform_users`` (ADR-005 Sec 3/4.2).

Deliberately does NOT use ``BaseRepository``'s ``_tenant_select``/
``_tenant_update`` helpers -- those mandate a ``tenant_id`` WHERE clause
(AR-8), and ``platform_users`` has no ``tenant_id`` column by design (a
PlatformActor is not a member of any tenant, ADR-005 Sec 3). This repository
only reuses ``BaseRepository``'s low-level primitives (``_execute``/
``_commit``, including the shared CircuitBreaker wiring) and writes its own
unscoped queries directly -- reusing the tenant-scoped builders here would be
actively wrong, not just unnecessary.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..contracts.models.platform_user import PlatformUser
from .base import BaseRepository

_TABLE = "platform_users"
_COLUMNS = (
    "platform_user_id",
    "email",
    "name",
    "platform_role",
    "is_active",
    "mfa_enabled",
    "last_login_at",
    "created_at",
    "updated_at",
)


class PlatformUserRepository(BaseRepository):
    """CRUD for ``platform_users`` (ADR-005 Sec 3/4.2)."""

    def create(self, user: PlatformUser) -> PlatformUser:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                platform_user_id, email, name, platform_role, is_active,
                mfa_enabled, last_login_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user.platform_user_id,
                user.email,
                user.name,
                user.platform_role,
                user.is_active,
                user.mfa_enabled,
                user.last_login_at,
                user.created_at,
                user.updated_at,
            ),
        )
        self._commit()
        return user

    def get(self, platform_user_id: str) -> PlatformUser | None:
        cols = ", ".join(_COLUMNS)
        cur = self._execute(
            f"SELECT {cols} FROM {_TABLE} WHERE platform_user_id = %s",
            (platform_user_id,),
        )
        row = cur.fetchone()
        return self._hydrate(row) if row is not None else None

    def find_by_email(self, email: str) -> PlatformUser | None:
        cols = ", ".join(_COLUMNS)
        cur = self._execute(f"SELECT {cols} FROM {_TABLE} WHERE email = %s", (email,))
        row = cur.fetchone()
        return self._hydrate(row) if row is not None else None

    def list_all(self) -> tuple[PlatformUser, ...]:
        cols = ", ".join(_COLUMNS)
        cur = self._execute(f"SELECT {cols} FROM {_TABLE} ORDER BY created_at")
        rows = cur.fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def set_active_status(self, platform_user_id: str, *, is_active: bool) -> int:
        cur = self._execute(
            f"UPDATE {_TABLE} SET is_active = %s, updated_at = %s WHERE platform_user_id = %s",
            (is_active, datetime.now(UTC), platform_user_id),
        )
        self._commit()
        rowcount: int = cur.rowcount
        return rowcount

    @staticmethod
    def _hydrate(row: tuple[object, ...]) -> PlatformUser:
        return PlatformUser(
            platform_user_id=str(row[0]),
            email=str(row[1]),
            name=str(row[2]),
            platform_role=str(row[3]),
            is_active=bool(row[4]),
            mfa_enabled=bool(row[5]),
            last_login_at=row[6],  # type: ignore[arg-type]
            created_at=row[7],  # type: ignore[arg-type]
            updated_at=row[8],  # type: ignore[arg-type]
        )
