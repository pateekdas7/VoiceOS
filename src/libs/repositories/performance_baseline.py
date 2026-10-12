"""PerformanceBaselineRepository -- Postgres-backed store for ContinuousProfiler's daily percentiles.

Not tenant-scoped (fleet-level operational data, not customer data) --
does not compose ``BaseRepository``'s mechanically tenant-scoped helpers,
same precedent as other global-scope operational tables. Satisfies
``src.libs.performance_engineering.profiler.BaselineStore`` structurally
(no inheritance) -- a real deployment injects an instance of this class
directly wherever a ``BaselineStore`` is expected.

Architecture: V3 Ch19 (Performance Engineering); migration 0027.
"""

from __future__ import annotations

from datetime import date as date_
from typing import Any

from src.libs.performance_engineering.profiler import StagePercentiles

_DEFAULT_ENVIRONMENT = "production"


class PerformanceBaselineRepository:
    """Postgres-backed ``performance_baselines`` access -- upsert-per-(stage, environment, day)."""

    def __init__(self, conn: Any, *, environment: str = _DEFAULT_ENVIRONMENT) -> None:
        """
        Args:
            conn: A psycopg2 connection (or compatible test double) exposing
                ``cursor()`` and ``commit()``.
            environment: The deployment environment this repository instance
                writes/reads rows for (e.g. "staging", "production").
        """
        self._conn = conn
        self._environment = environment

    def record_percentiles(self, percentiles: StagePercentiles) -> None:
        """Upsert one stage's daily percentiles (idempotent -- re-running the same day's flush is safe)."""
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO performance_baselines
                (stage_name, environment, recorded_date, p50_ms, p95_ms, p99_ms, sample_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stage_name, environment, recorded_date)
            DO UPDATE SET
                p50_ms = EXCLUDED.p50_ms,
                p95_ms = EXCLUDED.p95_ms,
                p99_ms = EXCLUDED.p99_ms,
                sample_count = EXCLUDED.sample_count
            """,
            (
                percentiles.stage_name,
                self._environment,
                percentiles.recorded_date,
                percentiles.p50_ms,
                percentiles.p95_ms,
                percentiles.p99_ms,
                percentiles.sample_count,
            ),
        )
        self._conn.commit()

    def percentiles_for(self, stage_name: str, recorded_date: date_) -> StagePercentiles | None:
        """The registered baseline for ``stage_name`` on ``recorded_date``, or ``None`` if never recorded."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT stage_name, recorded_date, p50_ms, p95_ms, p99_ms, sample_count
            FROM performance_baselines
            WHERE stage_name = %s AND environment = %s AND recorded_date = %s
            """,
            (stage_name, self._environment, recorded_date),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return StagePercentiles(
            stage_name=row[0],
            recorded_date=row[1],
            p50_ms=row[2],
            p95_ms=row[3],
            p99_ms=row[4],
            sample_count=row[5],
        )

    def most_recent_percentiles(self, stage_name: str) -> StagePercentiles | None:
        """The most recently recorded baseline for ``stage_name`` in this environment, regardless of date.

        Used to seed ``RegressionDetector`` when "yesterday's" exact date
        has no row yet (e.g. the first run after a weekend gap).
        """
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT stage_name, recorded_date, p50_ms, p95_ms, p99_ms, sample_count
            FROM performance_baselines
            WHERE stage_name = %s AND environment = %s
            ORDER BY recorded_date DESC
            LIMIT 1
            """,
            (stage_name, self._environment),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return StagePercentiles(
            stage_name=row[0],
            recorded_date=row[1],
            p50_ms=row[2],
            p95_ms=row[3],
            p99_ms=row[4],
            sample_count=row[5],
        )


__all__ = ["PerformanceBaselineRepository"]
