from typing import Any
"""
CRM Sync Service — orchestrates bidirectional sync between VoiceOS and LeadSquared.

Outbound (VoiceOS → LeadSquared):
  sync_call_disposition()  — push call outcome + metadata
  sync_ptp()               — push promise-to-pay record
  sync_settlement()        — push settlement offer record

Inbound (LeadSquared → VoiceOS):
  import_leads()           — paginated import of LS leads into VoiceOS DB
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from uuid import UUID

import asyncpg

from .connectors.leadsquared import (
    LS_ACTIVITY_PHONE_CALL,
    LS_ACTIVITY_PTP,
    LS_ACTIVITY_SETTLEMENT,
    LeadSquaredConnector,
    LeadSquaredError,
    LSCredentials,
)

logger = logging.getLogger(__name__)

# ── Retry policy ──────────────────────────────────────────────────────────────
MAX_SYNC_ATTEMPTS = 5
RETRY_BACKOFF_S = [30, 120, 300, 900, 1800]  # 30s, 2m, 5m, 15m, 30m


class CRMSyncService:
    """
    Provides high-level sync operations. All methods accept a live asyncpg
    Connection (or Pool) — callers manage transactions.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ── Credential helpers ────────────────────────────────────────────────────

    async def _get_connector(self, tenant_id: str) -> LeadSquaredConnector | None:
        """Load LS credentials for tenant and return a configured connector, or None."""
        row = await self._pool.fetchrow(
            """SELECT access_key, secret_key, api_base_url
               FROM leadsquared_credentials
               WHERE tenant_id=$1 AND is_active=TRUE""",
            UUID(tenant_id),
        )
        if not row:
            return None
        return LeadSquaredConnector(
            LSCredentials(
                access_key=row["access_key"],
                secret_key=row["secret_key"],
                api_base_url=row["api_base_url"],
            )
        )

    async def _get_field_mapping(self, tenant_id: str) -> dict[str, str]:
        """Return voiceos_field → ls_field mapping (tenant overrides + defaults)."""
        rows = await self._pool.fetch(
            """SELECT d.voiceos_field,
                      COALESCE(t.ls_field, d.ls_field) AS ls_field
               FROM leadsquared_default_field_mapping d
               LEFT JOIN leadsquared_field_mapping t
                 ON t.voiceos_field=d.voiceos_field AND t.tenant_id=$1 AND t.is_active=TRUE""",
            UUID(tenant_id),
        )
        return {r["voiceos_field"]: r["ls_field"] for r in rows}

    # ── Sync log helpers ──────────────────────────────────────────────────────

    async def _upsert_sync_log(
        self,
        tenant_id: str,
        entity_type: str,
        entity_id: str,
        status: str = "PENDING",
        ls_lead_id: str | None = None,
        error: str | None = None,
    ) -> str:
        """Insert or update a sync_log record. Returns sync_id."""
        row = await self._pool.fetchrow(
            """INSERT INTO crm_sync_log
                 (tenant_id, entity_type, entity_id, sync_status, ls_lead_id, last_error)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (tenant_id, entity_type, entity_id) DO UPDATE SET
                 sync_status   = EXCLUDED.sync_status,
                 ls_lead_id    = COALESCE(EXCLUDED.ls_lead_id, crm_sync_log.ls_lead_id),
                 last_error    = EXCLUDED.last_error,
                 updated_at    = NOW()
               RETURNING sync_id""",
            UUID(tenant_id),
            entity_type,
            UUID(entity_id),
            status,
            ls_lead_id,
            error,
        )
        return str(row["sync_id"])

    async def _mark_synced(self, tenant_id: str, entity_type: str, entity_id: str, ls_lead_id: str) -> None:
        await self._pool.execute(
            """UPDATE crm_sync_log
               SET sync_status='SYNCED', ls_lead_id=$4, synced_at=NOW(),
                   updated_at=NOW(), last_error=NULL
               WHERE tenant_id=$1 AND entity_type=$2 AND entity_id=$3""",
            UUID(tenant_id),
            entity_type,
            UUID(entity_id),
            ls_lead_id,
        )

    async def _mark_failed(self, tenant_id: str, entity_type: str, entity_id: str, error: str) -> int:
        row = await self._pool.fetchrow(
            """UPDATE crm_sync_log
               SET sync_status = CASE WHEN attempt_count+1 >= $4 THEN 'FAILED' ELSE 'PENDING' END,
                   attempt_count = attempt_count + 1,
                   last_error = $5,
                   updated_at = NOW()
               WHERE tenant_id=$1 AND entity_type=$2 AND entity_id=$3
               RETURNING attempt_count""",
            UUID(tenant_id),
            entity_type,
            UUID(entity_id),
            MAX_SYNC_ATTEMPTS,
            error,
        )
        return row["attempt_count"] if row else 0

    # ── Outbound: disposition sync ────────────────────────────────────────────

    async def sync_call_disposition(self, call_id: str, tenant_id: str) -> bool:
        """
        Push a call's disposition + metadata to the matching LS lead.
        Looks up the lead by phone from call_runtime_records → leads → customer_contacts.
        Returns True on success.
        """
        await self._upsert_sync_log(tenant_id, "disposition", call_id, "PENDING")

        connector = await self._get_connector(tenant_id)
        if not connector:
            await self._upsert_sync_log(tenant_id, "disposition", call_id, "NO_CREDS")
            logger.info("CRMSync: no LS credentials for tenant=%s — skipping disposition %s", tenant_id, call_id)
            return False

        try:
            field_map = await self._get_field_mapping(tenant_id)

            # Fetch call details
            row = await self._pool.fetchrow(
                """SELECT r.disposition, r.duration_seconds, r.recording_url,
                          r.started_at, r.ended_at, l.phone
                   FROM call_runtime_records r
                   LEFT JOIN leads l ON l.lead_id = r.lead_id
                   WHERE r.call_sid = $1 OR r.lead_id::text = $1
                   LIMIT 1""",
                call_id,
            )
            if not row:
                await self._upsert_sync_log(tenant_id, "disposition", call_id, "SKIPPED", error="call record not found")
                return False

            phone = row["phone"]
            ls_lead = await connector.search_by_phone(phone) if phone else None
            if not ls_lead:
                await self._upsert_sync_log(
                    tenant_id, "disposition", call_id, "SKIPPED", error=f"no LS lead for phone={phone}"
                )
                logger.info("CRMSync: no LS lead for phone=%s — disposition skipped", phone)
                return False

            disposition = row["disposition"] or "UNKNOWN"
            duration_s = row["duration_seconds"] or 0
            recording_url = row["recording_url"] or ""
            call_dt = row["started_at"] or datetime.utcnow()

            note = f"VoiceOS Call — {disposition} ({duration_s}s)"

            extra_fields = [
                {"SchemaName": field_map.get("disposition", "mx_LastCallStatus"), "Value": disposition},
                {"SchemaName": field_map.get("call_duration_s", "mx_LastCallDuration"), "Value": str(duration_s)},
                {"SchemaName": field_map.get("call_date", "mx_LastCallDate"), "Value": call_dt.strftime("%Y-%m-%d")},
            ]
            if recording_url:
                extra_fields.append(
                    {
                        "SchemaName": field_map.get("call_recording_url", "mx_RecordingUrl"),
                        "Value": recording_url,
                    }
                )

            await connector.create_activity(
                ls_lead_id=ls_lead.lead_id,
                activity_event=LS_ACTIVITY_PHONE_CALL,
                note=note,
                extra_fields=extra_fields,
                activity_dt=call_dt,
            )
            await self._mark_synced(tenant_id, "disposition", call_id, ls_lead.lead_id)
            logger.info("CRMSync: disposition synced call=%s ls_lead=%s", call_id, ls_lead.lead_id)
            return True

        except (LeadSquaredError, Exception) as e:
            attempts = await self._mark_failed(tenant_id, "disposition", call_id, str(e))
            logger.warning("CRMSync: disposition sync failed call=%s attempt=%d: %s", call_id, attempts, e)
            return False
        finally:
            await connector.close()

    # ── Outbound: PTP sync ────────────────────────────────────────────────────

    async def sync_ptp(self, ptp_id: str, tenant_id: str) -> bool:
        """Push a promise-to-pay record to the LS lead."""
        await self._upsert_sync_log(tenant_id, "ptp", ptp_id, "PENDING")

        connector = await self._get_connector(tenant_id)
        if not connector:
            await self._upsert_sync_log(tenant_id, "ptp", ptp_id, "NO_CREDS")
            return False

        try:
            field_map = await self._get_field_mapping(tenant_id)

            row = await self._pool.fetchrow(
                """SELECT p.promised_amount_minor, p.promise_date, p.status,
                          cc.value AS phone, p.created_at
                   FROM promises_to_pay p
                   JOIN customers c ON c.customer_id = p.customer_id
                   JOIN customer_contacts cc ON cc.customer_id = c.customer_id
                     AND cc.contact_type = 'MOBILE' AND cc.is_primary = TRUE
                   WHERE p.ptp_id = $1
                   LIMIT 1""",
                UUID(ptp_id),
            )
            if not row:
                await self._upsert_sync_log(tenant_id, "ptp", ptp_id, "SKIPPED", error="ptp record not found")
                return False

            phone = row["phone"]
            ls_lead = await connector.search_by_phone(phone) if phone else None
            if not ls_lead:
                await self._upsert_sync_log(tenant_id, "ptp", ptp_id, "SKIPPED", error=f"no LS lead for phone={phone}")
                return False

            amount_inr = (row["promised_amount_minor"] or 0) / 100
            promise_date = row["promise_date"]

            extra_fields = [
                {"SchemaName": field_map.get("ptp_amount", "mx_PTPAmount"), "Value": str(amount_inr)},
                {
                    "SchemaName": field_map.get("ptp_date", "mx_PTPDate"),
                    "Value": promise_date.strftime("%Y-%m-%d")
                    if isinstance(promise_date, (date, datetime))
                    else str(promise_date),
                },
                {"SchemaName": field_map.get("ptp_status", "mx_PTPStatus"), "Value": row["status"]},
            ]

            await connector.create_activity(
                ls_lead_id=ls_lead.lead_id,
                activity_event=LS_ACTIVITY_PTP,
                note=f"PTP recorded: ₹{amount_inr:.2f} by {promise_date}",
                extra_fields=extra_fields,
                activity_dt=row["created_at"],
            )
            await connector.update_lead_fields(ls_lead.lead_id, extra_fields)
            await self._mark_synced(tenant_id, "ptp", ptp_id, ls_lead.lead_id)
            logger.info("CRMSync: PTP synced ptp=%s ls_lead=%s", ptp_id, ls_lead.lead_id)
            return True

        except (LeadSquaredError, Exception) as e:
            attempts = await self._mark_failed(tenant_id, "ptp", ptp_id, str(e))
            logger.warning("CRMSync: PTP sync failed ptp=%s attempt=%d: %s", ptp_id, attempts, e)
            return False
        finally:
            await connector.close()

    # ── Outbound: settlement sync ─────────────────────────────────────────────

    async def sync_settlement(self, settlement_id: str, tenant_id: str) -> bool:
        """Push a settlement offer to the LS lead."""
        await self._upsert_sync_log(tenant_id, "settlement", settlement_id, "PENDING")

        connector = await self._get_connector(tenant_id)
        if not connector:
            await self._upsert_sync_log(tenant_id, "settlement", settlement_id, "NO_CREDS")
            return False

        try:
            field_map = await self._get_field_mapping(tenant_id)

            row = await self._pool.fetchrow(
                """SELECT s.settlement_amount_minor, s.expiry_date, s.status,
                          cc.value AS phone, s.created_at
                   FROM settlements s
                   JOIN loan_accounts la ON la.loan_account_id = s.loan_account_id
                   JOIN customers c ON c.customer_id = la.customer_id
                   JOIN customer_contacts cc ON cc.customer_id = c.customer_id
                     AND cc.contact_type = 'MOBILE' AND cc.is_primary = TRUE
                   WHERE s.settlement_id = $1
                   LIMIT 1""",
                UUID(settlement_id),
            )
            if not row:
                await self._upsert_sync_log(
                    tenant_id, "settlement", settlement_id, "SKIPPED", error="settlement not found"
                )
                return False

            phone = row["phone"]
            ls_lead = await connector.search_by_phone(phone) if phone else None
            if not ls_lead:
                await self._upsert_sync_log(
                    tenant_id, "settlement", settlement_id, "SKIPPED", error=f"no LS lead for phone={phone}"
                )
                return False

            amount_inr = (row["settlement_amount_minor"] or 0) / 100
            expiry_date = row["expiry_date"]

            extra_fields = [
                {"SchemaName": field_map.get("settlement_amount", "mx_SettlementAmount"), "Value": str(amount_inr)},
                {
                    "SchemaName": field_map.get("settlement_expiry", "mx_SettlementExpiry"),
                    "Value": expiry_date.strftime("%Y-%m-%d")
                    if isinstance(expiry_date, (date, datetime))
                    else str(expiry_date),
                },
            ]

            await connector.create_activity(
                ls_lead_id=ls_lead.lead_id,
                activity_event=LS_ACTIVITY_SETTLEMENT,
                note=f"Settlement offer: ₹{amount_inr:.2f} valid till {expiry_date}",
                extra_fields=extra_fields,
                activity_dt=row["created_at"],
            )
            await connector.update_lead_fields(ls_lead.lead_id, extra_fields)
            await self._mark_synced(tenant_id, "settlement", settlement_id, ls_lead.lead_id)
            logger.info("CRMSync: settlement synced sid=%s ls_lead=%s", settlement_id, ls_lead.lead_id)
            return True

        except (LeadSquaredError, Exception) as e:
            attempts = await self._mark_failed(tenant_id, "settlement", settlement_id, str(e))
            logger.warning("CRMSync: settlement sync failed sid=%s attempt=%d: %s", settlement_id, attempts, e)
            return False
        finally:
            await connector.close()

    # ── Inbound: lead import from LeadSquared ─────────────────────────────────

    async def import_leads(
        self,
        tenant_id: str,
        campaign_id: str | None,
        filters: list[dict[str, Any]],
        max_leads: int = 10_000,
    ) -> dict[str, int]:
        """
        Pull leads from LeadSquared and import them into VoiceOS as customers + leads.
        Returns counts: {fetched, created, updated, skipped}.
        """
        connector = await self._get_connector(tenant_id)
        if not connector:
            logger.warning("CRMSync: import_leads — no LS credentials for tenant=%s", tenant_id)
            return {"fetched": 0, "created": 0, "updated": 0, "skipped": 0}

        import_id = await self._pool.fetchval(
            """INSERT INTO leadsquared_import_log
                 (tenant_id, campaign_id, status, filters)
               VALUES ($1, $2, 'RUNNING', $3::jsonb)
               RETURNING import_id""",
            UUID(tenant_id),
            UUID(campaign_id) if campaign_id else None,
            str(filters),
        )

        counts = {"fetched": 0, "created": 0, "updated": 0, "skipped": 0}
        try:
            start = 0
            page_size = 200
            async with connector:
                while counts["fetched"] < max_leads:
                    leads = await connector.fetch_leads_paginated(filters=filters, start=start, rows=page_size)
                    if not leads:
                        break

                    counts["fetched"] += len(leads)
                    for ls_lead in leads:
                        result = await self._upsert_customer_from_ls(tenant_id, campaign_id, ls_lead)
                        counts[result] += 1

                    if len(leads) < page_size:
                        break
                    start += page_size

            await self._pool.execute(
                """UPDATE leadsquared_import_log
                   SET status='DONE', leads_fetched=$2, leads_created=$3,
                       leads_updated=$4, leads_skipped=$5, completed_at=NOW()
                   WHERE import_id=$1""",
                import_id,
                counts["fetched"],
                counts["created"],
                counts["updated"],
                counts["skipped"],
            )
            logger.info("CRMSync: import done tenant=%s %s", tenant_id, counts)
            return counts

        except Exception as e:
            await self._pool.execute(
                """UPDATE leadsquared_import_log
                   SET status='FAILED', error_message=$2, completed_at=NOW()
                   WHERE import_id=$1""",
                import_id,
                str(e),
            )
            logger.error("CRMSync: import failed tenant=%s: %s", tenant_id, e)
            raise

    async def _upsert_customer_from_ls(
        self,
        tenant_id: str,
        campaign_id: str | None,
        ls_lead: Any,
    ) -> str:
        """
        Upsert a Customer record from a LS lead, then upsert a Lead record.
        Returns 'created' | 'updated' | 'skipped'.
        """
        if not ls_lead.phone:
            return "skipped"

        phone = ls_lead.phone.replace(" ", "").replace("-", "")
        try:
            # Upsert customer by crm_id (LS ProspectID)
            existing = await self._pool.fetchrow(
                """SELECT customer_id FROM customers
                   WHERE tenant_id=$1 AND crm_id=$2""",
                UUID(tenant_id),
                ls_lead.lead_id,
            )
            if existing:
                await self._pool.execute(
                    """UPDATE customers SET name=$3, updated_at=NOW()
                       WHERE tenant_id=$1 AND crm_id=$2""",
                    UUID(tenant_id),
                    ls_lead.lead_id,
                    ls_lead.name or "Unknown",
                )
                customer_id = existing["customer_id"]
                action = "updated"
            else:
                customer_id = await self._pool.fetchval(
                    """INSERT INTO customers (tenant_id, crm_id, name, preferred_language)
                       VALUES ($1, $2, $3, 'en')
                       ON CONFLICT (tenant_id, crm_id) DO UPDATE SET name=EXCLUDED.name
                       RETURNING customer_id""",
                    UUID(tenant_id),
                    ls_lead.lead_id,
                    ls_lead.name or "Unknown",
                )
                # Upsert primary mobile contact
                await self._pool.execute(
                    """INSERT INTO customer_contacts
                         (customer_id, contact_type, value, is_primary)
                       VALUES ($1, 'MOBILE', $2, TRUE)
                       ON CONFLICT DO NOTHING""",
                    customer_id,
                    phone,
                )
                action = "created"

            # Upsert lead record (only if campaign_id provided)
            if campaign_id:
                await self._pool.execute(
                    """INSERT INTO leads
                         (campaign_id, tenant_id, phone, name, queue_status,
                          status, metadata)
                       VALUES ($1, $2, $3, $4, 'PENDING', 'NEW', $5::jsonb)
                       ON CONFLICT (campaign_id, phone) DO NOTHING""",
                    UUID(campaign_id),
                    UUID(tenant_id),
                    phone,
                    ls_lead.name or "Unknown",
                    str({"ls_lead_id": ls_lead.lead_id}),
                )

            return action
        except Exception as e:
            logger.warning("CRMSync: upsert_customer failed phone=%s: %s", phone, e)
            return "skipped"
