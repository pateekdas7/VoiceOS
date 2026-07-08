"""Shared DDL helpers for Sprint-014 Alembic revisions.

Postgres has no ``ADD CONSTRAINT IF NOT EXISTS`` syntax, so idempotent CHECK
constraint addition (required because these revisions may run against a
database that already has the Sprint-002 tables) uses a guarded ``DO`` block
that checks ``pg_constraint`` first. This is the standard Postgres idiom for
idempotent constraint DDL.

Architecture: V6 Ch7 (expand-contract migrations — additive, reversible).
"""

from __future__ import annotations

from alembic import op


def add_enum_check(table: str, name: str, column: str, values: tuple[str, ...]) -> None:
    """Idempotently add a CHECK constraint restricting ``column`` to ``values``.

    Args:
        table: Table name (unquoted, must be a safe identifier).
        name: Constraint name (unique across the database).
        column: Column the constraint applies to.
        values: Allowed string values.
    """
    values_sql = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{name}'
            ) THEN
                ALTER TABLE {table}
                    ADD CONSTRAINT {name} CHECK ({column} IN ({values_sql}));
            END IF;
        END $$;
        """
    )


def drop_check(table: str, name: str) -> None:
    """Drop a CHECK constraint if it exists (reversible companion to add_enum_check)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def add_unique_if_missing(table: str, name: str, columns: tuple[str, ...]) -> None:
    """Idempotently add a UNIQUE constraint over ``columns``.

    Must be used (rather than an inline ``CONSTRAINT ... UNIQUE`` in a
    ``CREATE TABLE IF NOT EXISTS`` body) whenever the table may already exist
    from a prior sprint's raw-SQL migration: the inline form is silently
    skipped when ``IF NOT EXISTS`` finds the table already present, so a
    constraint introduced by a later revision would never actually be applied
    to an already-deployed database.

    Args:
        table: Table name (unquoted, must be a safe identifier).
        name: Constraint name (unique across the database).
        columns: Column names forming the unique key.
    """
    cols_sql = ", ".join(columns)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{name}'
            ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({cols_sql});
            END IF;
        END $$;
        """
    )


def drop_constraint(table: str, name: str) -> None:
    """Drop any named constraint if it exists (reversible companion helper)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
