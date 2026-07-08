"""AuditVerifier — walks a tenant's audit hash chain and proves (or disproves) integrity.

Architecture: V4 Ch11 (Audit Architecture) §11.7 (``verify_integrity``),
§11.11 (forensic reconstruction), §11.12 (hash chain).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.libs.repositories.audit import GENESIS_HASH, compute_audit_hash


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of walking a tenant's hash chain."""

    valid: bool
    verified_count: int
    broken_at_seq: int | None = None
    broken_at_audit_id: str | None = None


class AuditVerifier:
    """Recomputes a tenant's audit hash chain independently and compares it to storage."""

    def __init__(self, audit_repository: Any) -> None:
        self._repo = audit_repository

    def verify_chain(self, tenant_id: Any, start: Any = None, end: Any = None) -> VerificationResult:
        """Walk ``tenant_id``'s chain in ``seq`` order; stop at the first mismatch."""
        rows = self._repo.iter_chain(tenant_id, start, end)
        prev_hash = GENESIS_HASH
        verified = 0

        for row in rows:
            (
                audit_id,
                row_tenant_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                outcome,
                _ip_address,
                event_payload,
                _recorded_at,
                seq,
                stored_prev_hash,
                stored_hash,
            ) = row

            expected_hash = compute_audit_hash(
                prev_hash, row_tenant_id, actor_id, action, resource_type, resource_id, outcome, event_payload
            )

            if stored_prev_hash != prev_hash or stored_hash != expected_hash:
                return VerificationResult(
                    valid=False,
                    verified_count=verified,
                    broken_at_seq=seq,
                    broken_at_audit_id=str(audit_id),
                )

            prev_hash = stored_hash
            verified += 1

        return VerificationResult(valid=True, verified_count=verified)
