"""Unit tests for Campaign Management (Sprint-023): lifecycle, scheduler, A/B testing,
audience selection, retry policy, dispatch.

Architecture: V5 Ch6 (Campaign Management); V4 Ch2 (RBI calling hours).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from src.libs.contracts.models.campaign import (
    ABTestVariant,
    AudienceCriteria,
    Campaign,
    CampaignAudienceMember,
    CampaignStatus,
    RetryPolicy,
)
from src.libs.contracts.primitives import CampaignId, TenantId
from src.services.campaign_management.ab_testing import ABTestingFramework
from src.services.campaign_management.audience import AudienceSelector
from src.services.campaign_management.call_dispatcher import CallDispatcher
from src.services.campaign_management.lifecycle import CampaignLifecycle, CampaignLifecycleError
from src.services.campaign_management.retry_policy import RetryPolicyEngine
from src.services.campaign_management.scheduler import ScheduleEngine
from src.services.campaign_management.service import CampaignPromptNotPinnedError, CampaignService
from tests.fixtures.policy import FakePolicyEngineService

TENANT = TenantId("tenant-a")


def _make_campaign(status: CampaignStatus = CampaignStatus.DRAFT, **overrides: object) -> Campaign:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "campaign_id": CampaignId(str(uuid.uuid4())),
        "tenant_id": TENANT,
        "name": "Test Campaign",
        "status": status,
        "audience_criteria": AudienceCriteria(min_dpd=30, min_outstanding_minor=10_000),
        "retry_policy": RetryPolicy(max_attempts=3, retry_interval_hours=24),
        "created_at": now,
        "updated_at": now,
        "created_by": "admin",
    }
    defaults.update(overrides)
    return Campaign(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CampaignLifecycle
# ---------------------------------------------------------------------------


class TestCampaignLifecycle:
    def test_campaign_lifecycle_no_active_without_approved(self) -> None:
        """AC: DRAFT -> ACTIVE (without APPROVED first) raises."""
        with pytest.raises(CampaignLifecycleError):
            CampaignLifecycle.validate_transition(CampaignStatus.DRAFT, CampaignStatus.ACTIVE)

    def test_full_lifecycle_path_allowed(self) -> None:
        path = [
            (CampaignStatus.DRAFT, CampaignStatus.REVIEW),
            (CampaignStatus.REVIEW, CampaignStatus.APPROVED),
            (CampaignStatus.APPROVED, CampaignStatus.ACTIVE),
            (CampaignStatus.ACTIVE, CampaignStatus.PAUSED),
            (CampaignStatus.PAUSED, CampaignStatus.ACTIVE),
            (CampaignStatus.ACTIVE, CampaignStatus.COMPLETED),
            (CampaignStatus.COMPLETED, CampaignStatus.ARCHIVED),
        ]
        for current, target in path:
            CampaignLifecycle.validate_transition(current, target)  # must not raise

    def test_archived_is_terminal(self) -> None:
        assert CampaignLifecycle.can_transition(CampaignStatus.ARCHIVED, CampaignStatus.DRAFT) is False


# ---------------------------------------------------------------------------
# ScheduleEngine (RBI-compliant — AC-critical)
# ---------------------------------------------------------------------------


class TestScheduleEngine:
    def test_schedule_engine_rbi_frequency_limit(self) -> None:
        """AC: returns None for a customer with 3 calls already today."""
        engine = ScheduleEngine(FakePolicyEngineService())
        result = engine.schedule_next_call(TENANT, "cust-1", "camp-1", hour=10, calls_today_count=3)
        assert result is None

    def test_schedule_engine_rbi_calling_hours(self) -> None:
        """AC: returns None for a 21:00 scheduling request."""
        engine = ScheduleEngine(FakePolicyEngineService())
        result = engine.schedule_next_call(TENANT, "cust-1", "camp-1", hour=21, calls_today_count=0)
        assert result is None

    def test_schedule_engine_permits_within_window(self) -> None:
        engine = ScheduleEngine(FakePolicyEngineService())
        result = engine.schedule_next_call(TENANT, "cust-1", "camp-1", hour=10, calls_today_count=1)
        assert result is not None

    def test_schedule_engine_respects_max_attempts(self) -> None:
        engine = ScheduleEngine(FakePolicyEngineService())
        policy = RetryPolicy(max_attempts=2)
        result = engine.schedule_next_call(TENANT, "cust-1", "camp-1", hour=10, attempt_count=2, retry_policy=policy)
        assert result is None

    def test_schedule_engine_dnd_returns_none(self) -> None:
        class _AlwaysDND:
            def is_on_dnd(self, tenant_id: TenantId, customer_id: str) -> bool:
                return True

        engine = ScheduleEngine(FakePolicyEngineService(), dnd_checker=_AlwaysDND())
        result = engine.schedule_next_call(TENANT, "cust-1", "camp-1", hour=10)
        assert result is None


# ---------------------------------------------------------------------------
# RetryPolicyEngine
# ---------------------------------------------------------------------------


class TestRetryPolicyEngine:
    def test_should_retry_true_within_max_attempts(self) -> None:
        policy = RetryPolicy(max_attempts=3)
        assert RetryPolicyEngine.should_retry(policy, "NO_ANSWER", attempt_count=1) is True

    def test_should_retry_false_at_max_attempts(self) -> None:
        policy = RetryPolicy(max_attempts=3)
        assert RetryPolicyEngine.should_retry(policy, "NO_ANSWER", attempt_count=3) is False

    def test_should_retry_false_on_do_not_retry_outcome(self) -> None:
        policy = RetryPolicy(max_attempts=5, do_not_retry_on_outcomes=("PTP_MADE",))
        assert RetryPolicyEngine.should_retry(policy, "PTP_MADE", attempt_count=1) is False


# ---------------------------------------------------------------------------
# ABTestingFramework
# ---------------------------------------------------------------------------


class TestABTestingFramework:
    def test_ab_variant_deterministic(self) -> None:
        """AC: same customer_id + campaign_id -> same variant (call 5 times)."""
        variants = (
            ABTestVariant(variant_id="a", name="control", traffic_weight=50),
            ABTestVariant(variant_id="b", name="treatment", traffic_weight=50),
        )
        results = {
            ABTestingFramework.assign_variant("cust-42", "camp-1", variants).variant_id  # type: ignore[union-attr]
            for _ in range(5)
        }
        assert len(results) == 1

    def test_ab_variant_different_customers_can_differ(self) -> None:
        variants = (
            ABTestVariant(variant_id="a", name="control", traffic_weight=50),
            ABTestVariant(variant_id="b", name="treatment", traffic_weight=50),
        )
        assignments = {
            ABTestingFramework.assign_variant(f"cust-{i}", "camp-1", variants).variant_id  # type: ignore[union-attr]
            for i in range(50)
        }
        assert assignments == {"a", "b"}

    def test_ab_variant_none_when_no_variants(self) -> None:
        assert ABTestingFramework.assign_variant("cust-1", "camp-1", ()) is None

    def test_variant_metrics_without_repository_returns_zeroes(self) -> None:
        framework = ABTestingFramework()
        metrics = framework.variant_metrics(TENANT, CampaignId("camp-1"), "a")
        assert metrics == {"completion_count": 0, "ptp_count": 0, "ptp_rate": 0.0}


# ---------------------------------------------------------------------------
# AudienceSelector (fake repositories)
# ---------------------------------------------------------------------------


class _FakeLoanAccountRepository:
    def __init__(self, cohort: tuple[tuple[str, str, int], ...]) -> None:
        self._cohort = cohort

    def select_cohort(
        self,
        tenant_id: object,
        min_dpd: int,
        max_dpd: int | None,
        min_outstanding_minor: int,
        product_types: tuple[str, ...] = (),
    ) -> tuple[tuple[str, str, int], ...]:
        return self._cohort


class _FakeCampaignAudienceRepository:
    def __init__(self) -> None:
        self.created: list[CampaignAudienceMember] = []
        self._existing: dict[str, list[str]] = {}

    def create(self, member: CampaignAudienceMember) -> CampaignAudienceMember:
        self.created.append(member)
        self._existing.setdefault(member.customer_id, []).append(member.campaign_id)
        return member

    def find_active_campaigns_for_customer(self, tenant_id: object, customer_id: str) -> tuple[str, ...]:
        return tuple(self._existing.get(customer_id, ()))


class TestAudienceSelector:
    def test_select_includes_eligible_customer(self) -> None:
        loan_repo = _FakeLoanAccountRepository((("cust-1", "loan-1", 45),))
        audience_repo = _FakeCampaignAudienceRepository()
        selector = AudienceSelector(loan_repo, consent_repository=None, campaign_audience_repository=audience_repo)  # type: ignore[arg-type]
        campaign = _make_campaign()

        members = selector.select(TENANT, campaign)

        assert len(members) == 1
        assert members[0].customer_id == "cust-1"
        assert audience_repo.created == list(members)

    def test_select_dedups_customer_already_in_another_campaign(self) -> None:
        loan_repo = _FakeLoanAccountRepository((("cust-1", "loan-1", 45),))
        audience_repo = _FakeCampaignAudienceRepository()
        audience_repo._existing["cust-1"] = ["other-campaign"]
        selector = AudienceSelector(loan_repo, consent_repository=None, campaign_audience_repository=audience_repo)  # type: ignore[arg-type]
        campaign = _make_campaign()

        members = selector.select(TENANT, campaign)

        assert members == ()


# ---------------------------------------------------------------------------
# CallDispatcher
# ---------------------------------------------------------------------------


class TestCallDispatcher:
    def test_dispatch_skips_dnd_and_excluded_members(self) -> None:
        engine = ScheduleEngine(FakePolicyEngineService())
        dispatcher = CallDispatcher(engine, ABTestingFramework())
        campaign = _make_campaign(status=CampaignStatus.ACTIVE)
        now = datetime.now(UTC)
        members = (
            CampaignAudienceMember(
                campaign_audience_id="m1",
                campaign_id=campaign.campaign_id,
                tenant_id=TENANT,
                customer_id="cust-1",
                included_at=now,
                dnd=True,
            ),
            CampaignAudienceMember(
                campaign_audience_id="m2",
                campaign_id=campaign.campaign_id,
                tenant_id=TENANT,
                customer_id="cust-2",
                included_at=now,
                excluded_reason="DUPLICATE",
            ),
            CampaignAudienceMember(
                campaign_audience_id="m3",
                campaign_id=campaign.campaign_id,
                tenant_id=TENANT,
                customer_id="cust-3",
                included_at=now,
            ),
        )

        dispatched = dispatcher.dispatch(TENANT, campaign, members, hour=10)

        assert [d.customer_id for d in dispatched] == ["cust-3"]

    def test_dispatch_returns_empty_outside_calling_hours(self) -> None:
        engine = ScheduleEngine(FakePolicyEngineService())
        dispatcher = CallDispatcher(engine, ABTestingFramework())
        campaign = _make_campaign(status=CampaignStatus.ACTIVE)
        member = CampaignAudienceMember(
            campaign_audience_id="m1",
            campaign_id=campaign.campaign_id,
            tenant_id=TENANT,
            customer_id="cust-1",
            included_at=datetime.now(UTC),
        )

        dispatched = dispatcher.dispatch(TENANT, campaign, (member,), hour=22)

        assert dispatched == ()


# ---------------------------------------------------------------------------
# CampaignService (fake repository)
# ---------------------------------------------------------------------------


class _FakeCampaignRepository:
    def __init__(self) -> None:
        self._store: dict[str, Campaign] = {}

    def create(self, campaign: Campaign) -> Campaign:
        self._store[campaign.campaign_id] = campaign
        return campaign

    def get(self, tenant_id: object, campaign_id: str) -> Campaign | None:
        return self._store.get(campaign_id)

    def find_active_for_tenant(self, tenant_id: object) -> tuple[Campaign, ...]:
        return tuple(c for c in self._store.values() if c.status == CampaignStatus.ACTIVE)

    def find_all_for_tenant(self, tenant_id: object) -> tuple[Campaign, ...]:
        return tuple(self._store.values())

    def update_status(self, tenant_id: object, campaign_id: str, status: CampaignStatus) -> None:
        campaign = self._store[campaign_id]
        self._store[campaign_id] = campaign.model_copy(update={"status": status})

    def update_counts(
        self, tenant_id: object, campaign_id: str, target_call_count: int, completed_call_count: int
    ) -> None:
        campaign = self._store[campaign_id]
        self._store[campaign_id] = campaign.model_copy(
            update={"target_call_count": target_call_count, "completed_call_count": completed_call_count}
        )

    def create_variant(self, campaign_id: str, variant: ABTestVariant) -> ABTestVariant:
        return variant


class TestCampaignService:
    def test_create_starts_in_draft(self) -> None:
        service = CampaignService(_FakeCampaignRepository())  # type: ignore[arg-type]
        campaign = service.create(
            TENANT, "Q3 Collections", AudienceCriteria(min_dpd=30), RetryPolicy(), created_by="admin"
        )
        assert campaign.status == CampaignStatus.DRAFT

    def test_activate_requires_approved_first(self) -> None:
        service = CampaignService(_FakeCampaignRepository())  # type: ignore[arg-type]
        campaign = service.create(TENANT, "Q3", AudienceCriteria(), RetryPolicy(), created_by="admin")
        with pytest.raises(CampaignLifecycleError):
            service.activate(TENANT, campaign.campaign_id, "admin", target_call_count=100)

    def test_full_happy_path_reaches_active(self) -> None:
        service = CampaignService(_FakeCampaignRepository())  # type: ignore[arg-type]
        campaign = service.create(TENANT, "Q3", AudienceCriteria(), RetryPolicy(), created_by="admin")
        service.submit_for_review(TENANT, campaign.campaign_id)
        service.approve(TENANT, campaign.campaign_id, "supervisor-1")
        activated = service.activate(TENANT, campaign.campaign_id, "supervisor-1", target_call_count=500)
        assert activated.status == CampaignStatus.ACTIVE
        assert activated.target_call_count == 500

    def test_list_all_returns_campaigns_regardless_of_status(self) -> None:
        service = CampaignService(_FakeCampaignRepository())  # type: ignore[arg-type]
        draft = service.create(TENANT, "Draft One", AudienceCriteria(), RetryPolicy(), created_by="admin")
        other = service.create(TENANT, "Draft Two", AudienceCriteria(), RetryPolicy(), created_by="admin")
        service.submit_for_review(TENANT, other.campaign_id)

        names = {c.name for c in service.list_all(TENANT)}
        assert names == {"Draft One", "Draft Two"}
        assert draft.status == CampaignStatus.DRAFT


class _FakePromptPinLookup:
    def __init__(self, pinned_version_id: str | None) -> None:
        self._pinned = pinned_version_id

    def pinned_version_id(self, tenant_id: object, campaign_id: str) -> str | None:
        return self._pinned


class TestCampaignPromptPinning:
    """Sprint-025 Part-3: "every campaign references a pinned immutable prompt
    version rather than editable prompt text" -- enforced at activate()."""

    def test_activate_denied_without_pinned_prompt(self) -> None:
        service = CampaignService(_FakeCampaignRepository(), prompt_pins=_FakePromptPinLookup(None))  # type: ignore[arg-type]
        campaign = service.create(TENANT, "Q3", AudienceCriteria(), RetryPolicy(), created_by="admin")
        service.submit_for_review(TENANT, campaign.campaign_id)
        service.approve(TENANT, campaign.campaign_id, "supervisor-1")

        with pytest.raises(CampaignPromptNotPinnedError):
            service.activate(TENANT, campaign.campaign_id, "supervisor-1", target_call_count=500)

    def test_activate_permitted_with_pinned_prompt(self) -> None:
        service = CampaignService(
            _FakeCampaignRepository(),  # type: ignore[arg-type]
            prompt_pins=_FakePromptPinLookup("prompt-version-1"),
        )
        campaign = service.create(TENANT, "Q3", AudienceCriteria(), RetryPolicy(), created_by="admin")
        service.submit_for_review(TENANT, campaign.campaign_id)
        service.approve(TENANT, campaign.campaign_id, "supervisor-1")

        activated = service.activate(TENANT, campaign.campaign_id, "supervisor-1", target_call_count=500)

        assert activated.status == CampaignStatus.ACTIVE

    def test_activate_unaffected_when_prompt_pins_unwired(self) -> None:
        """Pre-Sprint-025 behavior preserved: no prompt_pins -> no check at all."""
        service = CampaignService(_FakeCampaignRepository())  # type: ignore[arg-type]
        campaign = service.create(TENANT, "Q3", AudienceCriteria(), RetryPolicy(), created_by="admin")
        service.submit_for_review(TENANT, campaign.campaign_id)
        service.approve(TENANT, campaign.campaign_id, "supervisor-1")

        activated = service.activate(TENANT, campaign.campaign_id, "supervisor-1", target_call_count=500)

        assert activated.status == CampaignStatus.ACTIVE
