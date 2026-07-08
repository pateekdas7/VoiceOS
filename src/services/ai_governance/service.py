"""AIGovernanceService — lifecycle façade around the GovernanceLayer (V4 Ch3 §3.7).

Same in-process library-façade precedent as ``PolicyEngineService``
(Sprint-017): no VoiceOS service ships a standalone HTTP/gRPC listener
before Sprint-026 (K8s/Helm) — this is the entry point every enforcement
point (``ConversationEngine``/``TrueStreamingPipeline``) consults directly.

Architecture: V4 Ch3 (AI Governance).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.ai_safety.content_moderator import ContentModerator
from src.libs.ai_safety.human_oversight import HumanOversightRouterPort
from src.libs.contracts.response_plan import ResponsePlan
from src.libs.event_bus.publisher import Publisher

from .governance_layer import DEFAULT_HUMAN_REVIEW_THRESHOLD, GovernanceLayer
from .law_of_authority import LawOfAuthorityChecker
from .verdict import GovernanceVerdict

if TYPE_CHECKING:
    from src.services.policy_engine.service import PolicyEngineService


class AIGovernanceService:
    """The mandatory AI Governance gate every LLM output passes through before TTS."""

    def __init__(self, governance_layer: GovernanceLayer) -> None:
        self._governance_layer = governance_layer

    @classmethod
    def create(
        cls,
        policy_engine_service: PolicyEngineService | None = None,
        publisher: Publisher | None = None,
        human_review_threshold: float = DEFAULT_HUMAN_REVIEW_THRESHOLD,
        content_moderator: ContentModerator | None = None,
        human_oversight_router: HumanOversightRouterPort | None = None,
    ) -> AIGovernanceService:
        """Factory: build an AIGovernanceService with the given (optional) backends."""
        return cls(
            GovernanceLayer(
                law_of_authority_checker=LawOfAuthorityChecker(),
                policy_engine_service=policy_engine_service,
                publisher=publisher,
                human_review_threshold=human_review_threshold,
                content_moderator=content_moderator,
                human_oversight_router=human_oversight_router,
            )
        )

    def evaluate_output(
        self,
        llm_output: str,
        response_plan: ResponsePlan,
        risk_score: float = 0.0,
        call_id: str = "",
        tenant_id: str | None = None,
    ) -> GovernanceVerdict:
        """Evaluate one piece of LLM output and return its GovernanceVerdict."""
        return self._governance_layer.evaluate(
            llm_output=llm_output,
            response_plan=response_plan,
            risk_score=risk_score,
            call_id=call_id,
            tenant_id=tenant_id,
        )
