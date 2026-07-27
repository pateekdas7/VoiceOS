"""PostgresPatternSignatureRepository -- backs PatternSignatureRepositoryPort against
``ops_pattern_signatures`` (migration 0032). Plain hash/counter persistence -- no ML/learned state.
"""

from __future__ import annotations

import json
from typing import Any

from src.libs.repositories.base import BaseRepository
from src.services.ops_intelligence.models import PatternSignature

_TABLE = "ops_pattern_signatures"
_COLUMNS = (
    "signature_id",
    "tenant_id",
    "fingerprint_hash",
    "category",
    "affected_components",
    "root_cause_summary",
    "first_seen",
    "last_seen",
    "occurrence_count",
)


class PostgresPatternSignatureRepository(BaseRepository):
    """Real Postgres-backed ``PatternSignatureRepositoryPort`` implementation."""

    def find(self, fingerprint_hash: str, *, tenant_id: str | None) -> PatternSignature | None:
        where = "fingerprint_hash = %s"
        params: list[Any] = [fingerprint_hash]
        if tenant_id is not None:
            where += " AND tenant_id = %s"
            params.append(tenant_id)
        else:
            where += " AND tenant_id IS NULL"
        cur = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE {where}", params)
        row = cur.fetchone()
        return _row_to_signature(row) if row is not None else None

    def upsert_occurrence(self, signature: PatternSignature) -> PatternSignature:
        existing = self.find(signature.fingerprint_hash, tenant_id=signature.tenant_id)
        if existing is None:
            self._execute(
                f"""
                INSERT INTO {_TABLE} (
                    signature_id, tenant_id, fingerprint_hash, category, affected_components,
                    root_cause_summary, first_seen, last_seen, occurrence_count
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                """,
                (
                    signature.signature_id,
                    signature.tenant_id,
                    signature.fingerprint_hash,
                    signature.category,
                    json.dumps(list(signature.affected_components)),
                    signature.root_cause_summary,
                    signature.first_seen,
                    signature.last_seen,
                    signature.occurrence_count,
                ),
            )
        else:
            self._execute(
                f"UPDATE {_TABLE} SET last_seen = %s, occurrence_count = %s WHERE signature_id = %s",
                (signature.last_seen, signature.occurrence_count, existing.signature_id),
            )
        self._commit()
        return signature


def _row_to_signature(row: tuple[Any, ...]) -> PatternSignature:
    signature_id, tenant_id, fingerprint_hash, category, affected_components, root_cause_summary, first_seen, last_seen, occurrence_count = row
    return PatternSignature(
        signature_id=str(signature_id),
        tenant_id=str(tenant_id) if tenant_id is not None else None,
        fingerprint_hash=fingerprint_hash,
        category=category,
        affected_components=tuple(json.loads(affected_components) if isinstance(affected_components, str) else affected_components),
        root_cause_summary=root_cause_summary,
        first_seen=first_seen,
        last_seen=last_seen,
        occurrence_count=occurrence_count,
    )


__all__ = ["PostgresPatternSignatureRepository"]
