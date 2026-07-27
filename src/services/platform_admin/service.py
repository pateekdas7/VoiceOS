"""PlatformAdminService -- CRUD over ``platform_users`` (ADR-005 Sec 3/4.2).

Architecture: ADR-005 Sec 3 (Actor Model), Sec 6.8 (Users & Roles, platform side).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.contracts.models.platform_user import PlatformUser

from .ports import PlatformUserRepositoryPort
from .roles import PlatformRole


class DuplicatePlatformUserError(ValueError):
    """Raised by :meth:`PlatformAdminService.create` when the email is already registered."""


class PlatformUserNotFoundError(LookupError):
    """Raised when a lookup or mutation targets a nonexistent platform_user_id."""


class PlatformAdminService:
    """CRUD over ``platform_users`` (ADR-005 Sec 3/4.2)."""

    def __init__(self, repository: PlatformUserRepositoryPort) -> None:
        self._repo = repository

    def create(self, *, email: str, name: str, platform_role: PlatformRole) -> PlatformUser:
        if self._repo.find_by_email(email) is not None:
            raise DuplicatePlatformUserError(f"platform user already exists: {email}")
        now = datetime.now(UTC)
        user = PlatformUser(
            platform_user_id=str(uuid.uuid4()),
            email=email,
            name=name,
            platform_role=platform_role.value,
            created_at=now,
            updated_at=now,
        )
        return self._repo.create(user)

    def get(self, platform_user_id: str) -> PlatformUser | None:
        return self._repo.get(platform_user_id)

    def find_by_email(self, email: str) -> PlatformUser | None:
        return self._repo.find_by_email(email)

    def list_all(self) -> tuple[PlatformUser, ...]:
        return self._repo.list_all()

    def deactivate(self, platform_user_id: str) -> PlatformUser:
        user = self._repo.get(platform_user_id)
        if user is None:
            raise PlatformUserNotFoundError(f"platform user not found: {platform_user_id}")
        self._repo.set_active_status(platform_user_id, is_active=False)
        return user.model_copy(update={"is_active": False, "updated_at": datetime.now(UTC)})
