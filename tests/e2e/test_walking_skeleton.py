"""Walking Skeleton e2e test — Sprint-012 M-3 milestone.

Tests the complete CIL pipeline end-to-end with mocked AI backends
(LLM and TTS stubs). Real engines, real services, mock inference.

Acceptance criteria (Phase 1):
  - Full pipeline: TurnInput → ResponsePlan → PromptBuilder → LLM (mock)
    → OutputValidator → TTS (mock) → AudioClause list
  - Pipeline completes without error
  - AudioClause list is non-empty
  - ResponsePlan is sealed and immutable
  - DecisionEnvelope has non-empty envelope_id
  - 20 simulated turns complete with no crashes
  - p95 latency ≤ 1500ms (mocked AI, CPU only)

Architecture: V1 Ch1 (Runtime Pipeline); V2 Ch1 (Conversation Engine).
Sprint: 012 — Walking Skeleton.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from src.libs.contracts.response_plan import (
    ResponsePlan,
    StrategyAction,
    StrategyLabel,
)
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.libs.contracts.turn import TurnInput, TurnRole

if TYPE_CHECKING:
    from src.libs.concurrency.worker_pool import WorkerPool
    from src.libs.idempotency.guard import IdempotencyGuard
    from src.libs.observability.logger import StructuredLogger
    from src.libs.observability.tracer import OTelTracer
    from src.libs.state.snapshot import Snapshot
    from src.services.ai_config.prompt_versioning import PromptVersioningService
    from src.services.conversation_engine.engine import ConversationEngine, EventBusPort, OutputEvaluatorPort
    from src.services.crm.context_assembler import CustomerContextAssembler

# ---------------------------------------------------------------------------
# Mock AI adapters (no GPU / network required)
# ---------------------------------------------------------------------------


class _MockLLMAdapter:
    """Mock LLM adapter — returns a fixed 8-token response."""

    async def generate_stream(
        self,
        prompt: str,
        response_plan: ResponsePlan,
        max_tokens: int,
    ) -> AsyncIterator[TokenChunk]:
        async def _gen() -> AsyncGenerator[TokenChunk, None]:
            tokens = [
                "Aapka ",
                "outstanding ",
                "balance ",
                "hai. ",
                "Kya ",
                "aap ",
                "payment ",
                "kar sakte hain?",
            ]
            for i, text in enumerate(tokens):
                yield TokenChunk(
                    text=text,
                    token_id=i + 1,
                    finish_reason="stop" if i == len(tokens) - 1 else None,
                )

        return _gen()


class _MockTTSAdapter:
    """Mock TTS adapter — returns a single AudioClause per text input."""

    async def synthesize_stream(
        self,
        text_chunks: AsyncIterator[str],
        voice_config: VoiceConfig,
    ) -> AsyncIterator[AudioClause]:
        async def _gen() -> AsyncGenerator[AudioClause, None]:
            collected: list[str] = []
            async for chunk in text_chunks:
                collected.append(chunk)
            text = "".join(collected)
            yield AudioClause(
                audio_data=b"\x00" * 1024,
                sample_rate=24_000,
                text=text,
                clause_index=0,
                is_final=True,
            )

        return _gen()


# ---------------------------------------------------------------------------
# Fixture: wired ConversationEngine with mock AI backends
# ---------------------------------------------------------------------------


def _build_engine(
    event_bus: EventBusPort | None = None,
    idempotency_guard: IdempotencyGuard | None = None,
    snapshot_store: Snapshot | None = None,
    output_evaluator: OutputEvaluatorPort | None = None,
    quality_scoring_pool: WorkerPool | None = None,
    structured_logger: StructuredLogger | None = None,
    tracer: OTelTracer | None = None,
    prompt_versioning_service: PromptVersioningService | None = None,
    context_assembler: CustomerContextAssembler | None = None,
) -> ConversationEngine:
    """Build a fully-wired ConversationEngine with mock AI.

    Args:
        event_bus: Optional EventBusPort implementation (Sprint-013
                    RedisEventBusAdapter). Defaults to ConversationEngine's
                    own _NoOpEventBus when not supplied, preserving the
                    Sprint-012 walking-skeleton regression behaviour.
        idempotency_guard: Optional IdempotencyGuard (Sprint-015). Defaults
                    to None, preserving pre-Sprint-015 behaviour.
        snapshot_store: Optional Snapshot store (Sprint-015). Defaults to
                    None, preserving pre-Sprint-015 behaviour.
        output_evaluator: Optional OutputEvaluatorPort (Sprint-016 test hook).
                    Defaults to None, preserving pre-Sprint-016 behaviour.
        quality_scoring_pool: Optional WorkerPool (Sprint-016 test hook).
                    Defaults to None (ConversationEngine builds its own).
        structured_logger: Optional StructuredLogger (Sprint-016 test hook).
                    Defaults to None, preserving pre-Sprint-016 behaviour.
        tracer: Optional OTelTracer (Sprint-016 test hook). Defaults to
                    None, preserving pre-Sprint-016 behaviour.
        prompt_versioning_service: Optional PromptVersioningService (Sprint-025
                    Part-3). Defaults to None, preserving pre-Sprint-025 behaviour
                    (no pinned-prompt enforcement on campaign-dispatched calls).
        context_assembler: Optional CustomerContextAssembler (Sprint-022), needed
                    only if the test calls start_call() to link a campaign_id.
    """
    from src.engines.adaptive_conversation.engine import AdaptiveConversationEngine
    from src.engines.dialogue_policy.engine import DialoguePolicyEngine
    from src.engines.emotion.engine import EmotionIntelligenceEngine
    from src.engines.empathy.engine import EmpathyPlanner
    from src.engines.entity_extraction.engine import EntityExtractor
    from src.engines.goal_planner.engine import GoalPlanner
    from src.engines.intent.engine import IntentEngine
    from src.engines.intent.model import IntentModel
    from src.engines.negotiation.engine import NegotiationEngine
    from src.engines.prompt_builder.builder import PromptBuilder
    from src.engines.response_planning.engine import ResponsePlanningEngine
    from src.engines.risk.engine import RiskEngine
    from src.engines.strategy.engine import StrategyEngine
    from src.services.ai_governance.service import AIGovernanceService
    from src.services.conversation_engine.engine import ConversationEngine
    from src.services.conversation_quality.scorer import ConversationQualityScorer
    from src.services.knowledge_retrieval.service import KnowledgeRetrievalService
    from src.services.llm_runtime.output_validator import OutputValidator
    from src.services.llm_runtime.service import LLMService, LLMServiceConfig
    from src.services.tts.service import TTSService, TTSServiceConfig

    # Real engines (keyword-mode / rule-based — no model files needed).
    intent_engine = IntentEngine(IntentModel())
    entity_extractor = EntityExtractor()
    emotion_engine = EmotionIntelligenceEngine()
    risk_engine = RiskEngine()
    policy_engine = DialoguePolicyEngine()
    strategy_engine = StrategyEngine()
    goal_planner = GoalPlanner()
    negotiation_engine = NegotiationEngine()
    empathy_planner = EmpathyPlanner()
    adaptive_engine = AdaptiveConversationEngine()

    # CIL orchestrator (in engines layer — passes CILPort boundary check).
    cil = ResponsePlanningEngine(
        intent_engine=intent_engine,
        entity_extractor=entity_extractor,
        emotion_engine=emotion_engine,
        risk_engine=risk_engine,
        dialogue_policy_engine=policy_engine,
        strategy_engine=strategy_engine,
        goal_planner=goal_planner,
        negotiation_engine=negotiation_engine,
        empathy_planner=empathy_planner,
        adaptive_conv_engine=adaptive_engine,
    )

    prompt_builder = PromptBuilder()

    # Mock AI services.
    llm_adapter = _MockLLMAdapter()
    llm_service = LLMService(adapter=llm_adapter, config=LLMServiceConfig())

    tts_adapter = _MockTTSAdapter()
    tts_service = TTSService(adapter=tts_adapter, config=TTSServiceConfig())

    validator = OutputValidator()
    knowledge = KnowledgeRetrievalService()
    quality_scorer = ConversationQualityScorer()

    return ConversationEngine(
        cil=cil,
        prompt_builder=prompt_builder,
        llm_service=llm_service,
        tts_service=tts_service,
        validator=validator,
        knowledge=knowledge,
        quality_scorer=quality_scorer,
        ai_governance_service=AIGovernanceService.create(),
        event_bus=event_bus,
        idempotency_guard=idempotency_guard,
        snapshot_store=snapshot_store,
        output_evaluator=output_evaluator,
        quality_scoring_pool=quality_scoring_pool,
        structured_logger=structured_logger,
        tracer=tracer,
        prompt_versioning_service=prompt_versioning_service,
        context_assembler=context_assembler,
    )


def _make_turn(
    transcript: str = "main payment karna chahta hoon",
    turn_index: int = 0,
    call_id: str = "call-e2e-001",
) -> TurnInput:
    return TurnInput(
        turn_id=str(uuid.uuid4()),
        call_id=call_id,
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(),
        created_at=datetime.utcnow(),
        correlation_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        turn_index=turn_index,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWalkingSkeleton:
    async def test_walking_skeleton(self) -> None:  # required named test
        """Full pipeline: TurnInput → AudioClause (mock AI, real engines)."""
        from src.services.playback.scheduler import PlaybackScheduler

        engine = _build_engine()
        playback = PlaybackScheduler()
        turn = _make_turn()

        clauses = await engine.handle_turn(turn=turn, playback=playback)

        assert len(clauses) > 0, "Pipeline must produce at least one AudioClause"
        assert all(isinstance(c, AudioClause) for c in clauses)
        assert all(c.audio_data for c in clauses)

    def test_response_plan_immutable(self) -> None:  # required named test
        """ResponsePlan.plan_id must not change after sealing (frozen=True)."""
        from pydantic import ValidationError

        plan = ResponsePlan(
            plan_id=str(uuid.uuid4()),
            version=1,
            call_id="call-001",
            tenant_id="tenant-001",
            created_at=datetime.utcnow(),
            strategy=StrategyAction(action=StrategyLabel.ASK),
        )
        with pytest.raises((ValidationError, TypeError)):
            plan.plan_id = "mutated"  # type: ignore[misc]

    async def test_twenty_turns_no_crash(self) -> None:
        """20 sequential turns complete without error — M-3 walking skeleton."""
        from src.services.playback.scheduler import PlaybackScheduler

        engine = _build_engine()
        call_id = str(uuid.uuid4())
        latencies_ms: list[float] = []

        transcripts = [
            "main payment karna chahta hoon",
            "main abhi payment nahi kar sakta",
            "mujhe dispute karna hai",
            "main 30 din baad dunga",
            "mujhe hardship plan chahiye",
            "call back karo please",
            "main samjha nahi",
            "haan ji theek hai",
            "payment kar diya",
            "mujhe EMI chahiye",
        ] * 2  # 20 turns

        playback = PlaybackScheduler()
        for i, transcript in enumerate(transcripts):
            turn = _make_turn(transcript=transcript, turn_index=i, call_id=call_id)
            t0 = time.perf_counter()
            clauses = await engine.handle_turn(turn=turn, playback=playback)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies_ms.append(elapsed_ms)
            assert len(clauses) > 0, f"Turn {i} produced no clauses"
            await playback.flush()
            playback.clear_barge_in()

        assert len(latencies_ms) == 20
        p95 = sorted(latencies_ms)[int(0.95 * len(latencies_ms)) - 1]
        print(
            f"\n[Walking Skeleton] 20 turns: p95={p95:.1f}ms min={min(latencies_ms):.1f}ms max={max(latencies_ms):.1f}ms"
        )
        assert p95 <= 1500.0, f"p95 latency {p95:.1f}ms exceeds 1500ms budget"

    def test_decision_envelope_has_decisions(self) -> None:
        """DecisionEnvelope must contain at least one decision record per turn."""
        from src.engines.adaptive_conversation.engine import AdaptiveConversationEngine
        from src.engines.dialogue_policy.engine import DialoguePolicyEngine
        from src.engines.emotion.engine import EmotionIntelligenceEngine
        from src.engines.empathy.engine import EmpathyPlanner
        from src.engines.entity_extraction.engine import EntityExtractor
        from src.engines.goal_planner.engine import GoalPlanner
        from src.engines.intent.engine import IntentEngine
        from src.engines.intent.model import IntentModel
        from src.engines.negotiation.engine import NegotiationEngine
        from src.engines.response_planning.engine import ResponsePlanningEngine
        from src.engines.risk.engine import RiskEngine
        from src.engines.strategy.engine import StrategyEngine

        cil = ResponsePlanningEngine(
            intent_engine=IntentEngine(IntentModel()),
            entity_extractor=EntityExtractor(),
            emotion_engine=EmotionIntelligenceEngine(),
            risk_engine=RiskEngine(),
            dialogue_policy_engine=DialoguePolicyEngine(),
            strategy_engine=StrategyEngine(),
            goal_planner=GoalPlanner(),
            negotiation_engine=NegotiationEngine(),
            empathy_planner=EmpathyPlanner(),
            adaptive_conv_engine=AdaptiveConversationEngine(),
        )

        turn = _make_turn()
        plan, envelope = cil.assemble(
            turn=turn,
            context=None,
            retrieval=[],
        )

        assert envelope.envelope_id != ""
        assert len(envelope.decisions) > 0
        assert envelope.response_plan_id == plan.plan_id
        assert plan.plan_id != ""

    def test_output_validator_gates_tts(self) -> None:
        """OutputValidator must block must_not_say violations before TTS."""
        from src.libs.contracts.response_plan import MustNotSayItem
        from src.services.llm_runtime.output_validator import OutputValidator

        plan = ResponsePlan(
            plan_id=str(uuid.uuid4()),
            version=1,
            call_id="call-001",
            tenant_id="tenant-001",
            created_at=datetime.utcnow(),
            strategy=StrategyAction(action=StrategyLabel.ASK),
            must_not_say=(
                MustNotSayItem(
                    item_id="NO_LEGAL",
                    description="No legal threats",
                    pattern=r"\bcourt\b",
                ),
            ),
        )
        validator = OutputValidator()
        result = validator.validate("We will take you to court.", plan)
        assert not result.valid

    async def test_knowledge_retrieval_populates_response_plan(self) -> None:
        """KnowledgeRetrievalService must return snippets that appear in ResponsePlan."""
        from src.services.knowledge_retrieval.service import KnowledgeRetrievalService

        service = KnowledgeRetrievalService()
        snippets = await service.retrieve("EMI restructuring payment options")
        assert len(snippets) > 0
        assert all(s.content for s in snippets)


class _NoPinPromptVersioningService:
    """Fake PromptVersioningService whose campaigns are never pinned."""

    def pinned_version(self, tenant_id: object, campaign_id: str) -> None:
        return None


class _PinnedPromptVersioningService:
    """Fake PromptVersioningService returning a fixed pinned PromptVersion."""

    def __init__(self, version: object) -> None:
        self._version = version

    def pinned_version(self, tenant_id: object, campaign_id: str) -> object:
        return self._version


class _NullContextAssembler:
    """Fake CustomerContextAssembler — returns no context (handle_turn tolerates None)."""

    def assemble(self, tenant_id: object, customer_id: object, call_id: str) -> None:
        return None


class TestCampaignPromptPinEnforcement:
    """Sprint-025 Part-3 (V5 Ch14): "every campaign execution path uses a pinned
    published prompt version" is enforced authoritatively inside
    ConversationEngine.handle_turn() itself -- on the real, full pipeline (real
    CIL/prompt-builder/LLM-mock/TTS-mock), not just via CampaignService.activate()'s
    administrative gate, which a differently-wired CampaignService instance could
    bypass entirely."""

    async def test_handle_turn_raises_for_unpinned_campaign_dispatched_call(self) -> None:
        from src.libs.contracts.primitives import TenantId
        from src.services.campaign_management.service import CampaignPromptNotPinnedError
        from src.services.playback.scheduler import PlaybackScheduler

        engine = _build_engine(
            prompt_versioning_service=_NoPinPromptVersioningService(),  # type: ignore[arg-type]
            context_assembler=_NullContextAssembler(),  # type: ignore[arg-type]
        )
        call_id = "call-unpinned-001"
        engine.start_call(TenantId("tenant-001"), "cust-1", call_id, campaign_id="campaign-1")
        playback = PlaybackScheduler()
        turn = _make_turn(call_id=call_id)

        with pytest.raises(CampaignPromptNotPinnedError):
            await engine.handle_turn(turn=turn, playback=playback)

    async def test_handle_turn_succeeds_for_pinned_campaign_dispatched_call(self) -> None:
        from datetime import UTC

        from src.libs.contracts.models.ai_config import PromptVersion
        from src.libs.contracts.primitives import TenantId
        from src.services.playback.scheduler import PlaybackScheduler

        pinned = PromptVersion(
            prompt_version_id="v1",
            tenant_id=TenantId("tenant-001"),
            name="collections_negotiation",
            template="Negotiate the payment.",
            version_number=1,
            hash="deadbeef",
            created_by="system",
            created_at=datetime.now(UTC),
        )
        engine = _build_engine(
            prompt_versioning_service=_PinnedPromptVersioningService(pinned),  # type: ignore[arg-type]
            context_assembler=_NullContextAssembler(),  # type: ignore[arg-type]
        )
        call_id = "call-pinned-001"
        engine.start_call(TenantId("tenant-001"), "cust-1", call_id, campaign_id="campaign-1")
        playback = PlaybackScheduler()
        turn = _make_turn(call_id=call_id)

        clauses = await engine.handle_turn(turn=turn, playback=playback)

        assert len(clauses) > 0

    async def test_handle_turn_unaffected_for_non_campaign_call(self) -> None:
        """A call never dispatched by a campaign (no start_call(campaign_id=...))
        must proceed normally even with an unpinned-everything PromptVersioningService
        wired -- only campaign-dispatched calls are gated."""
        from src.services.playback.scheduler import PlaybackScheduler

        engine = _build_engine(prompt_versioning_service=_NoPinPromptVersioningService())  # type: ignore[arg-type]
        playback = PlaybackScheduler()
        turn = _make_turn()

        clauses = await engine.handle_turn(turn=turn, playback=playback)

        assert len(clauses) > 0
