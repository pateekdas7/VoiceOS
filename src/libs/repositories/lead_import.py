"""LeadImportRepository — tenant-scoped CRUD for lead import audit records.

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.pipeline import LeadImport, LeadImportStatus
from ..contracts.primitives import CampaignId, TenantId
from .base import BaseRepository

_TABLE = "lead_imports"

_COLUMNS = (
    "import_id",
    "tenant_id",
    "campaign_id",
    "filename",
    "status",
    "total_rows",
    "valid_rows",
    "invalid_rows",
    "duplicate_rows",
    "created_at",
    "completed_at",
)


class LeadImportRepository(BaseRepository):
    """Tenant-scoped CRUD for lead import records."""

    def create(self, lead_import: LeadImport) -> LeadImport:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                import_id, tenant_id, campaign_id, filename, status,
                total_rows, valid_rows, invalid_rows, duplicate_rows,
                created_at, completed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                lead_import.import_id,
                lead_import.tenant_id,
                lead_import.campaign_id,
                lead_import.filename,
                lead_import.status.value,
                lead_import.total_rows,
                lead_import.valid_rows,
                lead_import.invalid_rows,
                lead_import.duplicate_rows,
                lead_import.created_at,
                lead_import.completed_at,
            ),
        )
        self._commit()
        return lead_import

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[LeadImport, ...]:
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
            order_by="created_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def complete(
        self,
        tenant_id: TenantId,
        import_id: str,
        *,
        total_rows: int,
        valid_rows: int,
        invalid_rows: int,
        duplicate_rows: int,
        status: LeadImportStatus = LeadImportStatus.DONE,
    ) -> None:
        from datetime import UTC, datetime

        self._tenant_update(
            _TABLE,
            ("status", "total_rows", "valid_rows", "invalid_rows", "duplicate_rows", "completed_at"),
            (status.value, total_rows, valid_rows, invalid_rows, duplicate_rows, datetime.now(UTC)),
            tenant_id,
            extra_where="import_id = %s",
            extra_params=(import_id,),
        )

    def _hydrate(self, row: tuple[Any, ...]) -> LeadImport:
        (
            import_id, tenant_id, campaign_id, filename, status,
            total_rows, valid_rows, invalid_rows, duplicate_rows,
            created_at, completed_at,
        ) = row
        return LeadImport(
            import_id=str(import_id),
            tenant_id=TenantId(str(tenant_id)),
            campaign_id=CampaignId(str(campaign_id)),
            filename=filename,
            status=LeadImportStatus(status),
            total_rows=total_rows,
            valid_rows=valid_rows,
            invalid_rows=invalid_rows,
            duplicate_rows=duplicate_rows,
            created_at=created_at,
            completed_at=completed_at,
        )
