"""GovernanceLayer — the mandatory gate every LLM output passes through
before TTS (V4 Ch3 §3.9 "Governance Pipeline").

Evaluation order (Sprint-018.md):
  1. LawOfAuthorityChecker — any ungrounded fact -> BLOCK.
  2. PolicyEngine.check(domain='ai_governance', action='output_approval') —
     conversational/compliance policy check (optional; ``None`` skips this
     step, same additive precedent as every PolicyEngineService integration
     since Sprint-017).
  3. Direct high-risk gate — ``risk_score`` at/above the human-review
     threshold routes to REQUIRE_HUMAN even with no PolicyEngineService
     wired (the PDP rule ``AIGOV-HUMAN-REVIEW-HIGH-RISK`` encodes the same
     threshold when a PolicyEngineService *is* wired — this is the
     in-process fallback for callers that aren't).
  4. AI Safety — a minimal keyword-based content-moderation check.

REQUIRE_HUMAN and BLOCK verdicts are always audited (EventBus, optional).

Architecture: V4 Ch3 (AI Governance); RI-5.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.libs.ai_safety.content_moderator import ContentModerator
from src.libs.ai_safety.human_oversight import HumanOversightRouterPort
from src.libs.contracts.primitives import TenantId
from src.libs.contracts.response_plan import ResponsePlan
from src.libs.event_bus.publisher import Publisher

from .law_of_authority import LawOfAuthorityChecker
from .metrics import record_law_of_authority_violation, record_verdict
from .verdict import GovernanceStatus, GovernanceVerdict

if TYPE_CHECKING:
    from src.services.policy_engine.service import PolicyEngineService

logger = logging.getLogger(__name__)

DEFAULT_HUMAN_REVIEW_THRESHOLD = 0.9

HUMAN_REVIEW_REQUIRED_EVENT_TYPE = "ai_governance.human_review_required"
OUTPUT_BLOCKED_EVENT_TYPE = "ai_governance.output_blocked"


class GovernanceLayer:
    """Orchestrates all AI Governance checks for a single LLM output.

    Args:
        law_of_authority_checker: Defaults to a fresh :class:`LawOfAuthorityChecker`.
        policy_engine_service: Optional Sprint-017 PDP. ``None`` skips step 2.
        publisher: Optional EventBus publisher for REQUIRE_HUMAN/BLOCK audit
            events. ``None`` skips emission (same additive precedent as
            every EventBus integration since Sprint-013).
        human_review_threshold: risk_score at/above this routes to REQUIRE_HUMAN.
        content_moderator: The AI Safety (V4 Ch14) content-moderation check.
            Defaults to a fresh :class:`ContentModerator`.
        human_oversight_router: Optional V4 Ch15 supervisor-queue router for
            REQUIRE_HUMAN verdicts. ``None`` skips routing (the EventBus
            publish below still fires independently).
    """

    def __init__(
        self,
        law_of_authority_checker: LawOfAuthorityChecker | None = None,
        policy_engine_service: PolicyEngineService | None = None,
        publisher: Publisher | None = None,
        human_review_threshold: float = DEFAULT_HUMAN_REVIEW_THRESHOLD,
        content_moderator: ContentModerator | None = None,
        human_oversight_router: HumanOversightRouterPort | None = None,
    ) -> None:
        self._loa_checker = law_of_authority_checker or LawOfAuthorityChecker()
        self._policy_engine_service = policy_engine_service
        self._publisher = publisher
        self._human_review_threshold = human_review_threshold
        self._content_moderator = content_moderator or ContentModerator()
        self._human_oversight_router = human_oversight_router

    def evaluate(
        self,
        llm_output: str,
        response_plan: ResponsePlan,
        risk_score: float = 0.0,
        call_id: str = "",
        tenant_id: str | None = None,
    ) -> GovernanceVerdict:
        """Run every governance check and return one :class:`GovernanceVerdict`."""
        loa_violations = self._loa_checker.check(llm_output, response_plan)
        if loa_violations:
            record_law_of_authority_violation()
            verdict = GovernanceVerdict(
                status=GovernanceStatus.BLOCK,
                violations=loa_violations,
                explanation=f"Law of Authority violation: {'; '.join(loa_violations)}",
            )
            self._finalize(verdict, call_id, tenant_id)
            return verdict

        policy_verdict = self._check_policy_engine(risk_score, call_id, tenant_id)
        if policy_verdict is not None:
            self._finalize(policy_verdict, call_id, tenant_id)
            return policy_verdict

        if risk_score >= self._human_review_threshold:
            verdict = GovernanceVerdict(
                status=GovernanceStatus.REQUIRE_HUMAN,
                violations=("AIGOV-HUMAN-REVIEW-HIGH-RISK",),
                explanation=f"risk_score {risk_score:.2f} at/above human-review threshold {self._human_review_threshold:.2f}",
            )
            self._finalize(verdict, call_id, tenant_id)
            return verdict

        moderation = self._content_moderator.check(llm_output)
        if not moderation.safe:
            verdict = GovernanceVerdict(
                status=GovernanceStatus.BLOCK,
                violations=(f"AI_SAFETY_{moderation.blocked_category}",),
                explanation="Output failed the content-moderation check",
            )
            self._finalize(verdict, call_id, tenant_id)
            return verdict

        verdict = GovernanceVerdict(status=GovernanceStatus.APPROVE, explanation="All governance checks passed")
        record_verdict(verdict.status.value)
        return verdict

    def _check_policy_engine(self, risk_score: float, call_id: str, tenant_id: str | None) -> GovernanceVerdict | None:
        if self._policy_engine_service is None:
            return None

        from src.services.policy_engine.decision import PolicyOutcome
        from src.services.policy_engine.rule import PolicyRequest

        context: dict[str, Any] = {"risk_score": risk_score, "human_review_threshold": self._human_review_threshold}
        decision = self._policy_engine_service.evaluate(
            PolicyRequest(
                domain="ai_governance",
                action="output_approval",
                subject="ai_governance_service",
                resource=call_id or "turn",
                tenant_id=tenant_id,
                context=context,
            )
        )

        if decision.outcome in (PolicyOutcome.DENY, PolicyOutcome.FORBID):
            return GovernanceVerdict(
                status=GovernanceStatus.BLOCK,
                violations=decision.matching_rules,
                explanation=decision.reason,
            )
        if decision.outcome == PolicyOutcome.REQUIRE and "require_human" in decision.obligations:
            return GovernanceVerdict(
                status=GovernanceStatus.REQUIRE_HUMAN,
                violations=decision.matching_rules,
                explanation=decision.reason,
            )
        return None

    def _finalize(self, verdict: GovernanceVerdict, call_id: str, tenant_id: str | None) -> None:
        record_verdict(verdict.status.value)
        if verdict.status == GovernanceStatus.APPROVE:
            return

        logger.warning(
            "GovernanceLayer: %s verdict for call=%s — %s", verdict.status.value, call_id, verdict.explanation
        )

        if verdict.status == GovernanceStatus.REQUIRE_HUMAN and self._human_oversight_router is not None:
            self._human_oversight_router.route(call_id, tenant_id or "", verdict.explanation)

        if self._publisher is None or tenant_id is None:
            return

        event_type = (
            HUMAN_REVIEW_REQUIRED_EVENT_TYPE
            if verdict.status == GovernanceStatus.REQUIRE_HUMAN
            else OUTPUT_BLOCKED_EVENT_TYPE
        )
        self._publisher.publish(
            event_type=event_type,
            tenant_id=TenantId(tenant_id),
            payload={
                "call_id": call_id,
                "status": verdict.status.value,
                "violations": list(verdict.violations),
                "explanation": verdict.explanation,
            },
            correlation_id=call_id or str(uuid.uuid4()),
        )
