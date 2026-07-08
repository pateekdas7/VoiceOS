"""Unit tests for the Policy Engine (Sprint-017, V4 Ch4).

All tests run fully in-process — no live Redis/Postgres required (Phase 1).
Redis-backed tests use `FakeRedisClient`; EventBus-backed tests use a real
`EventBus`/`Publisher` pair over `FakeRedisClient` (no network I/O).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.libs.contracts.models.ai_config import ModelConfig, PromptVersion
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.services.ai_governance.service import AIGovernanceService
from src.services.conversation_engine.engine import CILPort, ConversationEngine, PromptBuilderPort
from src.services.conversation_quality.scorer import ConversationQualityScorer
from src.services.knowledge_retrieval.service import KnowledgeRetrievalService
from src.services.llm_runtime.output_validator import OutputValidator
from src.services.llm_runtime.service import LLMService
from src.services.policy_engine.break_glass import BreakGlassDirective, BreakGlassPolicy
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome, most_restrictive
from src.services.policy_engine.engine import POLICY_DECISION_MADE_EVENT_TYPE, PolicyEngine
from src.services.policy_engine.inheritance import PolicyInheritance
from src.services.policy_engine.packs.admin import AdminPolicyPack
from src.services.policy_engine.packs.ai_governance import AIGovernancePolicyPack
from src.services.policy_engine.packs.authorization import AuthorizationPolicyPack
from src.services.policy_engine.packs.billing import BillingPolicyPack
from src.services.policy_engine.packs.conversational import ConversationalPolicyPack
from src.services.policy_engine.packs.dpdp import DPDPPolicyPack
from src.services.policy_engine.packs.rbi import RBIPolicyPack
from src.services.policy_engine.policy_set import PolicySet
from src.services.policy_engine.rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule
from src.services.policy_engine.service import PolicyEngineService
from src.services.tts.service import TTSService
from tests.fixtures.redis import FakeRedisClient


def _rbi_request(**context: object) -> PolicyRequest:
    return PolicyRequest(domain="rbi", action="start_call", subject="agent-1", resource="call-1", context=context)


def _dpdp_request(action: str = "process_customer_data", **context: object) -> PolicyRequest:
    return PolicyRequest(domain="dpdp", action=action, subject="system", resource="customer-1", context=context)


# ---------------------------------------------------------------------------
# Required named tests (Sprint-017.md)
# ---------------------------------------------------------------------------


class TestRequiredNamedTests:
    def test_rbi_calling_hours_deny_at_21h(self) -> None:
        request = _rbi_request(hour=21)
        assert RBIPolicyPack.CALLING_HOURS.decide(request) == PolicyOutcome.DENY

    def test_rbi_calling_hours_permit_at_10h(self) -> None:
        request = _rbi_request(hour=10)
        assert RBIPolicyPack.CALLING_HOURS.decide(request) == PolicyOutcome.PERMIT

    def test_rbi_frequency_deny_after_3_calls(self) -> None:
        request = _rbi_request(calls_today_count=3)
        assert RBIPolicyPack.CALLING_FREQUENCY.decide(request) == PolicyOutcome.DENY

    def test_dpdp_deny_without_consent(self) -> None:
        request = _dpdp_request(has_consent=False)
        assert DPDPPolicyPack.CONSENT_REQUIRED_FOR_PROCESSING.decide(request) == PolicyOutcome.DENY

    def test_deny_override_precedence(self, fake_redis: FakeRedisClient) -> None:
        """Tenant-level PERMIT + global-level DENY -> final outcome is DENY (V4 Ch4 §4.12)."""
        always_true = PolicyCondition("always", lambda _r: True)
        tenant_permit_rule = PolicyRule(
            rule_id="TEST-TENANT-PERMIT",
            pack="test",
            domain="test",
            condition=always_true,
            effect=PolicyEffect(PolicyOutcome.PERMIT),
        )
        global_deny_rule = PolicyRule(
            rule_id="TEST-GLOBAL-DENY",
            pack="test",
            domain="test",
            condition=always_true,
            effect=PolicyEffect(PolicyOutcome.DENY),
        )

        repository = _FakePolicyRepository(
            {
                ("global", None): (global_deny_rule.rule_id,),
                ("tenant", "tenant-1"): (tenant_permit_rule.rule_id,),
            }
        )
        engine = PolicyEngine(redis=fake_redis, policy_repository=repository)
        engine.registry[tenant_permit_rule.rule_id] = tenant_permit_rule
        engine.registry[global_deny_rule.rule_id] = global_deny_rule

        request = PolicyRequest(domain="test", action="do", subject="s", resource="r", tenant_id="tenant-1")
        decision = engine.evaluate(request)

        assert decision.outcome == PolicyOutcome.DENY
        assert set(decision.matching_rules) == {tenant_permit_rule.rule_id, global_deny_rule.rule_id}

    def test_break_glass_permits_with_audit(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="voiceos-events")
        publisher = Publisher(bus)
        engine = PolicyEngine(redis=fake_redis, publisher=publisher)

        directive = BreakGlassDirective(
            rule_id="RBI-CALLING-HOURS",
            reason="regulatory emergency outreach",
            requested_by="supervisor-1",
            approvers=("supervisor-1", "supervisor-2"),
        )
        request = PolicyRequest(
            domain="rbi", action="start_call", subject="supervisor-1", resource="call-1", tenant_id="tenant-1"
        )

        decision = engine.emergency_override(directive, request)

        assert decision.outcome == PolicyOutcome.PERMIT
        replayed = bus.replay_from()
        assert len(replayed) == 1
        assert replayed[0][1].event_type == POLICY_DECISION_MADE_EVENT_TYPE
        assert replayed[0][1].payload["decision"] == "PERMIT"

    def test_policy_decision_event_emitted_on_deny(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="voiceos-events")
        publisher = Publisher(bus)
        engine = PolicyEngine(redis=fake_redis, publisher=publisher)

        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject="agent-1",
            resource="call-1",
            tenant_id="tenant-1",
            context={"hour": 21, "call_id": "call-1"},
        )
        decision = engine.evaluate(request)

        assert decision.outcome == PolicyOutcome.DENY
        replayed = bus.replay_from()
        assert len(replayed) == 1
        envelope = replayed[0][1]
        assert envelope.event_type == POLICY_DECISION_MADE_EVENT_TYPE
        assert envelope.payload["decision"] == "DENY"
        assert envelope.payload["call_id"] == "call-1"


class _FakePolicyRepository:
    """Minimal Postgres-fallback stub for unit tests: scope -> active rule_ids."""

    def __init__(self, table: dict[tuple[str, str | None], tuple[str, ...]]) -> None:
        self._table = table
        self.calls: list[tuple[str, str | None]] = []

    def load_active_rule_ids(self, scope: str, scope_id: str | None) -> tuple[str, ...]:
        self.calls.append((scope, scope_id))
        return self._table.get((scope, scope_id), ())


# ---------------------------------------------------------------------------
# PolicyOutcome / decision combination
# ---------------------------------------------------------------------------


class TestPolicyOutcome:
    def test_most_restrictive_empty_is_permit(self) -> None:
        assert most_restrictive([]) == PolicyOutcome.PERMIT

    def test_most_restrictive_forbid_beats_deny(self) -> None:
        assert most_restrictive([PolicyOutcome.DENY, PolicyOutcome.FORBID]) == PolicyOutcome.FORBID

    def test_most_restrictive_deny_beats_require_and_permit(self) -> None:
        assert most_restrictive([PolicyOutcome.PERMIT, PolicyOutcome.REQUIRE, PolicyOutcome.DENY]) == PolicyOutcome.DENY

    def test_most_restrictive_require_beats_permit(self) -> None:
        assert most_restrictive([PolicyOutcome.PERMIT, PolicyOutcome.REQUIRE]) == PolicyOutcome.REQUIRE


# ---------------------------------------------------------------------------
# RBI pack — remaining rules
# ---------------------------------------------------------------------------


class TestRBIPolicyPack:
    def test_abuse_prohibition_forbids_threatening_utterance(self) -> None:
        request = _rbi_request(utterance_classification="threatening")
        assert RBIPolicyPack.ABUSE_PROHIBITION.decide(request) == PolicyOutcome.FORBID

    def test_abuse_prohibition_permits_neutral_utterance(self) -> None:
        request = _rbi_request(utterance_classification="neutral")
        assert RBIPolicyPack.ABUSE_PROHIBITION.decide(request) == PolicyOutcome.PERMIT

    def test_identity_verify_first_requires_verification_before_disclosure(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="disclose_debt",
            subject="agent-1",
            resource="call-1",
            context={"identity_verified": False},
        )
        assert RBIPolicyPack.IDENTITY_VERIFY_FIRST.decide(request) == PolicyOutcome.REQUIRE

    def test_identity_verify_first_permits_after_verification(self) -> None:
        request = PolicyRequest(
            domain="rbi",
            action="disclose_debt",
            subject="agent-1",
            resource="call-1",
            context={"identity_verified": True},
        )
        assert RBIPolicyPack.IDENTITY_VERIFY_FIRST.decide(request) == PolicyOutcome.PERMIT

    def test_disclosure_required_at_turn_zero(self) -> None:
        request = _rbi_request(turn_index=0, disclosure_given=False)
        assert RBIPolicyPack.DISCLOSURE_REQUIRED.decide(request) == PolicyOutcome.REQUIRE

    def test_recording_consent_required_when_missing(self) -> None:
        request = _rbi_request(recording_consent=False)
        assert RBIPolicyPack.RECORDING_CONSENT.decide(request) == PolicyOutcome.REQUIRE

    def test_recording_consent_permitted_when_captured(self) -> None:
        request = _rbi_request(recording_consent=True)
        assert RBIPolicyPack.RECORDING_CONSENT.decide(request) == PolicyOutcome.PERMIT

    def test_all_rules_are_hard_rules(self) -> None:
        assert all(rule.hard_rule for rule in RBIPolicyPack.rules())


# ---------------------------------------------------------------------------
# DPDP pack — remaining rules
# ---------------------------------------------------------------------------


class TestDPDPPolicyPack:
    def test_dpdp_permits_processing_with_consent(self) -> None:
        request = _dpdp_request(has_consent=True)
        assert DPDPPolicyPack.CONSENT_REQUIRED_FOR_PROCESSING.decide(request) == PolicyOutcome.PERMIT

    def test_erasure_honor_requires_processing(self) -> None:
        request = _dpdp_request(action="check_erasure", erasure_requested=True)
        assert DPDPPolicyPack.ERASURE_HONOR.decide(request) == PolicyOutcome.REQUIRE

    def test_purpose_limitation_denies_uncovered_purpose(self) -> None:
        request = _dpdp_request(action="use_data", purpose="marketing", consented_purposes=("collections",))
        assert DPDPPolicyPack.PURPOSE_LIMITATION.decide(request) == PolicyOutcome.DENY

    def test_purpose_limitation_permits_covered_purpose(self) -> None:
        request = _dpdp_request(action="use_data", purpose="collections", consented_purposes=("collections",))
        assert DPDPPolicyPack.PURPOSE_LIMITATION.decide(request) == PolicyOutcome.PERMIT

    def test_purpose_limitation_permits_when_purpose_absent(self) -> None:
        request = _dpdp_request(action="use_data")
        assert DPDPPolicyPack.PURPOSE_LIMITATION.decide(request) == PolicyOutcome.PERMIT

    def test_retention_schedule_requires_flagging_expired_data(self) -> None:
        request = _dpdp_request(action="retain_data", data_age_days=3000, retention_limit_days=2555)
        assert DPDPPolicyPack.RETENTION_SCHEDULE.decide(request) == PolicyOutcome.REQUIRE

    def test_all_rules_are_hard_rules(self) -> None:
        assert all(rule.hard_rule for rule in DPDPPolicyPack.rules())


# ---------------------------------------------------------------------------
# Authorization / AI-governance / conversational packs
# ---------------------------------------------------------------------------


class TestAuthorizationPolicyPack:
    def test_permission_required_denies_ungranted_action(self) -> None:
        request = PolicyRequest(domain="authz", action="delete_customer", subject="agent-1", resource="customer-1")
        assert AuthorizationPolicyPack.PERMISSION_REQUIRED.decide(request) == PolicyOutcome.DENY

    def test_permission_required_permits_granted_action(self) -> None:
        request = PolicyRequest(
            domain="authz",
            action="delete_customer",
            subject="agent-1",
            resource="customer-1",
            context={"granted_permissions": ("delete_customer",)},
        )
        assert AuthorizationPolicyPack.PERMISSION_REQUIRED.decide(request) == PolicyOutcome.PERMIT

    def test_tenant_isolation_forbids_cross_tenant_access(self) -> None:
        request = PolicyRequest(
            domain="authz",
            action="read",
            subject="agent-1",
            resource="customer-1",
            tenant_id="tenant-a",
            context={"resource_tenant_id": "tenant-b"},
        )
        assert AuthorizationPolicyPack.TENANT_ISOLATION.decide(request) == PolicyOutcome.FORBID


class TestAIGovernancePolicyPack:
    def test_no_hallucinated_facts_forbids_unverified_claims(self) -> None:
        request = PolicyRequest(
            domain="ai_governance",
            action="render_output",
            subject="llm",
            resource="turn-1",
            context={"contains_unverified_fact_claim": True},
        )
        assert AIGovernancePolicyPack.NO_HALLUCINATED_FACTS.decide(request) == PolicyOutcome.FORBID

    def test_human_review_required_above_threshold(self) -> None:
        request = PolicyRequest(
            domain="ai_governance", action="proceed", subject="llm", resource="turn-1", context={"risk_score": 0.95}
        )
        assert AIGovernancePolicyPack.HUMAN_REVIEW_FOR_HIGH_RISK.decide(request) == PolicyOutcome.REQUIRE


class TestConversationalPolicyPack:
    def test_must_not_threaten_forbids_threatening_utterance(self) -> None:
        request = PolicyRequest(
            domain="conversational",
            action="say",
            subject="agent-1",
            resource="turn-1",
            context={"utterance_classification": "threatening"},
        )
        assert ConversationalPolicyPack.MUST_NOT_THREATEN.decide(request) == PolicyOutcome.FORBID

    def test_must_disclose_purpose_at_start(self) -> None:
        request = PolicyRequest(
            domain="conversational",
            action="say",
            subject="agent-1",
            resource="turn-1",
            context={"turn_index": 0, "purpose_disclosed": False},
        )
        assert ConversationalPolicyPack.MUST_DISCLOSE_PURPOSE_AT_START.decide(request) == PolicyOutcome.REQUIRE


class TestBillingPolicyPack:
    def test_usage_limit_exceeded_denies_at_or_above_limit(self) -> None:
        request = PolicyRequest(
            domain="billing",
            action="use_feature",
            subject="billing_service",
            resource="CALL_MINUTE",
            context={"usage_quantity": 50_000, "usage_limit": 50_000},
        )
        assert BillingPolicyPack.USAGE_LIMIT_EXCEEDED.decide(request) == PolicyOutcome.DENY

    def test_usage_limit_permits_under_limit(self) -> None:
        request = PolicyRequest(
            domain="billing",
            action="use_feature",
            subject="billing_service",
            resource="CALL_MINUTE",
            context={"usage_quantity": 10, "usage_limit": 50_000},
        )
        assert BillingPolicyPack.USAGE_LIMIT_EXCEEDED.decide(request) == PolicyOutcome.PERMIT

    def test_usage_limit_none_means_unlimited(self) -> None:
        request = PolicyRequest(
            domain="billing",
            action="use_feature",
            subject="billing_service",
            resource="CALL_MINUTE",
            context={"usage_quantity": 10_000_000, "usage_limit": None},
        )
        assert BillingPolicyPack.USAGE_LIMIT_EXCEEDED.decide(request) == PolicyOutcome.PERMIT

    def test_trial_expired_denies(self) -> None:
        request = PolicyRequest(
            domain="billing",
            action="use_feature",
            subject="billing_service",
            resource="CALL_MINUTE",
            context={"tier": "TRIAL", "trial_expired": True},
        )
        assert BillingPolicyPack.TRIAL_EXPIRED.decide(request) == PolicyOutcome.DENY

    def test_trial_not_expired_permits(self) -> None:
        request = PolicyRequest(
            domain="billing",
            action="use_feature",
            subject="billing_service",
            resource="CALL_MINUTE",
            context={"tier": "TRIAL", "trial_expired": False},
        )
        assert BillingPolicyPack.TRIAL_EXPIRED.decide(request) == PolicyOutcome.PERMIT

    def test_non_trial_tier_never_hits_trial_expired_rule(self) -> None:
        request = PolicyRequest(
            domain="billing",
            action="use_feature",
            subject="billing_service",
            resource="CALL_MINUTE",
            context={"tier": "GROWTH", "trial_expired": True},
        )
        assert BillingPolicyPack.TRIAL_EXPIRED.decide(request) == PolicyOutcome.PERMIT


class TestAdminPolicyPack:
    def test_campaign_approval_denied_when_not_reviewed(self) -> None:
        request = PolicyRequest(
            domain="campaign_admin",
            action="approve",
            subject="admin-1",
            resource="campaign-1",
            context={"campaign_status": "DRAFT"},
        )
        assert AdminPolicyPack.CAMPAIGN_APPROVAL_REQUIRES_REVIEW.decide(request) == PolicyOutcome.DENY

    def test_campaign_approval_permitted_when_reviewed(self) -> None:
        request = PolicyRequest(
            domain="campaign_admin",
            action="approve",
            subject="admin-1",
            resource="campaign-1",
            context={"campaign_status": "REVIEW"},
        )
        assert AdminPolicyPack.CAMPAIGN_APPROVAL_REQUIRES_REVIEW.decide(request) == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# PolicySet / PolicyInheritance
# ---------------------------------------------------------------------------


class TestPolicySetAndInheritance:
    def test_matching_rules_filters_by_condition(self) -> None:
        policy_set = PolicySet(scope="global", scope_id=None, rules=RBIPolicyPack.rules())
        matched = policy_set.matching_rules(_rbi_request(hour=21))
        assert RBIPolicyPack.CALLING_HOURS in matched

    def test_inheritance_resolve_composes_all_scopes(self) -> None:
        global_set = PolicySet(scope="global", scope_id=None, rules=(RBIPolicyPack.CALLING_HOURS,))
        tenant_set = PolicySet(scope="tenant", scope_id="t1", rules=(RBIPolicyPack.CALLING_FREQUENCY,))
        resolved = PolicyInheritance.resolve(global_set, tenant_set, None)
        assert resolved == (RBIPolicyPack.CALLING_HOURS, RBIPolicyPack.CALLING_FREQUENCY)

    def test_inheritance_resolve_handles_all_none_scopes(self) -> None:
        assert PolicyInheritance.resolve(None, None, None) == ()


# ---------------------------------------------------------------------------
# BreakGlassPolicy
# ---------------------------------------------------------------------------


class TestBreakGlassPolicy:
    def test_denies_with_insufficient_approvals(self) -> None:
        policy = BreakGlassPolicy(required_approvals=2)
        directive = BreakGlassDirective(rule_id="R1", reason="test", requested_by="s1", approvers=("s1",))
        decision = policy.authorize(directive)
        assert decision.outcome == PolicyOutcome.DENY

    def test_permits_with_sufficient_distinct_approvals(self) -> None:
        policy = BreakGlassPolicy(required_approvals=2)
        directive = BreakGlassDirective(rule_id="R1", reason="test", requested_by="s1", approvers=("s1", "s2"))
        decision = policy.authorize(directive)
        assert decision.outcome == PolicyOutcome.PERMIT

    def test_denies_expired_directive(self) -> None:
        policy = BreakGlassPolicy(required_approvals=1, max_ttl_minutes=60)
        directive = BreakGlassDirective(
            rule_id="R1",
            reason="test",
            requested_by="s1",
            approvers=("s1",),
            requested_at=datetime.utcnow() - timedelta(minutes=120),
        )
        decision = policy.authorize(directive)
        assert decision.outcome == PolicyOutcome.DENY
        assert "expired" in decision.reason


# ---------------------------------------------------------------------------
# PolicyEngine — caching, no-op paths, obligations
# ---------------------------------------------------------------------------


class TestPolicyEngineCore:
    def test_permit_when_no_rule_matches(self, fake_redis: FakeRedisClient) -> None:
        engine = PolicyEngine(redis=fake_redis)
        request = PolicyRequest(domain="no_such_domain", action="noop", subject="agent-1", resource="call-1")
        decision = engine.evaluate(request)
        assert decision.outcome == PolicyOutcome.PERMIT
        assert decision.matching_rules == ()

    def test_no_audit_emitted_for_permit(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="voiceos-events")
        publisher = Publisher(bus)
        engine = PolicyEngine(redis=fake_redis, publisher=publisher)
        request = PolicyRequest(
            domain="rbi",
            action="start_call",
            subject="agent-1",
            resource="call-1",
            tenant_id="tenant-1",
            context={"hour": 10, "recording_consent": True, "disclosure_given": True, "turn_index": 1},
        )
        decision = engine.evaluate(request)
        assert decision.outcome == PolicyOutcome.PERMIT
        assert bus.replay_from() == []

    def test_engine_works_with_no_redis_or_repository(self) -> None:
        """Pure in-process mode (no infra wired at all)."""
        engine = PolicyEngine()
        request = _rbi_request(hour=21)
        decision = engine.evaluate(request)
        assert decision.outcome == PolicyOutcome.DENY

    def test_obligations_are_collected_from_matching_rules(self, fake_redis: FakeRedisClient) -> None:
        engine = PolicyEngine(redis=fake_redis)
        request = _rbi_request(recording_consent=False)
        decision = engine.evaluate(request)
        assert "capture_recording_consent" in decision.obligations


class TestPolicyEngineCaching:
    def test_policy_engine_caches_rules_in_redis(self, fake_redis: FakeRedisClient) -> None:
        """Load rules once (Postgres fallback), subsequent calls use Redis cache."""
        repository = _FakePolicyRepository({("global", None): ("RBI-CALLING-HOURS",)})
        engine = PolicyEngine(redis=fake_redis, policy_repository=repository)

        engine.evaluate(_rbi_request(hour=21))
        assert len(repository.calls) == 1

        engine.evaluate(_rbi_request(hour=21))
        assert len(repository.calls) == 1  # second call served from Redis cache, no repository hit

    def test_cache_miss_then_hit_returns_same_rule_set(self, fake_redis: FakeRedisClient) -> None:
        repository = _FakePolicyRepository({("global", None): ("RBI-CALLING-HOURS", "RBI-CALLING-FREQUENCY")})
        engine = PolicyEngine(redis=fake_redis, policy_repository=repository)

        first = engine.evaluate(_rbi_request(hour=21))
        second = engine.evaluate(_rbi_request(hour=21))
        assert first.outcome == second.outcome == PolicyOutcome.DENY


# ---------------------------------------------------------------------------
# PolicyEngineService
# ---------------------------------------------------------------------------


class TestPolicyEngineService:
    def test_check_call_admission_denies_outside_calling_hours(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_call_admission(tenant_id="tenant-1", call_id="call-1", hour=21)
        assert decision.outcome == PolicyOutcome.DENY

    def test_check_call_admission_permits_inside_calling_hours(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_call_admission(tenant_id="tenant-1", call_id="call-1", hour=10)
        assert decision.outcome == PolicyOutcome.PERMIT

    def test_check_call_admission_denies_after_frequency_cap(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_call_admission(tenant_id="tenant-1", call_id="call-1", hour=10, calls_today_count=3)
        assert decision.outcome == PolicyOutcome.DENY

    def test_check_conversational_rule_returns_outcome_string(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        outcome = service.check_conversational_rule("RBI-RECORDING-CONSENT", {"recording_consent": False})
        assert outcome == "REQUIRE"

    def test_check_conversational_rule_unknown_rule_permits(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        outcome = service.check_conversational_rule("NOT-A-REAL-RULE", {})
        assert outcome == "PERMIT"

    def test_emergency_override_delegates_to_engine(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        directive = BreakGlassDirective(rule_id="R1", reason="test", requested_by="s1", approvers=("s1", "s2"))
        request = PolicyRequest(domain="rbi", action="start_call", subject="s1", resource="call-1")
        decision = service.emergency_override(directive, request)
        assert decision.outcome == PolicyOutcome.PERMIT

    def test_check_entitlement_denies_at_usage_limit(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_entitlement(
            tenant_id="tenant-1", feature="CALL_MINUTE", tier="GROWTH", usage_quantity=50_000, usage_limit=50_000
        )
        assert decision.outcome == PolicyOutcome.DENY

    def test_check_entitlement_permits_under_limit(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_entitlement(
            tenant_id="tenant-1", feature="CALL_MINUTE", tier="GROWTH", usage_quantity=10, usage_limit=50_000
        )
        assert decision.outcome == PolicyOutcome.PERMIT

    def test_check_entitlement_denies_expired_trial(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_entitlement(
            tenant_id="tenant-1", feature="CALL_MINUTE", tier="TRIAL", trial_expired=True
        )
        assert decision.outcome == PolicyOutcome.DENY

    def test_check_campaign_approval_denies_unreviewed_campaign(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_campaign_approval("tenant-1", "campaign-1", "DRAFT")
        assert decision.outcome == PolicyOutcome.DENY

    def test_check_campaign_approval_permits_reviewed_campaign(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        decision = service.check_campaign_approval("tenant-1", "campaign-1", "REVIEW")
        assert decision.outcome == PolicyOutcome.PERMIT


def test_policy_decision_is_frozen() -> None:
    decision = PolicyDecision(outcome=PolicyOutcome.PERMIT)
    with pytest.raises(ValidationError):
        decision.outcome = PolicyOutcome.DENY  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConversationEngine.check_call_admission wiring (Sprint-017 integration)
# ---------------------------------------------------------------------------


def _make_conversation_engine(policy_engine_service: PolicyEngineService | None = None) -> ConversationEngine:
    """A ConversationEngine with every non-policy dependency mocked out —
    check_call_admission never touches handle_turn's collaborators."""
    return ConversationEngine(
        cil=cast(CILPort, MagicMock()),
        prompt_builder=cast(PromptBuilderPort, MagicMock()),
        llm_service=cast(LLMService, MagicMock()),
        tts_service=cast(TTSService, MagicMock()),
        validator=cast(OutputValidator, MagicMock()),
        knowledge=cast(KnowledgeRetrievalService, MagicMock()),
        quality_scorer=cast(ConversationQualityScorer, MagicMock()),
        ai_governance_service=AIGovernanceService.create(),
        policy_engine_service=policy_engine_service,
    )


class TestConversationEnginePolicyWiring:
    def test_check_call_admission_permits_when_unwired(self) -> None:
        engine = _make_conversation_engine()
        decision = engine.check_call_admission(tenant_id="tenant-1", call_id="call-1")
        assert decision.outcome == PolicyOutcome.PERMIT

    def test_check_call_admission_delegates_to_policy_engine_service(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        engine = _make_conversation_engine(policy_engine_service=service)
        decision = engine.check_call_admission(tenant_id="tenant-1", call_id="call-1", hour=21)
        assert decision.outcome == PolicyOutcome.DENY

    def test_check_call_admission_permits_inside_hours(self, fake_redis: FakeRedisClient) -> None:
        service = PolicyEngineService.create(redis=fake_redis)
        engine = _make_conversation_engine(policy_engine_service=service)
        decision = engine.check_call_admission(tenant_id="tenant-1", call_id="call-1", hour=10)
        assert decision.outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# ConversationEngine.resolve_runtime_config (Sprint-025 Part-3: AI Config wiring)
# ---------------------------------------------------------------------------


class _FakeModelConfigServiceForEngine:
    def __init__(self, llm_temperature: float) -> None:
        self._llm_temperature = llm_temperature

    def resolve(self, tenant_id: object, campaign_id: str | None = None) -> ModelConfig:
        return ModelConfig(
            model_config_id="mc-1",
            tenant_id=cast(TenantId, tenant_id),
            campaign_id=campaign_id,
            llm_temperature=self._llm_temperature,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


class _FakePromptVersioningServiceForEngine:
    def __init__(self, version: PromptVersion | None) -> None:
        self._version = version

    def pinned_version(self, tenant_id: object, campaign_id: str) -> PromptVersion | None:
        return self._version


class TestConversationEngineAIConfigWiring:
    """Sprint-025 Part-3: AI Configuration resolves before inference begins."""

    def test_resolve_runtime_config_all_none_when_unwired(self) -> None:
        engine = _make_conversation_engine()
        config = engine.resolve_runtime_config(TenantId("tenant-1"), "call-1")
        assert config.prompt_version is None
        assert config.model_config is None

    def test_resolve_runtime_config_resolves_model_config_without_campaign(self) -> None:
        engine = ConversationEngine(
            cil=cast(CILPort, MagicMock()),
            prompt_builder=cast(PromptBuilderPort, MagicMock()),
            llm_service=cast(LLMService, MagicMock()),
            tts_service=cast(TTSService, MagicMock()),
            validator=cast(OutputValidator, MagicMock()),
            knowledge=cast(KnowledgeRetrievalService, MagicMock()),
            quality_scorer=cast(ConversationQualityScorer, MagicMock()),
            ai_governance_service=AIGovernanceService.create(),
            model_config_service=cast(Any, _FakeModelConfigServiceForEngine(0.42)),
        )
        config = engine.resolve_runtime_config(TenantId("tenant-1"), "call-1")
        assert config.model_config is not None
        assert config.model_config.llm_temperature == 0.42
        assert config.prompt_version is None

    def test_resolve_runtime_config_resolves_pinned_prompt_for_campaign(self) -> None:
        pinned = PromptVersion(
            prompt_version_id="v1",
            tenant_id=TenantId("tenant-1"),
            name="collections_negotiation",
            template="Negotiate the payment.",
            version_number=1,
            hash="deadbeef",
            created_by="system",
            created_at=datetime.now(UTC),
        )
        engine = ConversationEngine(
            cil=cast(CILPort, MagicMock()),
            prompt_builder=cast(PromptBuilderPort, MagicMock()),
            llm_service=cast(LLMService, MagicMock()),
            tts_service=cast(TTSService, MagicMock()),
            validator=cast(OutputValidator, MagicMock()),
            knowledge=cast(KnowledgeRetrievalService, MagicMock()),
            quality_scorer=cast(ConversationQualityScorer, MagicMock()),
            ai_governance_service=AIGovernanceService.create(),
            prompt_versioning_service=cast(Any, _FakePromptVersioningServiceForEngine(pinned)),
            context_assembler=cast(Any, MagicMock()),
        )
        engine.start_call(TenantId("tenant-1"), "cust-1", "call-1", campaign_id="campaign-1")

        config = engine.resolve_runtime_config(TenantId("tenant-1"), "call-1")

        assert config.prompt_version is not None
        assert config.prompt_version.prompt_version_id == "v1"
