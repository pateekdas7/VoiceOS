"""Tenant-scoped telephony number resolution.

Provider phone numbers are the routing boundary between a Twilio account and
a VoiceOS tenant. Provider credentials are never stored here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TelephonyNumber:
    tenant_id: str
    e164_number: str
    provider: str
    provider_number_id: str
    campaign_id: str | None


class TelephonyNumberResolver:
    """Resolve active provider numbers to their owning tenant."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def resolve_for_call(self, number: str, *, direction: str) -> TelephonyNumber | None:
        if not number or direction not in {"inbound", "outbound-api", "outbound-dial"}:
            return None
        inbound = direction == "inbound"
        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                SELECT tenant_id, e164_number, provider, provider_number_id, campaign_id
                FROM telephony_phone_numbers
                WHERE e164_number = %s
                  AND status = 'ACTIVE'
                  AND provider = 'twilio'
                  AND (CASE WHEN %s THEN inbound_enabled ELSE outbound_enabled END) = TRUE
                LIMIT 1
                """,
                (number, inbound),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        if row is None:
            return None
        return TelephonyNumber(
            tenant_id=str(row[0]),
            e164_number=str(row[1]),
            provider=str(row[2]),
            provider_number_id=str(row[3]),
            campaign_id=str(row[4]) if row[4] is not None else None,
        )
