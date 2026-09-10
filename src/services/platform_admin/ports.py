"""Narrow structural port PlatformAdminService depends on (see user_management.ports)."""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.models.platform_user import PlatformUser


class PlatformUserRepositoryPort(Protocol):
    """The ``platform_users`` operations platform admin needs."""

    def create(self, user: PlatformUser) -> PlatformUser: ...

    def get(self, platform_user_id: str) -> PlatformUser | None: ...

    def find_by_email(self, email: str) -> PlatformUser | None: ...

    def list_all(self) -> tuple[PlatformUser, ...]: ...

    def set_active_status(self, platform_user_id: str, *, is_active: bool) -> int: ...
