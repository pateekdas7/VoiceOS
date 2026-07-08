"""Integration test configuration and environment-based skip markers.

Integration tests require live Docker services. Each test class or function
that needs a database must be decorated with the corresponding marker from
this module.

Running integration tests locally:
    docker compose up -d
    POSTGRES_DSN=postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev \\
    REDIS_URL=redis://localhost:6379/0 \\
    MONGODB_URI=mongodb://localhost:27017/voiceos_dev \\
    pytest tests/integration/ -v

In CI the values are injected by the workflow after docker compose up.

Architecture: V6 Ch9 (Testing Standards); DocSuite-08.
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Environment variable detection
# ---------------------------------------------------------------------------

_POSTGRES_DSN: str = os.environ.get("POSTGRES_DSN", "")
_REDIS_URL: str = os.environ.get("REDIS_URL", "")
_MONGODB_URI: str = os.environ.get("MONGODB_URI", "")

# ---------------------------------------------------------------------------
# Skip markers — import and apply these to test classes / functions
# ---------------------------------------------------------------------------

requires_postgres = pytest.mark.skipif(
    not _POSTGRES_DSN,
    reason=(
        "POSTGRES_DSN not set — skipping Postgres integration tests. "
        "Set POSTGRES_DSN=postgresql://user:pass@host:port/db to enable."
    ),
)

requires_redis = pytest.mark.skipif(
    not _REDIS_URL,
    reason=("REDIS_URL not set — skipping Redis integration tests. Set REDIS_URL=redis://host:port/db to enable."),
)

requires_mongodb = pytest.mark.skipif(
    not _MONGODB_URI,
    reason=(
        "MONGODB_URI not set — skipping MongoDB integration tests. Set MONGODB_URI=mongodb://host:port/db to enable."
    ),
)

requires_all_db = pytest.mark.skipif(
    not (_POSTGRES_DSN and _REDIS_URL and _MONGODB_URI),
    reason=("POSTGRES_DSN, REDIS_URL, or MONGODB_URI not set — skipping full multi-database integration tests."),
)
