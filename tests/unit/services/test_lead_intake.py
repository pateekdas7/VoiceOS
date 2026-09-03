"""Unit tests for LeadIntakeService, LeadDistributionEngine, and helpers.

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from src.libs.contracts.models.pipeline import (
    CampaignLead,
    LeadImport,
    LeadImportStatus,
    LeadLanguage,
    LeadQueueStatus,
    LeadStatus,
)
from src.libs.contracts.primitives import CampaignId, LeadId, PipelineId, TenantId
from src.services.campaign_management.lead_distribution import (
    LeadDistributionEngine,
    detect_language,
    normalize_phone,
    score_lead,
)
from src.services.campaign_management.lead_intake import (
    LeadIntakeService,
    suggest_column_mapping,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / test doubles
# ─────────────────────────────────────────────────────────────────────────────

TENANT = TenantId("t-001")
CAMPAIGN = CampaignId("c-001")
NOW = datetime.now(UTC)


def _make_lead(
    phone: str = "+919876543210",
    score: int = 50,
    is_duplicate: bool = False,
    pipeline_id: PipelineId | None = None,
) -> CampaignLead:
    return CampaignLead(
        lead_id=LeadId(str(uuid.uuid4())),
        tenant_id=TENANT,
        campaign_id=CAMPAIGN,
        pipeline_id=pipeline_id,
        phone=phone,
        phone_raw=phone,
        name="Test User",
        score=score,
        is_duplicate=is_duplicate,
        status=LeadStatus.REJECTED if is_duplicate else LeadStatus.VALIDATED,
        queue_status=LeadQueueStatus.PENDING,
        created_at=NOW,
    )


class _FakeLeadRepository:
    def __init__(self, existing_phones: set[str] | None = None) -> None:
        self._phones: set[str] = existing_phones or set()
        self.saved: list[CampaignLead] = []

    def phone_exists_in_campaign(self, campaign_id: CampaignId, phone: str) -> bool:
        return phone in self._phones

    def bulk_create(self, leads: list[CampaignLead]) -> int:
        for lead in leads:
            if not lead.is_duplicate:
                self._phones.add(lead.phone)
        self.saved.extend(leads)
        return len(leads)


class _FakeImportRepository:
    def __init__(self) -> None:
        self.records: list[LeadImport] = []
        self.completions: list[dict] = []

    def create(self, record: LeadImport) -> LeadImport:
        self.records.append(record)
        return record

    def complete(self, tenant_id: TenantId, import_id: str, **kwargs: object) -> None:
        self.completions.append({"import_id": import_id, **kwargs})


class _FakePipelineRepository:
    def find_active_for_campaign(self, *_: object) -> tuple:
        return ()


# ─────────────────────────────────────────────────────────────────────────────
# normalize_phone
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizePhone:
    def test_ten_digit_mobile(self) -> None:
        assert normalize_phone("9876543210") == "+919876543210"

    def test_ten_digit_with_spaces(self) -> None:
        assert normalize_phone("98765 43210") == "+919876543210"

    def test_ten_digit_with_dashes(self) -> None:
        assert normalize_phone("987-654-3210") == "+919876543210"

    def test_twelve_digit_with_country_code(self) -> None:
        assert normalize_phone("919876543210") == "+919876543210"

    def test_eleven_digit_with_leading_zero(self) -> None:
        assert normalize_phone("09876543210") == "+919876543210"

    def test_thirteen_digit_with_091(self) -> None:
        assert normalize_phone("0919876543210") == "+919876543210"

    def test_invalid_short(self) -> None:
        assert normalize_phone("12345") is None

    def test_invalid_landline_prefix(self) -> None:
        assert normalize_phone("1234567890") is None

    def test_already_e164(self) -> None:
        assert normalize_phone("+919876543210") == "+919876543210"


# ─────────────────────────────────────────────────────────────────────────────
# score_lead
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreLead:
    def test_zero_dpd_zero_outstanding(self) -> None:
        score = score_lead({"dpd": "0", "outstanding": "0"}, has_name=False, has_email=False)
        assert score == 15  # phone_score only

    def test_high_dpd_high_outstanding(self) -> None:
        score = score_lead({"dpd": "120", "outstanding": "200000"}, has_name=True, has_email=True)
        # 40 (dpd) + 20 (outstanding) + 10 (name) + 5 (email) + 15 (phone) = 90
        assert score == 90
        assert score <= 100

    def test_name_and_email_contribute(self) -> None:
        score_with = score_lead({}, has_name=True, has_email=True)
        score_without = score_lead({}, has_name=False, has_email=False)
        assert score_with > score_without
        assert score_with - score_without == 15  # 10 name + 5 email

    def test_dpd_tiers(self) -> None:
        tiers = [
            ("0", 0), ("15", 10), ("45", 20), ("75", 30), ("100", 40),
        ]
        for dpd, expected_dpd_component in tiers:
            s = score_lead({"dpd": dpd}, has_name=False, has_email=False)
            assert s == expected_dpd_component + 15, f"dpd={dpd}: expected {expected_dpd_component + 15}, got {s}"

    def test_invalid_dpd_defaults_zero(self) -> None:
        score = score_lead({"dpd": "abc"}, has_name=False, has_email=False)
        assert score == 15


# ─────────────────────────────────────────────────────────────────────────────
# detect_language
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectLanguage:
    def test_explicit_hindi(self) -> None:
        assert detect_language({"language": "HINDI"}) == LeadLanguage.HINDI

    def test_explicit_tamil_lowercase(self) -> None:
        assert detect_language({"language": "tamil"}) == LeadLanguage.TAMIL

    def test_state_maharashtra(self) -> None:
        assert detect_language({"state": "Maharashtra"}) == LeadLanguage.MARATHI

    def test_state_karnataka(self) -> None:
        assert detect_language({"state": "Karnataka"}) == LeadLanguage.KANNADA

    def test_state_code_tn(self) -> None:
        assert detect_language({"state": "TN"}) == LeadLanguage.TAMIL

    def test_fallback_hindi(self) -> None:
        assert detect_language({}) == LeadLanguage.HINDI

    def test_explicit_overrides_state(self) -> None:
        assert detect_language({"language": "TELUGU", "state": "Maharashtra"}) == LeadLanguage.TELUGU


# ─────────────────────────────────────────────────────────────────────────────
# LeadDistributionEngine
# ─────────────────────────────────────────────────────────────────────────────

class TestLeadDistributionEngine:
    def _engine(self) -> LeadDistributionEngine:
        return LeadDistributionEngine()

    def test_no_pipelines_leaves_unassigned(self) -> None:
        leads = [_make_lead(f"+9198765432{i:02d}", score=50) for i in range(3)]
        result = self._engine().distribute(leads, [])
        assert all(l.pipeline_id is None for l in result)

    def test_single_pipeline_all_assigned(self) -> None:
        leads = [_make_lead(f"+9198765432{i:02d}", score=50) for i in range(5)]
        p1 = PipelineId("p-001")
        result = self._engine().distribute(leads, [p1])
        assert all(l.pipeline_id == p1 for l in result)

    def test_round_robin_two_pipelines(self) -> None:
        leads = [_make_lead(f"+9198765432{i:02d}", score=50 - i) for i in range(4)]
        p1, p2 = PipelineId("p-001"), PipelineId("p-002")
        result = self._engine().distribute(leads, [p1, p2])
        pipeline_ids = [l.pipeline_id for l in result]
        # After score-sort descending and round-robin: alternates p1/p2
        assert pipeline_ids.count(p1) == 2
        assert pipeline_ids.count(p2) == 2

    def test_duplicates_skipped(self) -> None:
        leads = [
            _make_lead("+919876543210", score=80),
            _make_lead("+919876543211", score=60, is_duplicate=True),
            _make_lead("+919876543212", score=40),
        ]
        p1 = PipelineId("p-001")
        result = self._engine().distribute(leads, [p1])
        # Duplicate remains unassigned
        assert result[1].pipeline_id is None
        # Others assigned
        assert result[0].pipeline_id == p1
        assert result[2].pipeline_id == p1

    def test_highest_score_distributed_first(self) -> None:
        leads = [
            _make_lead("+919876543210", score=20),
            _make_lead("+919876543211", score=80),
            _make_lead("+919876543212", score=50),
        ]
        p1, p2 = PipelineId("p-001"), PipelineId("p-002")
        result = self._engine().distribute(leads, [p1, p2])
        # Score 80 → p1, score 50 → p2, score 20 → p1
        by_phone = {l.phone: l.pipeline_id for l in result}
        assert by_phone["+919876543211"] == p1  # highest score → first pipeline
        assert by_phone["+919876543212"] == p2
        assert by_phone["+919876543210"] == p1  # round-robin wraps

    def test_models_are_immutable_copies(self) -> None:
        lead = _make_lead("+919876543210", score=50)
        result = self._engine().distribute([lead], [PipelineId("p-001")])
        assert result[0] is not lead
        assert lead.pipeline_id is None  # original unchanged


# ─────────────────────────────────────────────────────────────────────────────
# suggest_column_mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestSuggestColumnMapping:
    def test_phone_aliases(self) -> None:
        for col in ["Mobile No", "phone", "contact", "mob"]:
            mapping = suggest_column_mapping([col])
            assert mapping.get(col) == "phone", f"col={col!r} did not map to 'phone'"

    def test_name_alias(self) -> None:
        mapping = suggest_column_mapping(["Customer Name"])
        assert mapping["Customer Name"] == "name"

    def test_unknown_column_not_in_mapping(self) -> None:
        mapping = suggest_column_mapping(["random_column"])
        assert "random_column" not in mapping

    def test_multiple_columns(self) -> None:
        mapping = suggest_column_mapping(["Name", "Mobile No", "DPD", "Outstanding"])
        assert mapping["Name"] == "name"
        assert mapping["Mobile No"] == "phone"
        assert mapping["DPD"] == "dpd"
        assert mapping["Outstanding"] == "outstanding"


# ─────────────────────────────────────────────────────────────────────────────
# LeadIntakeService
# ─────────────────────────────────────────────────────────────────────────────

class TestLeadIntakeService:
    def _service(
        self, existing_phones: set[str] | None = None
    ) -> tuple[LeadIntakeService, _FakeLeadRepository, _FakeImportRepository]:
        lead_repo = _FakeLeadRepository(existing_phones)
        import_repo = _FakeImportRepository()
        pipeline_repo = _FakePipelineRepository()
        svc = LeadIntakeService(lead_repo, import_repo, pipeline_repo)
        return svc, lead_repo, import_repo

    def _rows(self, phones: list[str]) -> list[dict[str, str]]:
        return [{"phone": p, "name": f"Customer {i}"} for i, p in enumerate(phones)]

    def _mapping(self) -> dict[str, str]:
        return {"phone": "phone", "name": "name"}

    def test_valid_import_counts(self) -> None:
        svc, lead_repo, import_repo = self._service()
        result = svc.ingest(
            TENANT, CAMPAIGN, "test.csv",
            self._rows(["9876543210", "9876543211", "9876543212"]),
            self._mapping(), [],
            uploaded_by="user-1",
        )
        assert result.total == 3
        assert result.valid == 3
        assert result.invalid == 0
        assert result.duplicates == 0
        assert len(lead_repo.saved) == 3

    def test_invalid_phones_rejected(self) -> None:
        svc, lead_repo, import_repo = self._service()
        result = svc.ingest(
            TENANT, CAMPAIGN, "test.csv",
            self._rows(["9876543210", "12345", "abcdef"]),
            self._mapping(), [],
            uploaded_by="user-1",
        )
        assert result.total == 3
        assert result.valid == 1
        assert result.invalid == 2
        assert len(lead_repo.saved) == 1

    def test_within_batch_duplicates(self) -> None:
        svc, lead_repo, import_repo = self._service()
        result = svc.ingest(
            TENANT, CAMPAIGN, "test.csv",
            self._rows(["9876543210", "9876543210"]),  # same phone twice
            self._mapping(), [],
            uploaded_by="user-1",
        )
        assert result.total == 2
        assert result.valid == 1
        assert result.duplicates == 1
        dup_leads = [l for l in lead_repo.saved if l.is_duplicate]
        assert len(dup_leads) == 1
        assert dup_leads[0].rejection_reason == "DUPLICATE_PHONE"

    def test_cross_import_duplicates(self) -> None:
        svc, lead_repo, import_repo = self._service(
            existing_phones={"+919876543210"}  # already in campaign
        )
        result = svc.ingest(
            TENANT, CAMPAIGN, "test.csv",
            self._rows(["9876543210"]),
            self._mapping(), [],
            uploaded_by="user-1",
        )
        assert result.duplicates == 1
        assert result.valid == 0

    def test_import_record_created_and_completed(self) -> None:
        svc, lead_repo, import_repo = self._service()
        result = svc.ingest(
            TENANT, CAMPAIGN, "batch.csv",
            self._rows(["9876543210"]),
            self._mapping(), [],
            uploaded_by="user-1",
        )
        assert len(import_repo.records) == 1
        assert import_repo.records[0].filename == "batch.csv"
        assert len(import_repo.completions) == 1
        completion = import_repo.completions[0]
        assert completion["status"] == LeadImportStatus.DONE
        assert completion["valid_rows"] == 1

    def test_leads_distributed_to_pipelines(self) -> None:
        svc, lead_repo, import_repo = self._service()
        p1, p2 = PipelineId("p-001"), PipelineId("p-002")
        result = svc.ingest(
            TENANT, CAMPAIGN, "batch.csv",
            self._rows(["9876543210", "9876543211", "9876543212", "9876543213"]),
            self._mapping(), [p1, p2],
            uploaded_by="user-1",
        )
        assert result.valid == 4
        assigned = [l for l in lead_repo.saved if l.pipeline_id is not None]
        assert len(assigned) == 4
        p1_count = sum(1 for l in assigned if l.pipeline_id == p1)
        p2_count = sum(1 for l in assigned if l.pipeline_id == p2)
        assert p1_count == 2
        assert p2_count == 2

    def test_phone_normalized_to_e164(self) -> None:
        svc, lead_repo, import_repo = self._service()
        svc.ingest(
            TENANT, CAMPAIGN, "batch.csv",
            [{"phone": "9876543210", "name": "Test"}],
            self._mapping(), [],
            uploaded_by="user-1",
        )
        assert lead_repo.saved[0].phone == "+919876543210"
        assert lead_repo.saved[0].phone_raw == "9876543210"

    def test_result_import_id_set(self) -> None:
        svc, _lead_repo, _import_repo = self._service()
        result = svc.ingest(
            TENANT, CAMPAIGN, "batch.csv",
            self._rows(["9876543210"]),
            self._mapping(), [],
            uploaded_by="user-1",
        )
        assert result.import_id  # non-empty UUID string
        import uuid as _uuid
        _uuid.UUID(result.import_id)  # must be a valid UUID — raises if not
