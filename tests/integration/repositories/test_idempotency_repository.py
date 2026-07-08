"""Integration test: IdempotencyRepository against real Postgres (V3 Ch8).

Required named test: test_idempotency_double_key.

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from src.libs.contracts.primitives import TenantId
from src.libs.repositories.idempotency import IdempotencyRepository
from tests.integration.conftest import requires_postgres


@requires_postgres
class TestIdempotencyRepository:
    def test_idempotency_double_key(self, pg_conn: Any) -> None:
        """Same key recorded twice → second call is a no-op; check() returns the cached result."""
        repo = IdempotencyRepository(pg_conn)
        tenant_id = TenantId(str(uuid.uuid4()))
        key = f"key-{uuid.uuid4()}"

        assert repo.check(tenant_id, key) is None

        first = repo.record(tenant_id, key, "ptp", {"ptp_id": "ptp-1"})
        assert first is True

        second = repo.record(tenant_id, key, "ptp", {"ptp_id": "ptp-DIFFERENT"})
        assert second is False

        cached = repo.check(tenant_id, key)
        assert cached == {"ptp_id": "ptp-1"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
