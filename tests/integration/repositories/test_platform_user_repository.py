"""Integration test: PlatformUserRepository against real Postgres (ADR-005 Sec 3/4.2).

Skipped when POSTGRES_DSN is not set.
Architecture: ADR-005 Sec 3 (Actor Model), Sec 4.2 (New Components).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.libs.contracts.models.platform_user import PlatformUser
from src.libs.repositories.platform_user import PlatformUserRepository
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 26, tzinfo=UTC)


@requires_postgres
class TestPlatformUserRepository:
    def test_create_and_get_round_trip(self, pg_conn: Any) -> None:
        repo = PlatformUserRepository(pg_conn)
        platform_user_id = str(uuid.uuid4())
        email = f"it-{uuid.uuid4()}@voiceos.ai"

        try:
            created = repo.create(
                PlatformUser(
                    platform_user_id=platform_user_id,
                    email=email,
                    name="Integration Test Admin",
                    platform_role="PLATFORM_ADMIN",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            assert created.email == email

            fetched = repo.get(platform_user_id)
            assert fetched is not None
            assert fetched.platform_user_id == platform_user_id
            assert fetched.email == email
            assert fetched.platform_role == "PLATFORM_ADMIN"
            assert fetched.is_active is True
        finally:
            _delete(pg_conn, platform_user_id)

    def test_find_by_email(self, pg_conn: Any) -> None:
        repo = PlatformUserRepository(pg_conn)
        platform_user_id = str(uuid.uuid4())
        email = f"it-{uuid.uuid4()}@voiceos.ai"

        try:
            repo.create(
                PlatformUser(
                    platform_user_id=platform_user_id,
                    email=email,
                    name="Findable",
                    platform_role="PLATFORM_SUPPORT",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            found = repo.find_by_email(email)
            assert found is not None
            assert found.platform_user_id == platform_user_id
        finally:
            _delete(pg_conn, platform_user_id)

    def test_find_by_email_returns_none_when_absent(self, pg_conn: Any) -> None:
        repo = PlatformUserRepository(pg_conn)
        assert repo.find_by_email(f"nobody-{uuid.uuid4()}@voiceos.ai") is None

    def test_set_active_status(self, pg_conn: Any) -> None:
        repo = PlatformUserRepository(pg_conn)
        platform_user_id = str(uuid.uuid4())
        email = f"it-{uuid.uuid4()}@voiceos.ai"

        try:
            repo.create(
                PlatformUser(
                    platform_user_id=platform_user_id,
                    email=email,
                    name="Deactivate Me",
                    platform_role="PLATFORM_BILLING_OPS",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            rowcount = repo.set_active_status(platform_user_id, is_active=False)
            assert rowcount == 1
            assert repo.get(platform_user_id).is_active is False  # type: ignore[union-attr]
        finally:
            _delete(pg_conn, platform_user_id)

    def test_email_uniqueness_is_enforced(self, pg_conn: Any) -> None:
        repo = PlatformUserRepository(pg_conn)
        email = f"it-dup-{uuid.uuid4()}@voiceos.ai"
        first_id = str(uuid.uuid4())
        second_id = str(uuid.uuid4())

        try:
            repo.create(
                PlatformUser(
                    platform_user_id=first_id,
                    email=email,
                    name="First",
                    platform_role="PLATFORM_ADMIN",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            with pytest.raises(Exception):  # noqa: B017 - psycopg2 raises IntegrityError, not imported here
                repo.create(
                    PlatformUser(
                        platform_user_id=second_id,
                        email=email,
                        name="Second",
                        platform_role="PLATFORM_SUPPORT",
                        created_at=_NOW,
                        updated_at=_NOW,
                    )
                )
            pg_conn.rollback()
        finally:
            _delete(pg_conn, first_id)
            _delete(pg_conn, second_id)

    def test_invalid_platform_role_rejected_by_check_constraint(self, pg_conn: Any) -> None:
        repo = PlatformUserRepository(pg_conn)
        platform_user_id = str(uuid.uuid4())

        with pytest.raises(Exception):  # noqa: B017 - psycopg2 raises IntegrityError, not imported here
            repo.create(
                PlatformUser(
                    platform_user_id=platform_user_id,
                    email=f"it-{uuid.uuid4()}@voiceos.ai",
                    name="Bad Role",
                    platform_role="NOT_A_REAL_ROLE",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
        pg_conn.rollback()
        _delete(pg_conn, platform_user_id)


def _delete(conn: Any, platform_user_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM platform_users WHERE platform_user_id = %s", (platform_user_id,))
    conn.commit()
