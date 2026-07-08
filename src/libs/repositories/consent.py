"""ConsentRepository — DPDP/RBI consent state + audit trail (V4 Ch2).

Architecture: V4 Ch2 (DPDP Compliance); Invariant RI-5 (consent is authoritative).
"""

from __future__ import annotations

from typing import Any

from ..contracts.context import ConsentStatus
from ..contracts.models.consent import Consent, ConsentType
from ..contracts.primitives import CustomerId, TenantId
from .base import BaseRepository

_TABLE = "consents"
_RECORDS_TABLE = "consent_records"

_CONSENT_COLUMNS = (
    "consent_id",
    "tenant_id",
    "customer_id",
    "consent_type",
    "status",
    "granted_at",
    "revoked_at",
    "expires_at",
    "created_at",
    "updated_at",
)


class ConsentRepository(BaseRepository):
    """Tenant-scoped consent state queries + immutable grant/revoke history."""

    def check_consent(self, tenant_id: TenantId, customer_id: CustomerId, consent_type: ConsentType) -> Consent | None:
        """Fetch the current consent state for a customer + consent type, scoped to ``tenant_id``."""
        row = self._tenant_select_one(
            _TABLE,
            _CONSENT_COLUMNS,
            tenant_id,
            extra_where="customer_id = %s AND consent_type = %s",
            extra_params=(customer_id, consent_type.value),
        )
        return self._hydrate(row) if row is not None else None

    def record_grant(
        self,
        tenant_id: TenantId,
        customer_id: CustomerId,
        consent_type: ConsentType,
        actor_id: str,
        channel: str,
    ) -> Consent:
        """Grant (or re-grant) consent: upserts current state + appends an immutable history row."""
        return self._upsert_and_record(
            tenant_id, customer_id, consent_type, ConsentStatus.GRANTED, actor_id, channel, action="GRANTED"
        )

    def record_revoke(
        self,
        tenant_id: TenantId,
        customer_id: CustomerId,
        consent_type: ConsentType,
        actor_id: str,
        channel: str,
    ) -> Consent:
        """Revoke consent: upserts current state + appends an immutable history row."""
        return self._upsert_and_record(
            tenant_id, customer_id, consent_type, ConsentStatus.REVOKED, actor_id, channel, action="REVOKED"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _upsert_and_record(
        self,
        tenant_id: TenantId,
        customer_id: CustomerId,
        consent_type: ConsentType,
        status: ConsentStatus,
        actor_id: str,
        channel: str,
        *,
        action: str,
    ) -> Consent:
        granted_at_expr = "NOW()" if status == ConsentStatus.GRANTED else "consents.granted_at"
        revoked_at_expr = "NOW()" if status == ConsentStatus.REVOKED else "consents.revoked_at"

        cur = self._execute(
            f"""
            INSERT INTO {_TABLE} (
                tenant_id, customer_id, consent_type, status, granted_at, revoked_at,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, {"NOW()" if status == ConsentStatus.GRANTED else "NULL"},
                      {"NOW()" if status == ConsentStatus.REVOKED else "NULL"}, NOW(), NOW())
            ON CONFLICT (customer_id, consent_type) DO UPDATE SET
                status = EXCLUDED.status,
                granted_at = {granted_at_expr},
                revoked_at = {revoked_at_expr},
                updated_at = NOW()
            RETURNING {", ".join(_CONSENT_COLUMNS)}
            """,
            (tenant_id, customer_id, consent_type.value, status.value),
        )
        row = cur.fetchone()
        consent = self._hydrate(row)

        self._execute(
            f"""
            INSERT INTO {_RECORDS_TABLE} (consent_id, action, actor_id, channel, recorded_at)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (consent.consent_id, action, actor_id, channel),
        )
        self._commit()
        return consent

    def _hydrate(self, row: tuple[Any, ...]) -> Consent:
        (
            consent_id,
            tenant_id,
            customer_id,
            consent_type,
            status,
            granted_at,
            revoked_at,
            expires_at,
            created_at,
            updated_at,
        ) = row
        return Consent(
            consent_id=str(consent_id),
            tenant_id=TenantId(tenant_id),
            customer_id=CustomerId(customer_id),
            consent_type=ConsentType(consent_type),
            status=ConsentStatus(status),
            granted_at=granted_at,
            revoked_at=revoked_at,
            expires_at=expires_at,
            created_at=created_at,
            updated_at=updated_at,
        )
