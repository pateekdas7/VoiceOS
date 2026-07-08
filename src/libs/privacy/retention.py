"""RetentionScheduler — flags data past its retention period (V4 Ch9 §9.12/§9.13).

Architecture: V4 Ch9 §9.13 (default retention: recordings 90d, transcripts
180d, lineage 365d), §9.12 ("per-data-class TTLs enforced automatically;
legal hold overrides").
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from src.libs.privacy.purpose_registry import DataClass

DEFAULT_RETENTION_DAYS: dict[DataClass, int] = {
    DataClass.VOICE_RECORDING: 90,
    DataClass.TRANSCRIPT: 180,
}


class RetentionScheduler:
    """Flags records whose age exceeds their data class's retention period."""

    def __init__(self, retention_days: dict[DataClass, int] | None = None) -> None:
        self._retention_days = dict(retention_days or DEFAULT_RETENTION_DAYS)

    def is_expired(
        self, data_class: DataClass, created_at: datetime, *, legal_hold: bool = False, now: datetime | None = None
    ) -> bool:
        """True if ``created_at`` is older than the data class's retention period.

        A legal hold (V4 Ch9 §9.13 "legal_hold_overrides_retention") always
        overrides expiry, regardless of age.
        """
        if legal_hold:
            return False
        days = self._retention_days.get(data_class)
        if days is None:
            return False
        current = now if now is not None else datetime.now(UTC)
        return current - created_at > timedelta(days=days)

    def flag_expired(
        self,
        data_class: DataClass,
        records: Sequence[tuple[str, datetime]],
        *,
        legal_holds: frozenset[str] = frozenset(),
        now: datetime | None = None,
    ) -> list[str]:
        """Return the record_ids in ``records`` whose retention period has expired."""
        return [
            record_id
            for record_id, created_at in records
            if self.is_expired(data_class, created_at, legal_hold=record_id in legal_holds, now=now)
        ]
