"""LeadSquared REST API connector for VoiceOS Phase 4 CRM integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ─── LeadSquared activity event IDs ──────────────────────────────────────────
# Default IDs — configurable per tenant via leadsquared_field_mapping table.
LS_ACTIVITY_PHONE_CALL = 206  # "Phone Call" built-in activity
LS_ACTIVITY_PTP = 208  # "Promise To Pay" (custom — may vary per account)
LS_ACTIVITY_SETTLEMENT = 209  # "Settlement Offer" (custom)


@dataclass
class LSCredentials:
    access_key: str
    secret_key: str
    api_base_url: str = "https://api.leadsquared.com"


@dataclass
class LSLead:
    lead_id: str
    phone: str
    name: str
    email: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


class LeadSquaredConnector:
    """
    Async HTTP client for the LeadSquared REST API.

    All methods raise LeadSquaredError on non-2xx responses.
    The caller is responsible for retry logic (handled in CRMSyncService).
    """

    def __init__(self, credentials: LSCredentials):
        self._creds = credentials
        self._client = httpx.AsyncClient(
            base_url=credentials.api_base_url,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            headers={"Content-Type": "application/json"},
        )

    def _auth(self) -> dict[str, str]:
        return {"accessKey": self._creds.access_key, "secretKey": self._creds.secret_key}

    async def _get(self, path: str, params: dict | None = None) -> Any:
        p = {**(params or {}), **self._auth()}
        resp = await self._client.get(path, params=p)
        self._raise_for_status(resp)
        return resp.json()

    async def _post(self, path: str, body: Any, params: dict | None = None) -> Any:
        p = {**(params or {}), **self._auth()}
        resp = await self._client.post(path, json=body, params=p)
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise LeadSquaredError(f"LeadSquared API error {resp.status_code}: {resp.text[:300]}")

    # ── Lead lookup ───────────────────────────────────────────────────────────

    async def search_by_phone(self, phone: str) -> LSLead | None:
        """Find a LeadSquared lead by mobile phone number. Returns None if not found."""
        try:
            data = await self._post(
                "/v2/LeadManagement.svc/Leads.Get",
                body={
                    "Parameter": {
                        "LookupName": "Phone",
                        "LookupValue": phone,
                    },
                    "Columns": {
                        "Include": {"ColumnName": ["ProspectID", "FirstName", "LastName", "EmailAddress", "Phone"]}
                    },
                    "Paging": {"Start": 0, "Rows": 1},
                },
            )
            leads = data.get("Leads") or []
            if not leads:
                return None
            raw = leads[0]
            return LSLead(
                lead_id=raw.get("ProspectID", ""),
                phone=raw.get("Phone", phone),
                name=f"{raw.get('FirstName', '')} {raw.get('LastName', '')}".strip(),
                email=raw.get("EmailAddress"),
                fields=raw,
            )
        except LeadSquaredError as e:
            logger.warning("LS search_by_phone failed phone=%s: %s", phone, e)
            return None

    async def get_lead(self, ls_lead_id: str) -> LSLead | None:
        """Fetch a single lead by its ProspectID."""
        try:
            data = await self._get(
                "/v2/LeadManagement.svc/RetrieveLead",
                params={"retrieveAssociatedData": "true", "leadId": ls_lead_id},
            )
            raw = data.get("Lead") or data
            return LSLead(
                lead_id=ls_lead_id,
                phone=raw.get("Phone", ""),
                name=f"{raw.get('FirstName', '')} {raw.get('LastName', '')}".strip(),
                email=raw.get("EmailAddress"),
                fields=raw,
            )
        except LeadSquaredError as e:
            logger.warning("LS get_lead failed lead_id=%s: %s", ls_lead_id, e)
            return None

    # ── Activity creation ─────────────────────────────────────────────────────

    async def create_activity(
        self,
        ls_lead_id: str,
        activity_event: int,
        note: str,
        extra_fields: list[dict[str, str]],
        activity_dt: datetime | None = None,
    ) -> str:
        """
        Post an activity (call outcome, PTP, settlement) to a LS lead.
        Returns the created activity ID.
        """
        dt_str = (activity_dt or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
        body = {
            "ActivityEvent": activity_event,
            "ActivityNote": note,
            "ActivityDateTime": dt_str,
            "ProspectId": ls_lead_id,
            "Fields": extra_fields,
        }
        data = await self._post("/v2/ProspectActivity.svc/Create", body=body)
        return str(data.get("ActivityId") or data.get("Id") or "")

    # ── Lead field update ─────────────────────────────────────────────────────

    async def update_lead_fields(
        self,
        ls_lead_id: str,
        attributes: list[dict[str, str]],
    ) -> bool:
        """
        Update custom fields on a LS lead.
        attributes: [{"SchemaName": "mx_Field", "Value": "..."}]
        """
        body = {
            "LeadId": ls_lead_id,
            "Attribute": attributes,
        }
        await self._post("/v2/LeadManagement.svc/Lead.UpdateMapped", body=body)
        return True

    # ── Paginated lead fetch (for import) ─────────────────────────────────────

    async def fetch_leads_paginated(
        self,
        filters: list[dict],
        start: int = 0,
        rows: int = 200,
        columns: list[str] | None = None,
    ) -> list[LSLead]:
        """
        Fetch a page of leads from LeadSquared using filter conditions.
        filters: [{"Attribute": {"FieldName": "...", "Operator": "Equal", "Value": "..."}}]
        """
        default_cols = ["ProspectID", "FirstName", "LastName", "Phone", "EmailAddress", "CreatedOn", "ModifiedOn"]
        body = {
            "Parameter": {
                "LookupName": "AdvancedSearch",
                "AdvancedFilter": {"FilterRule": filters},
            },
            "Columns": {"Include": {"ColumnName": columns or default_cols}},
            "Paging": {"Start": start, "Rows": rows},
            "Sorting": {"ColumnName": "ModifiedOn", "Direction": "Descending"},
        }
        data = await self._post("/v2/LeadManagement.svc/Leads.Get", body=body)
        raw_leads = data.get("Leads") or []
        return [
            LSLead(
                lead_id=r.get("ProspectID", ""),
                phone=r.get("Phone", ""),
                name=f"{r.get('FirstName', '')} {r.get('LastName', '')}".strip(),
                email=r.get("EmailAddress"),
                fields=r,
            )
            for r in raw_leads
        ]

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()


class LeadSquaredError(Exception):
    """Raised when the LeadSquared API returns an error response."""
