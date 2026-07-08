"""Alembic environment — Sprint-014 Persistent Storage.

Resolves the Postgres connection from the ``POSTGRES_DSN`` environment
variable (never from a committed config value) and runs migrations in
"online" mode only. Migrations are hand-written raw SQL (``op.execute``),
not SQLAlchemy ORM/autogenerate — repositories stay psycopg2-based and
model-agnostic (see BaseRepository), matching the RelationshipMemoryStore
(Sprint-010) precedent. ``target_metadata`` is intentionally ``None``:
autogenerate diffing is not used.

Architecture: V6 Ch7 (Data Modeling Standards — expand-contract migrations).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_dsn = os.environ.get("POSTGRES_DSN", "")
if _dsn:
    # psycopg2-binary is the installed driver; normalize scheme so SQLAlchemy
    # selects it explicitly rather than guessing.
    _sqlalchemy_url = _dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
    config.set_main_option("sqlalchemy.url", _sqlalchemy_url)

target_metadata = None


def run_migrations_offline() -> None:
    """Not supported — Sprint-014 migrations require a live connection.

    Raises:
        RuntimeError: Always. Offline SQL-script generation is not a
            supported deployment path for this project (V6 Ch7 requires
            migrations to run against a real database with verifiable
            up/down results).
    """
    raise RuntimeError(
        "Offline migration mode is not supported for VoiceOS. "
        "Set POSTGRES_DSN and run 'alembic upgrade head' against a live database."
    )


def run_migrations_online() -> None:
    """Run migrations against a live Postgres connection (POSTGRES_DSN)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
