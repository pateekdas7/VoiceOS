"""LeadIntakeService — processes raw CSV rows into CampaignLead records.

Intake pipeline per row (in order):
  1. Map client columns → standard fields via caller-supplied column_mapping.
  2. Normalize phone to E.164; reject row if invalid.
  3. Deduplicate within the same campaign (phone uniqueness gate).
  4. Score the lead (0–100).
  5. Detect language from metadata.
  6. Build CampaignLead; mark is_duplicate if phone already known.

After all rows are processed the LeadDistributionEngine distributes valid
leads across the campaign's pipelines (if any are ACTIVE).

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.pipeline import (
    CampaignLead,
    LeadImport,
    LeadImportStatus,
    LeadQueueStatus,
    LeadStatus,
)
from src.libs.contracts.primitives import CampaignId, LeadId, PipelineId, TenantId
from src.libs.event_bus.publisher import Publisher

from . import metrics as _metrics
from .lead_distribution import LeadDistributionEngine, detect_language, normalize_phone, score_lead

if TYPE_CHECKING:
    from src.libs.repositories.campaign_lead import CampaignLeadRepository
    from src.libs.repositories.lead_import import LeadImportRepository
    from src.libs.repositories.pipeline import PipelineRepository


# Standard field → CSV column aliases used for auto-suggest
_STANDARD_ALIASES: dict[str, list[str]] = {
    "phone": ["phone", "mobile", "mobile no", "contact", "phone no", "phone number",
               "mobileno", "mobile_no", "phoneno", "phone_no", "mob"],
    "name": ["name", "customer name", "full name", "fullname", "customer_name"],
    "email": ["email", "email address", "email_address", "emailid", "email id"],
    "loan_amount": ["loan amount", "loan_amount", "loanamount", "loan"],
    "dpd": ["dpd", "days past due", "days_past_due", "dayspastdue", "overdue days"],
    "outstanding": ["outstanding", "outstanding amount", "outstanding_amount", "balance",
                    "due amount", "due_amount"],
    "product_type": ["product type", "product_type", "producttype", "product"],
    "city": ["city", "district"],
    "state": ["state", "province"],
    "language": ["language", "preferred language", "lang"],
}


def suggest_column_mapping(columns: list[str]) -> dict[str, str]:
    """Return {csv_column: standard_field} for each column that fuzzy-matches a standard field."""
    mapping: dict[str, str] = {}
    for col in columns:
        col_lower = col.lower().strip()
        for field, aliases in _STANDARD_ALIASES.items():
            if col_lower in aliases:
                mapping[col] = field
                break
    return mapping


class LeadIntakeResult:
    """Summary of one import operation (returned to the BFF route handler)."""

    __slots__ = ("import_id", "total", "valid", "invalid", "duplicates")

    def __init__(self, import_id: str, total: int, valid: int, invalid: int, duplicates: int) -> None:
        self.import_id = import_id
        self.total = total
        self.valid = valid
        self.invalid = invalid
        self.duplicates = duplicates


class LeadIntakeService:
    """Processes uploaded CSV rows into CampaignLead records and distributes across pipelines."""

    def __init__(
        self,
        lead_repository: CampaignLeadRepository,
        import_repository: LeadImportRepository,
        pipeline_repository: PipelineRepository,
        distribution_engine: LeadDistributionEngine | None = None,
        publisher: Publisher | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._lead_repo = lead_repository
        self._import_repo = import_repository
        self._pipeline_repo = pipeline_repository
        self._distribution = distribution_engine or LeadDistributionEngine()
        self._publisher = publisher
        self._audit_logger = audit_logger

    def ingest(
        self,
        tenant_id: TenantId,
        campaign_id: CampaignId,
        filename: str,
        rows: list[dict[str, str]],
        column_mapping: dict[str, str],
        pipeline_ids: list[PipelineId],
        *,
        uploaded_by: str,
    ) -> LeadIntakeResult:
        """Process ``rows`` into CampaignLead records, then distribute to pipelines.

        Args:
            tenant_id: Owning tenant.
            campaign_id: Target campaign.
            filename: Original file name (stored in the audit record).
            rows: Raw CSV rows — list of {col_header: value} dicts.
            column_mapping: {csv_col: standard_field} as supplied by the client.
            pipeline_ids: Active pipeline IDs to distribute leads into. May be empty.
            uploaded_by: User ID or email of the uploader (for audit).

        Returns:
            LeadIntakeResult with import_id and row counts.
        """
        now = datetime.now(UTC)
        import_id = str(uuid.uuid4())

        # Create the import audit record immediately so the client can poll status.
        lead_import = LeadImport(
            import_id=import_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            filename=filename,
            status=LeadImportStatus.PROCESSING,
            total_rows=len(rows),
            created_at=now,
        )
        self._import_repo.create(lead_import)

        # Track normalized phones seen in this batch to catch within-batch duplicates.
        seen_phones: set[str] = set()
        valid_leads: list[CampaignLead] = []
        invalid_count = 0
        duplicate_count = 0

        for row in rows:
            mapped = _apply_mapping(row, column_mapping)
            phone_raw = mapped.get("phone", "").strip()
            phone = normalize_phone(phone_raw)
            if phone is None:
                invalid_count += 1
                continue

            # Dedup: check both this batch and existing campaign leads.
            is_dup = (
                phone in seen_phones
                or self._lead_repo.phone_exists_in_campaign(campaign_id, phone)
            )

            name = mapped.get("name", "").strip()
            email = mapped.get("email", "").strip() or None
            metadata = _build_metadata(mapped)
            language = detect_language(metadata)
            lead_score = score_lead(metadata, has_name=bool(name), has_email=bool(email))

            if is_dup:
                duplicate_count += 1

            lead = CampaignLead(
                lead_id=LeadId(str(uuid.uuid4())),
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                pipeline_id=None,
                import_id=import_id,
                phone=phone,
                phone_raw=phone_raw,
                name=name,
                email=email,
                language=language,
                score=lead_score,
                status=LeadStatus.REJECTED if is_dup else LeadStatus.VALIDATED,
                queue_status=LeadQueueStatus.PENDING,
                is_duplicate=is_dup,
                is_blacklisted=False,
                rejection_reason="DUPLICATE_PHONE" if is_dup else None,
                metadata=metadata,
                created_at=now,
            )
            valid_leads.append(lead)
            if not is_dup:
                seen_phones.add(phone)

        non_duplicate_count = len(valid_leads) - duplicate_count

        # Distribute valid (non-duplicate) leads across pipelines.
        if pipeline_ids and valid_leads:
            valid_leads = self._distribution.distribute(valid_leads, pipeline_ids)

        # Persist all leads (including duplicates as rejected records).
        self._lead_repo.bulk_create(valid_leads)

        # Finalize the import record.
        self._import_repo.complete(
            tenant_id,
            import_id,
            total_rows=len(rows),
            valid_rows=non_duplicate_count,
            invalid_rows=invalid_count,
            duplicate_rows=duplicate_count,
            status=LeadImportStatus.DONE,
        )

        _metrics.record_leads_imported(str(tenant_id), non_duplicate_count)

        if self._publisher is not None:
            self._publisher.publish(
                event_type="saas.campaign.leads_imported",
                tenant_id=tenant_id,
                payload={
                    "campaign_id": campaign_id,
                    "import_id": import_id,
                    "total_rows": len(rows),
                    "valid_rows": non_duplicate_count,
                    "invalid_rows": invalid_count,
                    "duplicate_rows": duplicate_count,
                    "uploaded_by": uploaded_by,
                },
                correlation_id=import_id,
            )

        if self._audit_logger is not None:
            self._audit_logger.record(
                tenant_id,
                uploaded_by,
                "campaign.leads_imported",
                "Campaign",
                campaign_id,
                "SUCCESS",
                event_payload={"import_id": import_id, "valid_rows": non_duplicate_count},
            )

        return LeadIntakeResult(
            import_id=import_id,
            total=len(rows),
            valid=non_duplicate_count,
            invalid=invalid_count,
            duplicates=duplicate_count,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _apply_mapping(row: dict[str, str], column_mapping: dict[str, str]) -> dict[str, str]:
    """Remap CSV columns to standard field names using column_mapping."""
    result: dict[str, str] = {}
    for csv_col, value in row.items():
        standard_field = column_mapping.get(csv_col, "")
        if standard_field:
            result[standard_field] = value
        else:
            result[csv_col] = value
    return result


_METADATA_FIELDS = frozenset({"dpd", "outstanding", "product_type", "city", "state", "language", "loan_amount"})


def _build_metadata(mapped: dict[str, str]) -> dict[str, str]:
    """Extract scoring/language metadata from a mapped row."""
    return {k: v for k, v in mapped.items() if k in _METADATA_FIELDS and v}
