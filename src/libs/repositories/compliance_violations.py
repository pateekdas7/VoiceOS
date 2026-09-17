"""ComplianceViolationRepository — durable per-(tenant, rule) violation lifecycle store (V4 Ch16, Phase 6d).

Replaces the in-memory _violated_tenants set with a Postgres-backed table that
survives service restarts. One row per (tenant_id, rule_id) pair.

Lifecycle states:
    ACTIVE   — rule threshold crossed; tenant is in violation.
    RESOLVED — violation cleared (manual or automated); tenant is compliant for this rule.

Re-detection: when ingest() fires a rule that was previously RESOLVED, upsert_active()
flips it back to ACTIVE and records redetected_at for audit trail.

Architecture: V4 Ch16 (Compliance Monitoring) §16.7 (Public Interfaces).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..contracts.primitives import TenantId
from .base import BaseRepository

_TABLE = "compliance_violations"

_CV_COLUMNS = (
    "violation_id",
    "tenant_id",
    "rule_id",
    "signal_summary",
    "status",
    "detected_at",
    "resolved_at",
    "redetected_at",
)


class ComplianceViolationRepository(BaseRepository):
    """Durable per-(tenant, rule) compliance violation lifecycle store."""

    def upsert_active(
        self,
        tenant_id: TenantId,
        rule_id: str,
        signal_summary: str,
    ) -> None:
        """Record a new violation or re-activate a resolved one (idempotent).

        Behaviour on conflict (same tenant_id + rule_id already exists):
        - Was RESOLVED → flips to ACTIVE, sets redetected_at to now.
        - Already ACTIVE → updates signal_summary only (no timestamp churn).

        Uses ON CONFLICT ON CONSTRAINT uq_compliance_violations_tenant_rule so
        the caller can invoke this method on every signal without risk of
        duplicate rows or phantom re-detections.
        """
        now = datetime.now(UTC)
        self._execute(
            f"""
            INSERT INTO {_TABLE} (tenant_id, rule_id, signal_summary, status, detected_at)
            VALUES (%s, %s, %s, 'ACTIVE', %s)
            ON CONFLICT ON CONSTRAINT uq_compliance_violations_tenant_rule
            DO UPDATE SET
                status         = 'ACTIVE',
                signal_summary = EXCLUDED.signal_summary,
                redetected_at  = CASE
                    WHEN {_TABLE}.status = 'RESOLVED' THEN EXCLUDED.detected_at
                    ELSE {_TABLE}.redetected_at
                END
            """,
            (str(tenant_id), rule_id, signal_summary, now),
        )
        self._commit()

    def is_violated(self, tenant_id: TenantId) -> bool:
        """Return True if the tenant has at least one ACTIVE violation."""
        cur = self._execute(
            f"SELECT 1 FROM {_TABLE} WHERE tenant_id = %s AND status = 'ACTIVE' LIMIT 1",
            (str(tenant_id),),
        )
        return cur.fetchone() is not None

    def list_active(self, tenant_id: TenantId) -> tuple[dict[str, Any], ...]:
        """Return all ACTIVE violations for a tenant, newest first."""
        rows = self._tenant_select(
            _TABLE,
            _CV_COLUMNS,
            str(tenant_id),
            extra_where="status = 'ACTIVE'",
            order_by="detected_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def resolve(self, tenant_id: TenantId, rule_id: str) -> None:
        """Mark a specific (tenant, rule) violation as RESOLVED.

        No-op if no ACTIVE row exists for the given (tenant, rule).
        """
        self._tenant_update(
            _TABLE,
            ("status", "resolved_at"),
            ("RESOLVED", datetime.now(UTC)),
            str(tenant_id),
            extra_where="rule_id = %s AND status = 'ACTIVE'",
            extra_params=(rule_id,),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> dict[str, Any]:
        violation_id, tenant_id, rule_id, signal_summary, status, detected_at, resolved_at, redetected_at = row
        return {
            "violation_id": str(violation_id),
            "tenant_id": str(tenant_id),
            "rule_id": rule_id,
            "signal_summary": signal_summary,
            "status": status,
            "detected_at": detected_at.isoformat() if detected_at else None,
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
            "redetected_at": redetected_at.isoformat() if redetected_at else None,
        }
