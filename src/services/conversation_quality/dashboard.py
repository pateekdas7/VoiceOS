"""QualityDashboard — quality trend API for analytics and monitoring.

Stores per-call quality records and exposes a trend query that returns
a time-ordered list of quality records for a given tenant.

Sprint-012 uses in-memory storage. Sprint-027 will write these records
to TimescaleDB / the analytics pipeline.

Architecture: V5 Ch11 (Analytics).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityRecord:
    """A single quality measurement record for one call session."""

    call_id: str
    tenant_id: str
    grade: str
    weighted_score: float
    turn_count: int
    recorded_at: datetime


class QualityDashboard:
    """In-memory quality trend store with tenant-scoped query API.

    Architecture: V5 Ch11.
    """

    def __init__(self) -> None:
        self._records: list[QualityRecord] = []

    def record(
        self,
        call_id: str,
        tenant_id: str,
        grade: str,
        weighted_score: float,
        turn_count: int,
    ) -> None:
        """Persist a quality record for a completed call.

        Args:
            call_id: Call session identifier.
            tenant_id: Tenant scope (AR-8).
            grade: Quality grade ('A'-'F').
            weighted_score: Aggregate weighted quality score [0.0, 1.0].
            turn_count: Number of turns in this call.
        """
        rec = QualityRecord(
            call_id=call_id,
            tenant_id=tenant_id,
            grade=grade,
            weighted_score=weighted_score,
            turn_count=turn_count,
            recorded_at=datetime.utcnow(),
        )
        self._records.append(rec)
        logger.debug("QualityDashboard: recorded call %s grade=%s", call_id, grade)

    def get_quality_trend(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
    ) -> list[QualityRecord]:
        """Return quality records for a tenant within a time range.

        Args:
            tenant_id: Tenant to filter by.
            start: Inclusive start timestamp (UTC).
            end: Inclusive end timestamp (UTC).

        Returns:
            List of QualityRecord, ordered by recorded_at ascending.
        """
        results = [r for r in self._records if r.tenant_id == tenant_id and start <= r.recorded_at <= end]
        results.sort(key=lambda r: r.recorded_at)
        return results
