"""Repository port for alert_history (ADR-006 Sec 4/7).

A real deployment backs this with ``psycopg2``/``BaseRepository``-style
tenant-scoped SQL (``alert_history``, migration 0033); unit tests inject an
in-memory fake, the same "Phase 1 local/mock, Phase 2 real infra" split
every prior sprint in this repo uses.
"""

from __future__ import annotations

from typing import Protocol

from src.services.ops_intelligence.models import AlertRecord, AlertStatus


class AlertRepositoryPort(Protocol):
    def create(self, alert: AlertRecord) -> AlertRecord: ...

    def get(self, alert_id: str) -> AlertRecord | None: ...

    def find_by_fingerprint_open(self, fingerprint: str) -> AlertRecord | None:
        """Most recent non-resolved alert with this fingerprint, if any (dedup on re-fire)."""
        ...

    def update_status(self, alert_id: str, alert: AlertRecord) -> AlertRecord: ...

    def list_open(self, tenant_id: str | None = None) -> tuple[AlertRecord, ...]: ...

    def count_by_status(self) -> dict[tuple[AlertStatus, str], int]:
        """Count of open alerts, keyed by (status, source), for the plumbing metrics gauge."""
        ...


__all__ = ["AlertRepositoryPort"]
