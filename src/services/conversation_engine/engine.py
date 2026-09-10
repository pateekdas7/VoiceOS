"""ConversationEngine — top-level CIL orchestrator service.

The ConversationEngine is the "brain" of VoiceOS. Per turn it:
  1. Retrieves knowledge snippets (KnowledgeRetrievalService).
  2. Runs the CIL pipeline (via injected CILPort protocol → ResponsePlanningEngine).
  3. Builds the LLM prompt (via injected PromptBuilderPort).
  4. Streams LLM output through OutputValidator and TrueStreamingPipeline → TTS.
  5. Publishes the DecisionEnvelope before TTS starts (RI-4 commit-before-act).
  6. Fires async quality scoring (non-blocking).

BOUNDARY RULE (check_boundaries.py Rule 1):
  This file is in src/services/ and must NOT import from src/engines/.
  All engine functionality is accessed through Protocol objects injected
  at construction time (dependency inversion / structural typing).

Architecture: V2 Ch1 (Conversation Engine / CIL orchestrator).
Invariants: RI-4 (commit before act — envelope published before TTS).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from src.libs.ai_safety.prompt_injection import PromptInjectionDetector
from src.libs.concurrency.worker_pool import Priority, WorkerPool
from src.libs.contracts.context import CustomerContext
from src.libs.contracts.decision import DecisionEnvelope
from src.libs.contracts.models.ai_config import ModelConfig, PromptVersion
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.contracts.response_plan import ResponsePlan, Snippet
from src.libs.contracts.streaming import AudioClause, TokenChunk
from src.libs.contracts.turn import TurnInput
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.idempotency.key_builder import IdempotencyKeyBuilder
from src.libs.invariants.guards import assert_ri4_commit_before_act
from src.libs.observability.logger import StructuredLogger
from src.libs.observability.tracer import OTelTracer
from src.libs.state.snapshot import Snapshot
from src.services.ai_config.model_config import ModelConfigService
from src.services.ai_config.prompt_versioning import PromptVersioningService
from src.services.ai_governance.service import AIGovernanceService
from src.services.campaign_management.service import CampaignPromptNotPinnedError
from src.services.collections.promise_to_pay import (
    PolicyDeniedError,
    PromiseToPayService,
    PTPValidationError,
)
from src.services.contact_center.live_transfer import TransferResult
from src.services.contact_center.service import ContactCenterService
from src.services.conversation_quality.scorer import ConversationQualityScorer
from src.services.crm.context_assembler import CustomerContextAssembler
from src.services.knowledge_retrieval.service import KnowledgeRetrievalService
from src.services.llm_runtime.output_validator import OutputValidator
from src.services.llm_runtime.service import LLMService
from src.services.playback.scheduler import PlaybackScheduler
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome
from src.services.policy_engine.service import PolicyEngineService
from src.services.tts.service import TTSService
from src.services.tts.startup_buffer_gate import StartupBufferGate, TTSMode
from src.services.tts.streaming_pipeline import TrueStreamingPipeline

from src.libs.observability.metrics import call_count_total as _call_count_total
from src.libs.observability.metrics import negotiation_outcome_total as _negotiation_outcome_total

from .metrics import red as _metrics
from .session_state import ConversationSessionState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeConfig:
    """The resolved, immutable configuration for one call's turns (Sprint-025 Part-3, V5 Ch14).

    Resolution order: pinned Prompt Version (Campaign Management, immutable
    once published) and Model Configuration (global default -> tenant
    override -> campaign override) -- both resolved once, before inference
    begins, via :meth:`ConversationEngine.resolve_runtime_config`. Either
    field is ``None`` when its corresponding service wasn't wired or no
    pin/config exists yet (preserves pre-Sprint-025 behavior: the engine's
    existing ``prompt_builder``/``llm_service`` remain the actual inference
    path -- this method centralizes *resolution*, not model invocation,
    keeping business logic model-agnostic per CLAUDE.md's AI Model Rules).
    """

    prompt_version: PromptVersion | None
    model_config: ModelConfig | None


def _make_csi_tracker() -> Any:
    """Create a ConversationStateIntelligence tracker without importing from
    src/engines/ (boundary Rule 1). Deferred import so the import only fires
    the first time a call needs a fresh tracker — zero cost for callers that
    never wire conversation_state_tracker at all."""
    from src.engines.conversation_state.engine import ConversationStateIntelligence  # noqa: PLC0415

    return ConversationStateIntelligence()


def _default_response_plan() -> ResponsePlan:
    """A minimal, valid ResponsePlan for speak_scripted_text() calls that
    have no real per-turn plan yet (the call-open greeting, the call-close
    hangup — both spoken outside handle_turn()'s per-turn CIL pipeline)."""
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="",
        tenant_id="",
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Protocol interfaces — allow injecting engine implementations without
# importing from src/engines/ (check_boundaries Rule 1).
# ---------------------------------------------------------------------------


@runtime_checkable
class CILPort(Protocol):
    """Protocol for the CIL pipeline (implemented by ResponsePlanningEngine)."""

    def assemble(
        self,
        turn: TurnInput,
        context: CustomerContext | None,
        retrieval: list[Snippet],
        intent_history: list[str] | None,
        identity_verified: bool,
        silence_duration_ms: int,
        conversation_state_tracker: Any = None,
        concession_round: int = 0,
    ) -> tuple[ResponsePlan, DecisionEnvelope]:
        """Run all CIL engines and return (ResponsePlan, DecisionEnvelope)."""
        ...


@runtime_checkable
class PromptBuilderPort(Protocol):
    """Protocol for the PromptBuilder (implemented by PromptBuilder engine)."""

    def build(
        self,
        response_plan: ResponsePlan,
        context: CustomerContext | None,
    ) -> tuple[str, str]:
        """Return (prompt_text, prompt_hash)."""
        ...


@runtime_checkable
class OutputEvaluatorPort(Protocol):
    """Protocol for async quality scoring (implemented by OutputEvaluationEngine)."""

    def score(
        self,
        turn: TurnInput,
        llm_output: str,
        response_plan: ResponsePlan,
    ) -> Any:
        """Return a TurnQualityScore-like object."""
        ...


@runtime_checkable
class DialogueTurnOutputPort(Protocol):
    """Structural shape of DialogueResponseEngine's per-turn output.

    Only the attributes this engine actually reads are declared — the real
    return type (src.engines.dialogue_response.engine.DialogueTurnOutput,
    Phase 6f) cannot be imported here (check_boundaries.py Rule 1).
    """

    reply_text: str
    needs_llm_fallback: bool


@runtime_checkable
class DialogueResponsePort(Protocol):
    """Protocol for the scripted-response FSM (implemented by
    DialogueResponseEngine, Path-A Phase 6f).

    ``session`` is typed ``Any`` here rather than re-declaring
    DialogueSessionState: ConversationEngine already owns the concrete
    ConversationSessionState instance it passes in (same-package import,
    not a boundary crossing) — the port only needs to describe what this
    class receives *back*, not re-validate what it already has.
    """

    def generate_reply(
        self,
        session: Any,
        response_plan: ResponsePlan,
        context: CustomerContext | None,
        user_text: str,
        lender_name: str,
    ) -> DialogueTurnOutputPort:
        """Return the scripted reply (and routing/empathy metadata) for one turn."""
        ...

    def build_greeting(self, context: CustomerContext | None, lender_name: str) -> str:
        """Return the call-open identity-verification greeting."""
        ...


@runtime_checkable
class EventBusPort(Protocol):
    """Protocol for publishing DecisionEnvelope (implemented by EventBus, Sprint-013)."""

    async def publish(self, envelope: DecisionEnvelope) -> str:
        """Durably persist the envelope (RI-4: before any external act).

        Returns:
            The EventBus stream entry ID the envelope was appended under
            (Sprint-015: used as the Recoverable session's snapshot resume
            offset — see ``ConversationSessionState``/``EventTailReplay``).
        """
        ...


class _NoOpEventBus:
    """No-op event bus for Sprint-012 walking skeleton (no Kafka/Redis yet)."""

    async def publish(self, envelope: DecisionEnvelope) -> str:
        logger.debug(
            "EventBus(noop): envelope %s published for call %s",
            envelope.envelope_id,
            envelope.call_id,
        )
        return "-"


class ConversationEngine:
    """Orchestrates the full per-turn pipeline.

    All engine dependencies are injected via Protocol objects so this
    class never imports from src/engines/ (boundary Rule 1).

    Args:
        cil: CILPort implementation (ResponsePlanningEngine).
        prompt_builder: PromptBuilderPort implementation.
        llm_service: LLMService for LLM generation.
        tts_service: TTSService for speech synthesis.
        validator: OutputValidator.
        knowledge: KnowledgeRetrievalService.
        quality_scorer: ConversationQualityScorer.
        ai_governance_service: AIGovernanceService — the mandatory Law-of-
            Authority/policy/content-safety gate (V4 Ch3, Sprint-018). Every
            LLM output passes through it before TTS; there is no way to
            construct a ConversationEngine without one.
        output_evaluator: OutputEvaluatorPort (optional; no-op if None).
        event_bus: EventBusPort for DecisionEnvelope publication (RI-4).
        max_validation_retries: Maximum LLM output rejection retries.
        quality_scoring_pool: WorkerPool bounding concurrent background
            quality-scoring tasks (Sprint-016; defaults to a private
            4-worker pool if not supplied).
        structured_logger: StructuredLogger for JSON turn-completion logs
            (Sprint-016; no-op if None).
        tracer: OTelTracer opening a span per turn (Sprint-016; no-op if None).
        promise_to_pay_service: PromiseToPayService (Path-A Phase 5). Optional
            — None preserves the pre-Phase-5 behavior of computing but never
            persisting a negotiated commitment. When wired, a turn whose
            ResponsePlan.negotiation_envelope.is_finalized_commitment is True
            triggers a synchronous, idempotent PTP creation immediately after
            the RI-4 DecisionEnvelope commit and before any LLM/TTS output —
            the durable commitment is recorded before the agent ever speaks a
            confirmation of it.
        dialogue_response: DialogueResponsePort (Path-A Phase 6g — the
            scripted-response FSM, DialogueResponseEngine). Optional — None
            preserves pre-Phase-6 behavior (every turn goes through the LLM
            streaming path, Step 5). When wired, it becomes the PRIMARY
            reply path for every turn: the LLM/TTS token-streaming path is
            not invoked at all for a call using this engine — the golden
            path is deterministic end to end, matching what Call-001 proved
            worked (V2 Ch13). The scripted reply still passes through
            TrueStreamingPipeline (OutputValidator + AIGovernance + TTS +
            playback) via :meth:`speak_scripted_text`, so no governance gate
            is bypassed by using this path instead of the LLM.
        lender_name: The tenant's lender/brand name spoken by the scripted
            response engine (e.g. in kavya_persona templates). Only used
            when ``dialogue_response`` is wired.
    """

    def __init__(
        self,
        cil: CILPort,
        prompt_builder: PromptBuilderPort,
        llm_service: LLMService,
        tts_service: TTSService,
        validator: OutputValidator,
        knowledge: KnowledgeRetrievalService,
        quality_scorer: ConversationQualityScorer,
        ai_governance_service: AIGovernanceService,
        output_evaluator: OutputEvaluatorPort | None = None,
        event_bus: EventBusPort | None = None,
        max_validation_retries: int = 2,
        idempotency_guard: IdempotencyGuard | None = None,
        snapshot_store: Snapshot | None = None,
        snapshot_every_n_turns: int = 10,
        quality_scoring_pool: WorkerPool | None = None,
        structured_logger: StructuredLogger | None = None,
        tracer: OTelTracer | None = None,
        policy_engine_service: PolicyEngineService | None = None,
        prompt_injection_detector: PromptInjectionDetector | None = None,
        context_assembler: CustomerContextAssembler | None = None,
        contact_center_service: ContactCenterService | None = None,
        model_config_service: ModelConfigService | None = None,
        prompt_versioning_service: PromptVersioningService | None = None,
        promise_to_pay_service: PromiseToPayService | None = None,
        dialogue_response: DialogueResponsePort | None = None,
        lender_name: str = "",
        working_memory_store: Any | None = None,
        relationship_memory_store: Any | None = None,
    ) -> None:
        self._cil = cil
        self._prompt_builder = prompt_builder
        self._llm = llm_service
        self._tts = tts_service
        self._validator = validator
        self._knowledge = knowledge
        self._quality = quality_scorer
        self._evaluator = output_evaluator
        self._event_bus: EventBusPort = event_bus or _NoOpEventBus()
        self._max_retries = max_validation_retries
        # Sprint-018 (V4 Ch3): AI Governance is a mandatory gate, not an
        # optional/additive wiring like every Sprint-013-017 integration —
        # ConversationEngine cannot be constructed without one, so every
        # LLM output this engine ever produces passes through it before TTS.
        self._ai_governance_service = ai_governance_service
        self._pipeline = TrueStreamingPipeline(ai_governance_service=ai_governance_service)
        self._background_tasks: set[asyncio.Task[None]] = set()
        # Sprint-016 (V3 Ch9 §9.12): background quality scoring runs through a
        # bounded, fair-share WorkerPool at LOW priority instead of an
        # unbounded `asyncio.ensure_future` fan-out, so a burst of turns can't
        # spawn unbounded concurrent scoring work.
        self._quality_scoring_pool = quality_scoring_pool or WorkerPool(max_workers=4)

        # Sprint-015: reliability wiring. `idempotency_guard` and
        # `snapshot_store` are optional so pre-Sprint-015 callers/tests keep
        # working unchanged (matches the `event_bus or _NoOpEventBus()`
        # precedent set in Sprint-013).
        self._idempotency_guard = idempotency_guard
        self._snapshot_store = snapshot_store
        self._snapshot_every_n_turns = snapshot_every_n_turns
        self._sessions: dict[str, ConversationSessionState] = {}

        # Sprint-016: observability wiring (V3 Ch16 logging; Ch17 tracing).
        # Both optional — None preserves pre-Sprint-016 behavior exactly.
        self._structured_logger = structured_logger
        self._tracer = tracer

        # Sprint-017: PDP wiring (V4 Ch4). Optional — None preserves
        # pre-Sprint-017 behavior (check_call_admission always PERMITs).
        self._policy_engine_service = policy_engine_service

        # Sprint-020 (V4 Ch13 §13.7 screen_input): detective-only — the Law
        # of Authority remains the structural defense; this only flags known
        # manipulation patterns for a SECURITY log line. Optional — None
        # preserves pre-Sprint-020 behavior (no screening).
        self._prompt_injection_detector = prompt_injection_detector

        # Sprint-022 (V5 Ch4, RI-5): CustomerContext is assembled exactly once
        # per call, at start_call() — never re-assembled mid-call. Optional
        # (None preserves pre-Sprint-022 behavior: callers pass `context`
        # explicitly to handle_turn(), as every test does today).
        self._context_assembler = context_assembler
        self._call_contexts: dict[str, CustomerContext] = {}

        # Sprint-023 (V5 Ch7): optional live-transfer hook. None preserves
        # pre-Sprint-023 behavior — ESCALATE remains prompt-guidance-only
        # unless a caller explicitly invokes escalate_call().
        self._contact_center_service = contact_center_service
        self._call_campaign_ids: dict[str, str] = {}

        # Sprint-025 Part-3 (V5 Ch14): AI Configuration resolution. Optional --
        # None preserves pre-Sprint-025 behavior (resolve_runtime_config()
        # returns an all-None RuntimeConfig when unwired).
        self._model_config_service = model_config_service
        self._prompt_versioning_service = prompt_versioning_service

        # Path-A Phase 5 (V5 Ch4.3): Collections persistence for a finalized
        # negotiation commitment. Optional — None preserves pre-Phase-5
        # behavior (negotiation moves are computed and spoken but never
        # durably recorded — the exact gap the architecture audit found).
        self._promise_to_pay_service = promise_to_pay_service

        # Path-A Phase 6g (V2 Ch13): the scripted-response FSM. Optional —
        # None preserves pre-Phase-6 behavior (LLM streaming path for every
        # turn). When wired, it replaces the LLM as the primary reply path.
        self._dialogue_response = dialogue_response
        self._lender_name = lender_name

        # V2 Ch11 Working Memory: per-call short-term Redis-backed state shared
        # with CIL engines via the caller-owned CSI tracker pattern. Optional —
        # None preserves pre-memory-wiring behavior (CIL gets a fresh tracker
        # each turn, so AdaptiveConversationEngine has no cross-turn state).
        self._working_memory_store = working_memory_store

        # V2 Ch12 Relationship Memory: cross-call Postgres-backed customer state
        # loaded at call start and persisted at call end. Optional.
        self._relationship_memory_store = relationship_memory_store

        # One ConversationStateIntelligence tracker per active call — same
        # instance passed on every turn so dialogue-state persists across turns.
        # Typed Any here to avoid importing from src/engines/ (boundary Rule 1).
        self._csi_trackers: dict[str, Any] = {}

    def start_call(
        self, tenant_id: TenantId, customer_id: str, call_id: str, campaign_id: str | None = None
    ) -> CustomerContext:
        """Assemble the authoritative CustomerContext once, at call start (V5 Ch4, RI-5).

        The result is cached for ``call_id`` and reused by every subsequent
        ``handle_turn()`` call in this session — ``CustomerContextAssembler
        .assemble()`` is never invoked again for this call, and the returned
        CustomerContext is itself frozen (pydantic), so nothing downstream can
        mutate it.

        ``campaign_id`` (Sprint-023, optional): when this call was dispatched
        by a campaign (``CallDispatcher``), the campaign becomes the
        authoritative source of the call — recorded here so
        ``escalate_call()``/disposition recording can trace back to it.
        ``None`` preserves pre-Sprint-023 behavior (a call not dispatched by
        any campaign, e.g. an inbound call).

        Raises:
            RuntimeError: No ``context_assembler`` was configured for this engine.
        """
        if self._context_assembler is None:
            raise RuntimeError("start_call() requires a context_assembler to be configured")
        context = self._context_assembler.assemble(tenant_id, CustomerId(customer_id), call_id)
        self._call_contexts[call_id] = context
        if campaign_id is not None:
            self._call_campaign_ids[call_id] = campaign_id

        # V2 Ch12: load relationship memory at call start so it is available
        # to the CIL's AdaptiveConversationEngine on the very first turn.
        if self._relationship_memory_store is not None:
            try:
                self._relationship_memory_store.get(customer_id)
            except Exception:
                logger.warning("RelationshipMemoryStore.get() failed for customer %s", customer_id)

        return context

    def end_call(self, call_id: str, outcome: str = "completed", *, customer_id: str = "", sentiment: str = "neutral") -> None:
        """Release the cached CustomerContext (and campaign linkage) for a finished call.

        ``outcome`` is the SLO-level label: "completed" for a normally
        terminated call, "system_error" for a call terminated by an AI
        pipeline failure. The availability recording rule in
        recording_rules.yml partitions on outcome!="system_error".

        ``customer_id`` and ``sentiment``: when provided (and
        ``relationship_memory_store`` is wired), a minimal CallSummary is
        persisted so the next call can see this call's outcome and sentiment.
        """
        _call_count_total.labels(outcome=outcome).inc()

        # V2 Ch12: persist a cross-call CallSummary before releasing any state.
        if self._relationship_memory_store is not None and customer_id:
            try:
                from src.engines.memory.relationship.schema import CallSummary
                summary = CallSummary(
                    call_id=call_id,
                    sentiment=sentiment,
                    outcome=outcome,
                )
                self._relationship_memory_store.update(customer_id, summary)
            except Exception:
                logger.warning("RelationshipMemoryStore.update() failed for call %s", call_id)

        self._call_contexts.pop(call_id, None)
        self._call_campaign_ids.pop(call_id, None)
        self._csi_trackers.pop(call_id, None)

    def campaign_id_for_call(self, call_id: str) -> str | None:
        """The campaign that dispatched ``call_id``, if any (Sprint-023)."""
        return self._call_campaign_ids.get(call_id)

    def resolve_runtime_config(self, tenant_id: TenantId, call_id: str) -> RuntimeConfig:
        """Resolve this call's immutable runtime configuration before inference begins
        (Sprint-025 Part-3, V5 Ch14): pinned Prompt Version (Campaign Management) and
        Model Configuration (global default -> tenant override -> campaign override).

        Callers (e.g. the runtime orchestrator that constructs each turn) may call
        this once per call -- right after :meth:`start_call` -- to parameterize the
        prompt/model selection for every turn in the call. This is a convenience
        pre-flight check, not the enforcement point: :meth:`handle_turn` calls the
        same underlying resolution on every turn regardless of whether a caller
        remembers to call this method first, so a campaign-dispatched call cannot
        reach inference without a pinned prompt version merely because an
        orchestrator skipped this call (see :meth:`_require_pinned_prompt`).
        """
        campaign_id = self.campaign_id_for_call(call_id)
        prompt_version = self._require_pinned_prompt(tenant_id, campaign_id)

        model_config: ModelConfig | None = None
        if self._model_config_service is not None:
            model_config = self._model_config_service.resolve(tenant_id, campaign_id)

        return RuntimeConfig(prompt_version=prompt_version, model_config=model_config)

    def _require_pinned_prompt(self, tenant_id: TenantId, campaign_id: str | None) -> PromptVersion | None:
        """Resolve (and enforce) the pinned prompt version for a campaign-dispatched call.

        This is the actual authority for "every campaign execution path uses a
        pinned published prompt version" (Sprint-025 Part-3) -- it is invoked from
        :meth:`_handle_turn_impl` on every turn, not only when a caller explicitly
        opts into :meth:`resolve_runtime_config`, so the guarantee holds regardless
        of how the ``CampaignService`` instance that approved the campaign's
        activation happened to be constructed elsewhere (``CampaignService.
        activate()``'s own ``prompt_pins`` gate is defense-in-depth at the
        administrative state-transition boundary; this is enforcement at the
        point of actual inference).

        Returns ``None`` when unwired (``prompt_versioning_service`` was never
        supplied) or when the call was not dispatched by a campaign (``campaign_id
        is None`` -- inbound calls remain unaffected). Raises
        :class:`CampaignPromptNotPinnedError` when the call **is**
        campaign-dispatched, AI Config **is** wired, and no prompt version has
        been pinned for that campaign -- fails closed rather than silently
        proceeding with unpinned, editable prompt text.
        """
        if self._prompt_versioning_service is None or campaign_id is None:
            return None
        prompt_version = self._prompt_versioning_service.pinned_version(tenant_id, campaign_id)
        if prompt_version is None:
            raise CampaignPromptNotPinnedError(
                f"campaign {campaign_id} has no pinned prompt version -- "
                "call cannot proceed without one (V5 Ch14 immutable Prompt Version requirement)"
            )
        return prompt_version

    def escalate_call(
        self,
        tenant_id: TenantId,
        call_id: str,
        reason: str,
        *,
        transcript: tuple[str, ...] = (),
        ai_summary: str = "",
        open_issues: tuple[str, ...] = (),
        required_skills: tuple[str, ...] = (),
    ) -> TransferResult:
        """Transfer this call from AI to a human agent (V5 Ch7 — ESCALATE / REQUIRE_HUMAN).

        Uses the CustomerContext cached by ``start_call()`` — the same
        authoritative snapshot the AI has been using all call — to assemble
        the agent's ``AgentScreenContext``.

        Raises:
            RuntimeError: No ``contact_center_service`` was configured, or
                ``start_call()`` was never invoked for this ``call_id``.
        """
        if self._contact_center_service is None:
            raise RuntimeError("escalate_call() requires a contact_center_service to be configured")
        context = self._call_contexts.get(call_id)
        if context is None:
            raise RuntimeError(f"escalate_call(): no CustomerContext cached for call_id={call_id!r}")
        return self._contact_center_service.transfer_to_human(
            tenant_id,
            call_id,
            reason,
            context,
            transcript=transcript,
            ai_summary=ai_summary,
            open_issues=open_issues,
            required_skills=required_skills,
        )

    async def handle_turn(
        self,
        turn: TurnInput,
        playback: PlaybackScheduler,
        context: CustomerContext | None = None,
        intent_history: list[str] | None = None,
        identity_verified: bool = False,
        silence_duration_ms: int = 0,
        cancel_event: "asyncio.Event | None" = None,
    ) -> list[AudioClause]:
        """Process one customer turn end-to-end.

        Flow:
          retrieve → CIL → prompt → (RI-4 commit) → LLM stream → TTS → playback

        Args:
            turn: Sealed TurnInput from the DialogueManager.
            playback: PlaybackScheduler for the current call session.
            context: Authoritative CustomerContext (None for tests).
            intent_history: Recent intent label strings for loop detection.
            identity_verified: Whether identity was verified for this call.
            silence_duration_ms: Customer silence duration in milliseconds.

        Returns:
            List of all synthesised AudioClauses for this turn.
        """
        # Sprint-022: if the caller doesn't pass an explicit context, fall back
        # to the one CustomerContextAssembler assembled once at start_call() —
        # never re-assembled here, so the same frozen CustomerContext is reused
        # for every turn in this call (RI-5).
        if context is None:
            context = self._call_contexts.get(turn.call_id)

        if self._tracer is None:
            return await self._handle_turn_impl(
                turn, playback, context, intent_history, identity_verified, silence_duration_ms,
                cancel_event=cancel_event,
            )

        with self._tracer.start_span(
            "conversation_engine.handle_turn",
            attributes={"call_id": turn.call_id, "turn_id": turn.turn_id, "tenant_id": turn.tenant_id},
        ) as span:
            trace_id = format(span.get_span_context().trace_id, "032x")
            clauses = await self._handle_turn_impl(
                turn, playback, context, intent_history, identity_verified, silence_duration_ms,
                trace_id=trace_id, cancel_event=cancel_event,
            )
        return clauses

    async def _handle_turn_impl(
        self,
        turn: TurnInput,
        playback: PlaybackScheduler,
        context: CustomerContext | None,
        intent_history: list[str] | None,
        identity_verified: bool,
        silence_duration_ms: int,
        trace_id: str = "",
        cancel_event: "asyncio.Event | None" = None,
    ) -> list[AudioClause]:
        """The actual per-turn pipeline — see :meth:`handle_turn` for the public contract."""
        _t0 = time.monotonic()
        try:
            result = await self.__handle_turn_body(
                turn, playback, context, intent_history, identity_verified, silence_duration_ms, trace_id,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            _metrics.record_error(type(exc).__name__)
            _metrics.record_request("error", (time.monotonic() - _t0) * 1000)
            raise
        _metrics.record_request("success", (time.monotonic() - _t0) * 1000)
        return result

    async def __handle_turn_body(
        self,
        turn: TurnInput,
        playback: PlaybackScheduler,
        context: CustomerContext | None,
        intent_history: list[str] | None,
        identity_verified: bool,
        silence_duration_ms: int,
        trace_id: str = "",
        cancel_event: "asyncio.Event | None" = None,
    ) -> list[AudioClause]:
        # Step 0 — Sprint-020 (V4 Ch13 §13.7): detective prompt-injection screen.
        # Structural containment (Law of Authority) is the real defense; this
        # only logs a SECURITY signal for monitoring/incident-response (Ch16/17).
        if self._prompt_injection_detector is not None:
            injection_verdict = self._prompt_injection_detector.detect(turn.transcript)
            if injection_verdict.flagged and self._structured_logger is not None:
                self._structured_logger.warning(
                    "SECURITY: prompt injection pattern detected in customer utterance",
                    tenant_id=turn.tenant_id,
                    call_id=turn.call_id,
                    matched_pattern=injection_verdict.matched_pattern,
                )

        # Step 0.5 — Sprint-025 Part-3 (V5 Ch14): a campaign-dispatched call may not
        # proceed to inference without a pinned, immutable prompt version. Enforced
        # here (not only at CampaignService.activate()) so every turn of every
        # campaign-dispatched call is gated, regardless of what wired the
        # CampaignService that approved the campaign's activation. Raises
        # CampaignPromptNotPinnedError, failing this turn closed, if unpinned.
        self._require_pinned_prompt(TenantId(turn.tenant_id), self._call_campaign_ids.get(turn.call_id))

        # Session and CSI tracker must be initialized before the CIL call so
        # concession_round and the per-call ConversationStateIntelligence
        # instance are available as inputs to ResponsePlanningEngine.assemble().
        session = self._sessions.setdefault(turn.call_id, ConversationSessionState(CallId(turn.call_id)))
        csi_tracker = self._csi_trackers.setdefault(turn.call_id, _make_csi_tracker())

        # Step 1 — Knowledge retrieval.
        snippets: list[Snippet] = await self._knowledge.retrieve(turn.transcript)

        # Step 2 — CIL pipeline (all engines via injected protocol).
        # conversation_state_tracker: same instance across every turn so
        # AdaptiveConversationEngine's dialogue-state machine persists across turns.
        # concession_round: how many COUNTER moves have already been made this call.
        response_plan, decision_envelope = self._cil.assemble(
            turn=turn,
            context=context,
            retrieval=snippets,
            intent_history=intent_history,
            identity_verified=identity_verified,
            silence_duration_ms=silence_duration_ms,
            conversation_state_tracker=csi_tracker,
            concession_round=session.concession_round,
        )

        # Detect a COUNTER negotiation move (agent proposed a counter-offer but
        # did not finalize commitment) and advance the per-call concession counter
        # so the next turn's NegotiationEngine knows how much runway is left.
        _neg = response_plan.negotiation_envelope
        if _neg is not None and not _neg.is_finalized_commitment and _neg.proposed_amount_minor is not None:
            session.increment_concession_round()

        # Step 3 — Build deterministic prompt.
        prompt_text, _prompt_hash = self._prompt_builder.build(response_plan, context)

        # Step 4 — RI-4: commit DecisionEnvelope before any external act.
        # Sprint-015: publishing is the authoritative effect this turn
        # performs — IdempotencyGuard makes a retried handle_turn() call for
        # the same turn a no-op instead of double-publishing.
        async def _publish_decision_envelope() -> str:
            return await self._event_bus.publish(decision_envelope)

        if self._idempotency_guard is not None:
            idempotency_key = IdempotencyKeyBuilder.build(
                call_id=CallId(turn.call_id),
                turn_id=turn.turn_id,
                effect_name="decision_envelope_publish",
            )
            entry_id = await self._idempotency_guard.execute_once(
                TenantId(turn.tenant_id),
                idempotency_key,
                "decision_envelope",
                _publish_decision_envelope,
            )
        else:
            entry_id = await _publish_decision_envelope()

        assert_ri4_commit_before_act(
            event_committed=True,
            action_name="tts_synthesis",
        )

        # Step 4.5 — Path-A Phase 5 (V5 Ch4.3): persist a finalized negotiation
        # commitment (RI-4: durably recorded before the agent ever speaks a
        # confirmation of it, i.e. before Step 5's LLM/TTS).
        await self._persist_finalized_commitment(turn, context, response_plan)

        # Sprint-015: track this call's Recoverable session state and take a
        # periodic snapshot (every `snapshot_every_n_turns` turns — V3 Ch6).
        # `entry_id` is the real EventBus stream offset (not decision_envelope's
        # own UUID) — EventTailReplay resumes from it via a literal Redis
        # XRANGE, which rejects anything that is not a genuine stream ID.
        # (session already initialized above before the CIL call)
        session.record_turn(
            intent_label=intent_history[-1] if intent_history else None,
            event_offset=entry_id,
        )
        if self._snapshot_store is not None and session.turn_count % self._snapshot_every_n_turns == 0:
            self._snapshot_store.take_snapshot(session, TenantId(turn.tenant_id), CallId(turn.call_id))

        # V2 Ch11 Working Memory: update the per-call Redis state after each
        # turn so downstream engines and the next turn's CIL have the latest
        # extracted intents, entities, and strategy. Best-effort — a Redis
        # failure must never abort the call's reply generation.
        if self._working_memory_store is not None:
            try:
                from src.engines.memory.working.schema import WorkingMemoryDelta
                _existing_wm = self._working_memory_store.get(turn.call_id)
                _primary_intent = response_plan.intents[0].label if response_plan.intents else None
                _strategy_label = response_plan.strategy.action.value if response_plan.strategy else None
                _neg_state = "negotiating" if response_plan.negotiation_envelope else "initial"
                _entities_str = {k: str(v) for k, v in response_plan.entities.items()}
                _wm_delta = WorkingMemoryDelta(
                    turn_count=session.turn_count,
                    last_intent=_primary_intent,
                    extracted_entities=_entities_str if _entities_str else None,
                    negotiation_state=_neg_state,
                    last_strategy=_strategy_label,
                    customer_utterances=(*_existing_wm.customer_utterances, turn.transcript),
                )
                self._working_memory_store.update(turn.call_id, _wm_delta)
            except Exception:
                logger.warning("WorkingMemoryStore update failed for call %s — continuing", turn.call_id)

        # Step 5 — Reply generation: scripted golden path (Phase 6g) when
        # wired, with a real per-turn LLM fallback when the golden path can't
        # classify the customer's utterance for _ELSE_FALLBACK_THRESHOLD
        # consecutive turns (DialogueTurnOutput.needs_llm_fallback — the live
        # trigger condition for the approved plan's "LLM as fallback" intent,
        # not merely "LLM only runs when dialogue_response is unwired at
        # construction time"). Falls back to the plain LLM streaming path
        # (Sprint-009-018) unchanged when no dialogue_response is wired at all.
        all_clauses: list[AudioClause] = []
        full_output_text = ""
        customer_name = context.primary_party.name if context is not None else ""

        if self._dialogue_response is not None:
            dialogue_output = self._dialogue_response.generate_reply(
                session, response_plan, context, turn.transcript, self._lender_name
            )
            if dialogue_output.needs_llm_fallback:
                logger.info(
                    "ConversationEngine: scripted golden path exhausted for call %s turn %s — "
                    "falling back to the LLM for this turn",
                    turn.call_id,
                    turn.turn_id,
                )
                # Play first clause as soon as threshold_ms of audio is buffered,
                # stream remaining clauses concurrently (buffered_streaming mode).
                clauses = await self._run_llm_streaming_path(
                    prompt_text, response_plan, playback, customer_name,
                    tts_mode="buffered_streaming",
                    cancel_event=cancel_event,
                )
                all_clauses.extend(clauses)
                full_output_text = " ".join(c.text for c in all_clauses)
            else:
                full_output_text = dialogue_output.reply_text
                # buffered_streaming: ClauseSplitter segments the reply; first
                # clause releases after threshold_ms is buffered, rest stream behind.
                all_clauses.extend(await self.speak_scripted_text(
                    full_output_text, playback, response_plan,
                    tts_mode="buffered_streaming",
                ))
        else:
            # buffered_streaming: legacy no-dialogue-response path.
            clauses = await self._run_llm_streaming_path(
                prompt_text, response_plan, playback, customer_name,
                tts_mode="buffered_streaming",
                cancel_event=cancel_event,
            )
            all_clauses.extend(clauses)
            full_output_text = " ".join(c.text for c in all_clauses)

        # Step 6 — Async quality scoring (fire-and-forget, non-blocking).
        # Sprint-016: dispatched through the bounded WorkerPool at LOW
        # priority — a burst of turns queues fairly instead of spawning
        # unbounded concurrent scoring tasks (V3 Ch9 §9.12).
        if self._evaluator is not None:

            async def _score() -> None:
                await self._async_quality_score(turn, full_output_text, response_plan)

            task = asyncio.ensure_future(self._quality_scoring_pool.submit(_score, Priority.LOW))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        logger.info(
            "ConversationEngine: turn %s complete — %d clauses, plan=%s",
            turn.turn_id,
            len(all_clauses),
            response_plan.plan_id,
        )
        if self._structured_logger is not None:
            self._structured_logger.info(
                "turn complete",
                tenant_id=turn.tenant_id,
                call_id=turn.call_id,
                trace_id=trace_id,
                correlation_id=turn.turn_id,
                clause_count=len(all_clauses),
                plan_id=response_plan.plan_id,
            )
        return all_clauses

    def build_greeting(self, context: CustomerContext | None) -> str | None:
        """The call-open identity-verification greeting (Path-A Phase 6g),
        or None when no dialogue_response is wired (pre-Phase-6 behavior —
        callers must fall back to their own call-open handling).

        Callers (the WS entrypoint) speak this once via
        :meth:`speak_scripted_text` before the customer's first turn, so
        DialogueResponseEngine's AWAIT_IDENTITY state has already asked its
        question before handle_turn() is ever called for this call.
        """
        if self._dialogue_response is None:
            return None
        return self._dialogue_response.build_greeting(context, self._lender_name)

    async def speak_scripted_text(
        self,
        text: str,
        playback: PlaybackScheduler,
        response_plan: ResponsePlan | None = None,
        tts_mode: str | None = None,
    ) -> list[AudioClause]:
        """Synthesize a fixed string (not an LLM stream) through the same
        governance/validation/TTS/playback pipeline every LLM turn uses.

        Used by the scripted-response golden path (Phase 6g) for per-turn
        replies, and by callers outside handle_turn() (the WS entrypoint's
        call-start/call-end handling) for the fixed greeting and hangup
        lines (kavya_persona.build_greeting_text()/HANGUP_TEXT) — neither
        of which has a real per-turn ResponsePlan yet, hence ``response_plan``
        is optional and a minimal default is used when absent. Routing a
        fixed string through a single-chunk TokenChunk stream reuses
        TrueStreamingPipeline exactly as-is: OutputValidator and
        AIGovernanceService still run, so no fixed string bypasses the
        governance gate that every LLM turn is subject to.
        """
        plan = response_plan if response_plan is not None else _default_response_plan()

        async def _single_chunk() -> AsyncIterator[TokenChunk]:
            yield TokenChunk(text=text, token_id=0, finish_reason="stop")

        # ``tts_mode`` overrides VOICEOS_TTS_MODE for THIS one call only,
        # constructing a per-turn StartupBufferGate at the requested mode.
        # The greeting caller passes "blocking" so all greeting clauses are
        # held until is_final=True, then released FIFO — this eliminates the
        # 337–353ms inter-clause gaps that came from serial per-clause TTS
        # HTTP POSTs and were audible as mid-word breaks in Gate 3C.
        gate: StartupBufferGate | None = None
        if tts_mode is not None:
            try:
                mode_enum = TTSMode(tts_mode)
            except ValueError:
                logger.warning(
                    "speak_scripted_text: unknown tts_mode=%r — falling back to env default",
                    tts_mode,
                )
                mode_enum = None
            if mode_enum is not None and mode_enum != TTSMode.STREAMING:
                gate = StartupBufferGate(
                    playback=playback,
                    mode=mode_enum,
                    threshold_ms=0,
                    generation=playback.generation,
                )

        return await self._pipeline.run(
            token_stream=_single_chunk(),
            response_plan=plan,
            tts_service=self._tts,
            validator=self._validator,
            playback=playback,
            gate=gate,
        )

    async def _run_llm_streaming_path(
        self,
        prompt_text: str,
        response_plan: ResponsePlan,
        playback: PlaybackScheduler,
        customer_name: str = "",
        tts_mode: str | None = None,
        cancel_event: "asyncio.Event | None" = None,
    ) -> list[AudioClause]:
        """The original LLM token-streaming path (Sprint-009-018), extracted
        so it can be invoked either as the whole-call fallback (no
        dialogue_response wired at all) or as a genuine per-turn fallback
        when the scripted golden path can't classify the customer's
        utterance (DialogueTurnOutput.needs_llm_fallback, Phase 6g's
        Call-002-readiness follow-up). Same governance/validation gates as
        every other path through TrueStreamingPipeline — including, as of
        the same Call-002 readiness pass, the persona/register gate
        (customer_name plumbed through so LLM-generated replies get the
        same name-scrub/register enforcement the scripted path already had
        via DialogueResponseEngine's own guard pass).

        Phase I Gate 1 — ``tts_mode`` overrides ``VOICEOS_TTS_MODE`` for
        this one turn: when set to ``"full_response"`` every clause the
        splitter emits is held in a per-turn ``StartupBufferGate`` until
        ``is_final=True`` or ``flush_final()``, and nothing reaches the
        scheduler mid-response. Leaving it ``None`` restores the pre-Phase-I
        env-driven default (streaming pass-through)."""
        token_stream = await self._llm.generate_stream(
            prompt=prompt_text,
            response_plan=response_plan,
            max_tokens=response_plan.delivery.max_response_tokens,
            cancel_event=cancel_event,
        )
        gate: StartupBufferGate | None = None
        if tts_mode is not None:
            try:
                mode_enum = TTSMode(tts_mode)
            except ValueError:
                logger.warning(
                    "_run_llm_streaming_path: unknown tts_mode=%r — "
                    "falling back to env default", tts_mode,
                )
                mode_enum = None
            if mode_enum is not None and mode_enum != TTSMode.STREAMING:
                gate = StartupBufferGate(
                    playback=playback,
                    mode=mode_enum,
                    threshold_ms=0,
                    generation=playback.generation,
                )
        return await self._pipeline.run(
            token_stream=token_stream,
            response_plan=response_plan,
            tts_service=self._tts,
            validator=self._validator,
            playback=playback,
            customer_name=customer_name,
            gate=gate,
        )

    async def _persist_finalized_commitment(
        self,
        turn: TurnInput,
        context: CustomerContext | None,
        response_plan: ResponsePlan,
    ) -> None:
        """Persist a finalized negotiation commitment to Collections (V5 Ch4.3).

        No-op unless every one of the following holds: a promise_to_pay_service
        was wired, the ResponsePlan carries a negotiation_envelope, that
        envelope's is_finalized_commitment is True (engine-level
        NegotiationMove.ACCEPT/PROPOSE_PTP — see response_planning/engine.py),
        and enough authoritative data is available (CustomerContext with a
        primary_loan, a proposed amount, a proposed date) to construct a valid
        PTP. Missing data logs a warning and skips persistence rather than
        raising — a turn should not crash outright over an unpersisted
        commitment; the gap is surfaced in logs for operator follow-up.

        A PTPValidationError/PolicyDeniedError from the service itself
        (invalid amount/date, or a live policy denial) is also caught and
        logged rather than propagated — deciding what the agent should say
        differently when a commitment can't be recorded is a persona/dialogue
        concern (Phase 6), not this transport-and-plumbing layer's job.
        PromiseToPayService.create() is idempotent by construction
        (IdempotencyGuard + a DB-level unique constraint on the same key), so
        a retried turn never double-creates a PTP.
        """
        if self._promise_to_pay_service is None:
            return
        envelope = response_plan.negotiation_envelope
        if envelope is None or not envelope.is_finalized_commitment:
            return
        if context is None or context.primary_loan is None:
            logger.warning(
                "Finalized commitment on call %s cannot be persisted — no CustomerContext/primary_loan available",
                turn.call_id,
            )
            return
        if envelope.proposed_amount_minor is None or envelope.proposed_date is None:
            logger.warning(
                "Finalized commitment on call %s cannot be persisted — missing proposed_amount_minor/proposed_date",
                turn.call_id,
            )
            return

        promise_date = datetime.combine(envelope.proposed_date, datetime.min.time())
        currency = context.primary_loan.outstanding_balance.currency.value

        try:
            ptp = await self._promise_to_pay_service.create(
                tenant_id=TenantId(turn.tenant_id),
                call_id=CallId(turn.call_id),
                customer_id=context.customer_id,
                loan_account_id=str(context.primary_loan.account_id),
                promised_amount_minor=envelope.proposed_amount_minor,
                currency=currency,
                promise_date=promise_date,
            )
        except (PTPValidationError, PolicyDeniedError) as exc:
            logger.warning(
                "PromiseToPayService rejected finalized commitment on call %s: %s",
                turn.call_id,
                exc,
            )
            return

        _negotiation_outcome_total.labels(outcome="ptp_created").inc()
        logger.info(
            "Persisted PTP %s for call %s (%s minor %s by %s)",
            ptp.ptp_id,
            turn.call_id,
            envelope.proposed_amount_minor,
            currency,
            envelope.proposed_date,
        )

    def check_call_admission(
        self,
        tenant_id: str,
        call_id: str,
        hour: int | None = None,
        calls_today_count: int = 0,
    ) -> PolicyDecision:
        """Query the Policy Engine for RBI call-admission (Sprint-017 integration wiring).

        Callers invoke this before call start (turn_index == 0); a DENY
        outcome (outside calling hours, or the daily call-frequency cap
        reached) means the call must not proceed. Returns an unconditional
        PERMIT when no ``PolicyEngineService`` is wired — this method is
        purely additive and does not change ``handle_turn`` behavior.
        """
        if self._policy_engine_service is None:
            return PolicyDecision(outcome=PolicyOutcome.PERMIT, reason="no PolicyEngineService wired")
        return self._policy_engine_service.check_call_admission(
            tenant_id=tenant_id,
            call_id=call_id,
            hour=hour,
            calls_today_count=calls_today_count,
        )

    def get_session_state(self, call_id: str) -> ConversationSessionState | None:
        """Return the tracked Recoverable session state for ``call_id``, if any.

        Used by ``RecoveryManager``/``CPURestartStrategy`` (Sprint-015) to
        snapshot or restore a call's session state around a crash.
        """
        return self._sessions.get(call_id)

    async def _async_quality_score(
        self,
        turn: TurnInput,
        llm_output: str,
        response_plan: ResponsePlan,
    ) -> None:
        """Score output quality and record in the ConversationQualityScorer."""
        if self._evaluator is None:
            return
        try:
            score = self._evaluator.score(turn, llm_output, response_plan)
            self._quality.record_turn_score(
                turn_id=turn.turn_id,
                call_id=turn.call_id,
                coherence=score.coherence,
                policy_compliance=score.policy_compliance,
                empathy=score.empathy,
                factual_accuracy=score.factual_accuracy,
            )
        except Exception:
            logger.exception("ConversationEngine: quality scoring failed for turn %s", turn.turn_id)
