"""CustomerImporter — bulk customer import, validated + deduplicated (V5 Ch3).

Sprint-022.md's file list names this module ``import.py`` — not possible in
Python (``import`` is a reserved keyword; ``from package import import`` is a
syntax error, not just a lint warning). Named ``importer.py`` instead
(Sprint-022 deviation; see CHANGELOG.md).

Only CSV import is implemented via the standard library ``csv`` module.
XLSX parsing would require a new third-party dependency (e.g. ``openpyxl``),
which CLAUDE.md's Dependency Policy requires be explicitly justified before
adding — out of scope to decide unilaterally in this sprint. Callers needing
XLSX can parse it externally into the same row-dict shape ``import_rows()``
already accepts.

Architecture: V5 Ch3 (Customer CRM); Invariant RI-5 (external ``crm_id`` is
the deduplication key — never invented).
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.libs.contracts.models.customer import Customer, CustomerContact
from src.libs.contracts.primitives import CustomerId, TenantId

from .service import CustomerService

REQUIRED_FIELDS = ("crm_id", "name")


@dataclass
class ImportResult:
    """Outcome of one import run."""

    imported: int = 0
    skipped_duplicates: int = 0
    errors: list[str] = field(default_factory=list)


class CustomerImporter:
    """Validates and imports customer rows, deduplicating on ``crm_id`` (V5 Ch3)."""

    def __init__(self, customer_service: CustomerService) -> None:
        self._service = customer_service

    def import_csv(self, tenant_id: TenantId, csv_text: str) -> ImportResult:
        """Import customers from CSV text.

        Expected columns: ``crm_id``, ``name``, ``phone`` (optional),
        ``preferred_language`` (optional, default ``en``).
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        return self.import_rows(tenant_id, list(reader))

    def import_rows(self, tenant_id: TenantId, rows: list[dict[str, str]]) -> ImportResult:
        result = ImportResult()
        for line_number, row in enumerate(rows, start=2):  # header is row 1
            error = self._validate(row)
            if error is not None:
                result.errors.append(f"row {line_number}: {error}")
                continue

            crm_id = row["crm_id"].strip()
            if self._service.find_by_external_id(tenant_id, crm_id) is not None:
                result.skipped_duplicates += 1
                continue

            now = datetime.now(UTC)
            phone = row.get("phone", "").strip()
            contacts = (CustomerContact(contact_type="MOBILE", value=phone, is_primary=True),) if phone else ()
            customer = Customer(
                customer_id=CustomerId(str(uuid.uuid4())),
                tenant_id=tenant_id,
                crm_id=crm_id,
                name=row["name"].strip(),
                preferred_language=row.get("preferred_language", "en").strip() or "en",
                contacts=contacts,
                created_at=now,
                updated_at=now,
            )
            self._service.create(customer)
            result.imported += 1
        return result

    @staticmethod
    def _validate(row: dict[str, str]) -> str | None:
        for required in REQUIRED_FIELDS:
            if not row.get(required, "").strip():
                return f"missing required field '{required}'"
        return None
