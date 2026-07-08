"""Integration tests: MongoDB connectivity, insert/find, and cleanup.

Requires MONGODB_URI environment variable. Skipped automatically when absent.

Run:
    MONGODB_URI=mongodb://localhost:27017/voiceos_dev \\
    pytest tests/integration/test_mongodb_connectivity.py -v

Uses the ``voiceos`` database (not a separate ``voiceos_test`` database) —
since Sprint-019, the CPU node's MongoDB user is intentionally scoped to
``readWrite`` on ``voiceos`` only (least-privilege; see CPU_NODE_STATE.md
§7.3), so a database this narrowly-scoped user was never granted a role on
would fail with "not authorized", discovered running this suite in Sprint-019
Phase 2. A dedicated collection name keeps this smoke test's writes clearly
separated from real application collections.

Architecture: V3 Ch5 (Persistence); V6 Ch9; DocSuite-08.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from tests.integration.conftest import requires_mongodb

_MONGODB_URI: str = os.environ.get("MONGODB_URI", "")
_TEST_DB: str = "voiceos"
_TEST_COLLECTION: str = "sprint003_connectivity"


def _get_client() -> Any:
    from pymongo import MongoClient

    return MongoClient(_MONGODB_URI)


@requires_mongodb
class TestMongoDBConnectivity:
    """MongoDB connection, document CRUD, and teardown integration tests."""

    def test_connect_and_ping(self) -> None:
        """Can connect to MongoDB and receive ok=1.0 from the ping command."""
        client = _get_client()
        try:
            result: dict[str, Any] = client.admin.command("ping")
            assert result.get("ok") == 1.0
        finally:
            client.close()

    def test_insert_and_find_one(self) -> None:
        """Can insert a document and find it by a unique field."""
        client = _get_client()
        try:
            col = client[_TEST_DB][_TEST_COLLECTION]
            doc_id = str(uuid.uuid4())
            col.insert_one({"sprint": "sprint-003", "doc_id": doc_id, "status": "ok"})
            found = col.find_one({"doc_id": doc_id})
            assert found is not None
            assert found["status"] == "ok"
        finally:
            client[_TEST_DB][_TEST_COLLECTION].delete_many({"sprint": "sprint-003"})
            client.close()

    def test_delete_document(self) -> None:
        """Deleting a document makes it unfindable."""
        client = _get_client()
        try:
            col = client[_TEST_DB][_TEST_COLLECTION]
            doc_id = str(uuid.uuid4())
            col.insert_one({"doc_id": doc_id, "data": "to_delete"})
            col.delete_one({"doc_id": doc_id})
            assert col.find_one({"doc_id": doc_id}) is None
        finally:
            client.close()

    def test_find_nonexistent_returns_none(self) -> None:
        """find_one on a non-existent document returns None."""
        client = _get_client()
        try:
            col = client[_TEST_DB][_TEST_COLLECTION]
            result = col.find_one({"doc_id": str(uuid.uuid4())})
            assert result is None
        finally:
            client.close()

    def test_collection_names_accessible(self) -> None:
        """Can list collection names from the test database."""
        client = _get_client()
        try:
            db = client[_TEST_DB]
            names = db.list_collection_names()
            assert isinstance(names, list)
        finally:
            client.close()

    def test_disconnect_is_idempotent(self) -> None:
        """close() called twice does not raise."""
        client = _get_client()
        client.close()
        client.close()
