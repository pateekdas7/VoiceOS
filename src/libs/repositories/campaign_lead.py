"""CampaignLeadRepository — tenant-scoped CRUD for campaign lead records.

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

import json
from typing import Any

from ..contracts.models.pipeline import (
    CampaignLead,
    LeadLanguage,
    LeadQueueStatus,
    LeadStatus,
)
from ..contracts.primitives import CampaignId, LeadId, PipelineId, TenantId
from .base import BaseRepository

_TABLE = "campaign_leads"

_COLUMNS = (
    "lead_id",
    "tenant_id",
    "campaign_id",
    "pipeline_id",
    "import_id",
    "phone",
    "phone_raw",
    "name",
    "email",
    "language",
    "score",
    "status",
    "queue_status",
    "is_duplicate",
    "is_blacklisted",
    "rejection_reason",
    "metadata",
    "created_at",
)


class CampaignLeadRepository(BaseRepository):
    """Tenant-scoped CRUD for campaign lead records."""

    def create(self, lead: CampaignLead) -> CampaignLead:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                lead_id, tenant_id, campaign_id, pipeline_id, import_id,
                phone, phone_raw, name, email, language, score, status,
                queue_status, is_duplicate, is_blacklisted, rejection_reason,
                metadata, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s
            )
            """,
            (
                lead.lead_id,
                lead.tenant_id,
                lead.campaign_id,
                lead.pipeline_id,
                lead.import_id,
                lead.phone,
                lead.phone_raw,
                lead.name,
                lead.email,
                lead.language.value,
                lead.score,
                lead.status.value,
                lead.queue_status.value,
                lead.is_duplicate,
                lead.is_blacklisted,
                lead.rejection_reason,
                json.dumps(lead.metadata),
                lead.created_at,
            ),
        )
        self._commit()
        return lead

    def bulk_create(self, leads: list[CampaignLead]) -> int:
        """Insert multiple leads in a single transaction. Returns inserted count."""
        if not leads:
            return 0
        for lead in leads:
            self._execute(
                f"""
                INSERT INTO {_TABLE} (
                    lead_id, tenant_id, campaign_id, pipeline_id, import_id,
                    phone, phone_raw, name, email, language, score, status,
                    queue_status, is_duplicate, is_blacklisted, rejection_reason,
                    metadata, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    lead.lead_id,
                    lead.tenant_id,
                    lead.campaign_id,
                    lead.pipeline_id,
                    lead.import_id,
                    lead.phone,
                    lead.phone_raw,
                    lead.name,
                    lead.email,
                    lead.language.value,
                    lead.score,
                    lead.status.value,
                    lead.queue_status.value,
                    lead.is_duplicate,
                    lead.is_blacklisted,
                    lead.rejection_reason,
                    json.dumps(lead.metadata),
                    lead.created_at,
                ),
            )
        self._commit()
        return len(leads)

    def get(self, tenant_id: TenantId, lead_id: LeadId) -> CampaignLead | None:
        row = self._tenant_select_one(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="lead_id = %s",
            extra_params=(lead_id,),
        )
        return self._hydrate(row) if row is not None else None

    def phone_exists_in_campaign(self, campaign_id: CampaignId, phone: str) -> bool:
        """Return True if a non-duplicate lead with this phone already exists in the campaign."""
        cur = self._execute(
            f"SELECT 1 FROM {_TABLE} WHERE campaign_id = %s AND phone = %s AND is_duplicate = FALSE LIMIT 1",
            (campaign_id, phone),
        )
        return cur.fetchone() is not None

    def find_by_campaign(
        self,
        tenant_id: TenantId,
        campaign_id: CampaignId,
        *,
        status: str | None = None,
        pipeline_id: str | None = None,
        search: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[CampaignLead, ...]:
        extra_where_parts = ["campaign_id = %s"]
        extra_params: list[Any] = [campaign_id]

        if status:
            extra_where_parts.append("status = %s")
            extra_params.append(status)
        if pipeline_id == "unassigned":
            extra_where_parts.append("pipeline_id IS NULL")
        elif pipeline_id:
            extra_where_parts.append("pipeline_id = %s")
            extra_params.append(pipeline_id)
        if search:
            extra_where_parts.append("(name ILIKE %s OR phone ILIKE %s)")
            extra_params.extend([f"%{search}%", f"%{search}%"])

        cols = ", ".join(_COLUMNS)
        where_clause = " AND ".join(extra_where_parts)
        sql = (
            f"SELECT {cols} FROM {_TABLE}"
            f" WHERE tenant_id = %s AND {where_clause}"
            f" ORDER BY score DESC, created_at ASC"
            f" LIMIT %s OFFSET %s"
        )
        params = [tenant_id, *extra_params, limit, offset]
        cur = self._execute(sql, params)
        rows = cur.fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def find_by_pipeline(
        self,
        tenant_id: TenantId,
        pipeline_id: PipelineId,
        *,
        search: str | None = None,
        limit: int = 500,
    ) -> tuple[CampaignLead, ...]:
        extra_where = "pipeline_id = %s"
        extra_params: list[Any] = [pipeline_id]
        if search:
            extra_where += " AND (name ILIKE %s OR phone ILIKE %s)"
            extra_params.extend([f"%{search}%", f"%{search}%"])
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where=extra_where,
            extra_params=extra_params,
            order_by="score DESC, created_at ASC",
            limit=limit,
        )
        return tuple(self._hydrate(row) for row in rows)

    def stats_for_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> dict[str, Any]:
        cur = self._execute(
            """
            SELECT
                COUNT(*)                                        AS total,
                COUNT(*) FILTER (WHERE is_duplicate = FALSE
                                   AND is_blacklisted = FALSE
                                   AND rejection_reason IS NULL) AS valid,
                COUNT(*) FILTER (WHERE rejection_reason IS NOT NULL
                                    OR is_blacklisted = TRUE)    AS rejected,
                COUNT(*) FILTER (WHERE is_duplicate = TRUE)      AS duplicates,
                COUNT(*) FILTER (WHERE pipeline_id IS NOT NULL)  AS assigned,
                COUNT(*) FILTER (WHERE queue_status = 'QUEUED')  AS queued,
                ROUND(AVG(score), 1)                             AS avg_score
            FROM campaign_leads
            WHERE tenant_id = %s AND campaign_id = %s
            """,
            (tenant_id, campaign_id),
        )
        row = cur.fetchone()
        if row is None:
            return {"total": "0", "valid": "0", "rejected": "0",
                    "duplicates": "0", "assigned": "0", "queued": "0", "avg_score": None}
        total, valid, rejected, duplicates, assigned, queued, avg_score = row
        return {
            "total": str(total or 0),
            "valid": str(valid or 0),
            "rejected": str(rejected or 0),
            "duplicates": str(duplicates or 0),
            "assigned": str(assigned or 0),
            "queued": str(queued or 0),
            "avg_score": str(avg_score) if avg_score is not None else None,
        }

    def stats_for_pipeline(self, tenant_id: TenantId, pipeline_id: PipelineId) -> dict[str, Any]:
        cur = self._execute(
            """
            SELECT
                COUNT(*)                                             AS total,
                COUNT(*) FILTER (WHERE queue_status = 'QUEUED')     AS queued,
                COUNT(*) FILTER (WHERE queue_status = 'IN_CALL')    AS in_call,
                COUNT(*) FILTER (WHERE queue_status = 'DONE')       AS done,
                ROUND(AVG(score), 1)                                 AS avg_score
            FROM campaign_leads
            WHERE tenant_id = %s AND pipeline_id = %s
            """,
            (tenant_id, pipeline_id),
        )
        row = cur.fetchone()
        if row is None:
            return {"total": "0", "queued": "0", "in_call": "0", "done": "0", "avg_score": None}
        total, queued, in_call, done, avg_score = row
        return {
            "total": str(total or 0),
            "queued": str(queued or 0),
            "in_call": str(in_call or 0),
            "done": str(done or 0),
            "avg_score": str(avg_score) if avg_score is not None else None,
        }

    def assign_pipeline(self, tenant_id: TenantId, lead_id: LeadId, pipeline_id: PipelineId) -> None:
        self._tenant_update(
            _TABLE,
            ("pipeline_id", "status"),
            (pipeline_id, LeadStatus.ASSIGNED.value),
            tenant_id,
            extra_where="lead_id = %s",
            extra_params=(lead_id,),
        )

    def update_queue_status(
        self,
        tenant_id: TenantId,
        lead_id: LeadId,
        queue_status: LeadQueueStatus,
        *,
        call_sid: str | None = None,
    ) -> None:
        """Update queue_status (and optionally store call_sid in metadata)."""
        if call_sid is not None:
            cur = self._execute(
                f"SELECT metadata FROM {_TABLE} WHERE tenant_id = %s AND lead_id = %s LIMIT 1",
                (tenant_id, lead_id),
            )
            row = cur.fetchone()
            existing: dict = {}
            if row:
                existing = row[0] if isinstance(row[0], dict) else (json.loads(row[0]) if row[0] else {})
            existing["call_sid"] = call_sid
            self._tenant_update(
                _TABLE,
                ("queue_status", "metadata"),
                (queue_status.value, json.dumps(existing)),
                tenant_id,
                extra_where="lead_id = %s",
                extra_params=(lead_id,),
            )
        else:
            self._tenant_update(
                _TABLE,
                ("queue_status",),
                (queue_status.value,),
                tenant_id,
                extra_where="lead_id = %s",
                extra_params=(lead_id,),
            )

    def find_queued_for_campaign(
        self,
        tenant_id: TenantId,
        campaign_id: CampaignId,
        *,
        limit: int = 500,
    ) -> tuple[CampaignLead, ...]:
        """Return PENDING leads (not yet queued) ordered by score DESC for dialer seeding."""
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s AND queue_status = %s AND is_duplicate = FALSE AND is_blacklisted = FALSE",
            extra_params=(campaign_id, LeadQueueStatus.PENDING.value),
            order_by="score DESC",
            limit=limit,
        )
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> CampaignLead:
        (
            lead_id, tenant_id, campaign_id, pipeline_id, import_id,
            phone, phone_raw, name, email, language, score, status,
            queue_status, is_duplicate, is_blacklisted, rejection_reason,
            metadata, created_at,
        ) = row
        raw_metadata = metadata if isinstance(metadata, dict) else (json.loads(metadata) if metadata else {})
        return CampaignLead(
            lead_id=LeadId(str(lead_id)),
            tenant_id=TenantId(str(tenant_id)),
            campaign_id=CampaignId(str(campaign_id)),
            pipeline_id=PipelineId(str(pipeline_id)) if pipeline_id is not None else None,
            import_id=str(import_id) if import_id is not None else None,
            phone=phone,
            phone_raw=phone_raw,
            name=name or "",
            email=email,
            language=LeadLanguage(language),
            score=score,
            status=LeadStatus(status),
            queue_status=LeadQueueStatus(queue_status),
            is_duplicate=is_duplicate,
            is_blacklisted=is_blacklisted,
            rejection_reason=rejection_reason,
            metadata={str(k): str(v) for k, v in raw_metadata.items()},
            created_at=created_at,
        )
