"""Unit tests for platform_admin (ADR-005 Sec 3/4.2).

All tests run fully in-process -- no live Postgres required (Phase 1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from src.libs.contracts.models.platform_user import PlatformUser
from src.services.platform_admin.rbac_engine import PlatformRBACEngine
from src.services.platform_admin.roles import (
    PERM_PLATFORM_READ_ALL,
    PERM_PLATFORM_WRITE_BILLING,
    PERM_PLATFORM_WRITE_CLIENTS,
    PERM_PLATFORM_WRITE_STAFF,
    PlatformRole,
)
from src.services.platform_admin.service import (
    DuplicatePlatformUserError,
    PlatformAdminService,
    PlatformUserNotFoundError,
)


class _FakePlatformUserRepository:
    def __init__(self) -> None:
        self.users: list[PlatformUser] = []

    def create(self, user: PlatformUser) -> PlatformUser:
        self.users.append(user)
        return user

    def get(self, platform_user_id: str) -> PlatformUser | None:
        return next((u for u in self.users if u.platform_user_id == platform_user_id), None)

    def find_by_email(self, email: str) -> PlatformUser | None:
        return next((u for u in self.users if u.email == email), None)

    def list_all(self) -> tuple[PlatformUser, ...]:
        return tuple(self.users)

    def set_active_status(self, platform_user_id: str, *, is_active: bool) -> int:
        for i, u in enumerate(self.users):
            if u.platform_user_id == platform_user_id:
                self.users[i] = u.model_copy(update={"is_active": is_active})
                return 1
        return 0


@pytest.fixture
def repo() -> _FakePlatformUserRepository:
    return _FakePlatformUserRepository()


@pytest.fixture
def service(repo: _FakePlatformUserRepository) -> PlatformAdminService:
    return PlatformAdminService(repo)


# ---------------------------------------------------------------------------
# PlatformRole / permission mapping (ADR-005 Sec 3)
# ---------------------------------------------------------------------------


class TestPlatformRoles:
    def test_platform_admin_holds_all_write_permissions(self) -> None:
        engine = PlatformRBACEngine()
        assert engine.check(PlatformRole.PLATFORM_ADMIN, PERM_PLATFORM_WRITE_CLIENTS)
        assert engine.check(PlatformRole.PLATFORM_ADMIN, PERM_PLATFORM_WRITE_BILLING)
        assert engine.check(PlatformRole.PLATFORM_ADMIN, PERM_PLATFORM_WRITE_STAFF)

    def test_platform_support_is_read_only(self) -> None:
        engine = PlatformRBACEngine()
        assert engine.check(PlatformRole.PLATFORM_SUPPORT, PERM_PLATFORM_READ_ALL)
        assert not engine.check(PlatformRole.PLATFORM_SUPPORT, PERM_PLATFORM_WRITE_CLIENTS)
        assert not engine.check(PlatformRole.PLATFORM_SUPPORT, PERM_PLATFORM_WRITE_BILLING)

    def test_platform_billing_ops_can_only_write_billing(self) -> None:
        engine = PlatformRBACEngine()
        assert engine.check(PlatformRole.PLATFORM_BILLING_OPS, PERM_PLATFORM_WRITE_BILLING)
        assert not engine.check(PlatformRole.PLATFORM_BILLING_OPS, PERM_PLATFORM_WRITE_CLIENTS)
        assert not engine.check(PlatformRole.PLATFORM_BILLING_OPS, PERM_PLATFORM_WRITE_STAFF)

    def test_check_http_method_denies_write_for_support(self) -> None:
        engine = PlatformRBACEngine()
        assert engine.check_http_method(PlatformRole.PLATFORM_SUPPORT, "GET")
        assert not engine.check_http_method(PlatformRole.PLATFORM_SUPPORT, "POST")
        assert not engine.check_http_method(PlatformRole.PLATFORM_SUPPORT, "DELETE")

    def test_check_http_method_permits_write_for_admin(self) -> None:
        engine = PlatformRBACEngine()
        assert engine.check_http_method(PlatformRole.PLATFORM_ADMIN, "POST")
        assert engine.check_http_method(PlatformRole.PLATFORM_ADMIN, "DELETE")

    def test_unknown_role_string_denies(self) -> None:
        engine = PlatformRBACEngine()
        assert not engine.check("NOT_A_REAL_ROLE", PERM_PLATFORM_READ_ALL)
        assert not engine.check_http_method("NOT_A_REAL_ROLE", "GET")

    def test_accepts_role_as_plain_string(self) -> None:
        engine = PlatformRBACEngine()
        assert engine.check("PLATFORM_ADMIN", PERM_PLATFORM_WRITE_CLIENTS)


# ---------------------------------------------------------------------------
# PlatformAdminService CRUD (ADR-005 Sec 4.2)
# ---------------------------------------------------------------------------


class TestPlatformAdminService:
    def test_create_persists_and_returns_user(self, service: PlatformAdminService) -> None:
        user = service.create(email="alice@voiceos.ai", name="Alice", platform_role=PlatformRole.PLATFORM_ADMIN)
        assert user.email == "alice@voiceos.ai"
        assert user.platform_role == "PLATFORM_ADMIN"
        assert user.is_active is True
        assert service.get(user.platform_user_id) == user

    def test_create_rejects_duplicate_email(self, service: PlatformAdminService) -> None:
        service.create(email="bob@voiceos.ai", name="Bob", platform_role=PlatformRole.PLATFORM_SUPPORT)
        with pytest.raises(DuplicatePlatformUserError):
            service.create(email="bob@voiceos.ai", name="Bob Two", platform_role=PlatformRole.PLATFORM_ADMIN)

    def test_find_by_email(self, service: PlatformAdminService) -> None:
        created = service.create(
            email="carol@voiceos.ai", name="Carol", platform_role=PlatformRole.PLATFORM_BILLING_OPS
        )
        found = service.find_by_email("carol@voiceos.ai")
        assert found is not None
        assert found.platform_user_id == created.platform_user_id

    def test_find_by_email_returns_none_when_absent(self, service: PlatformAdminService) -> None:
        assert service.find_by_email("nobody@voiceos.ai") is None

    def test_list_all_returns_every_platform_user(self, service: PlatformAdminService) -> None:
        service.create(email="a@voiceos.ai", name="A", platform_role=PlatformRole.PLATFORM_ADMIN)
        service.create(email="b@voiceos.ai", name="B", platform_role=PlatformRole.PLATFORM_SUPPORT)
        assert {u.email for u in service.list_all()} == {"a@voiceos.ai", "b@voiceos.ai"}

    def test_deactivate_marks_user_inactive(self, service: PlatformAdminService) -> None:
        user = service.create(email="dan@voiceos.ai", name="Dan", platform_role=PlatformRole.PLATFORM_ADMIN)
        deactivated = service.deactivate(user.platform_user_id)
        assert deactivated.is_active is False
        assert service.get(user.platform_user_id).is_active is False  # type: ignore[union-attr]

    def test_deactivate_unknown_user_raises(self, service: PlatformAdminService) -> None:
        with pytest.raises(PlatformUserNotFoundError):
            service.deactivate(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# PlatformUser contract model (ADR-005 Sec 3) -- no tenant_id, ever
# ---------------------------------------------------------------------------


class TestPlatformUserModel:
    def test_has_no_tenant_id_field(self) -> None:
        assert "tenant_id" not in PlatformUser.model_fields

    def test_is_frozen(self) -> None:
        now = datetime.now(UTC)
        user = PlatformUser(
            platform_user_id=str(uuid.uuid4()),
            email="frozen@voiceos.ai",
            name="Frozen",
            platform_role="PLATFORM_ADMIN",
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(Exception):  # noqa: B017 - pydantic frozen model raises its own ValidationError
            user.email = "changed@voiceos.ai"  # type: ignore[misc]
