"""ResponsePlanningEngine — CIL pipeline orchestrator.

Runs all Perception Layer and Decision Layer engines for a single turn,
assembles their outputs into a sealed ResponsePlan, and produces a
DecisionEnvelope for the event log (AR-5).

All engines are injected at construction time (dependency inversion). The
engine itself is deterministic: given the same inputs and the same engine
instances it produces the same plan (RI-7).

Architecture: V2 Ch1 (Conversation Engine), V2 Ch15 (ResponsePlan / DecisionEnvelope).
Invariants: RI-4 (commit-before-act), RI-5 (Law of Authority).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.decision import (
    DecisionEnvelope,
    DecisionReason,
    DecisionRecord,
    GovernanceStatus,
    GovernanceVerdict,
)
from src.libs.contracts.response_plan import (
    DeliverySpec,
    EmotionSpec,
    MustNotSayItem,
    MustSayItem,
    NegotiationMoveType,
    PolicyConstraint,
    ResponsePlan,
    RiskLevel,
    Snippet,
    StrategyLabel,
)
from src.libs.contracts.response_plan import (
    NegotiationEnvelope as ContractsNegotiationEnvelope,
)
from src.libs.contracts.response_plan import (
    RiskFlag as ContractsRiskFlag,
)
from src.libs.contracts.response_plan import (
    StrategyAction as ContractsStrategyAction,
)
from src.libs.contracts.streaming import EmpathyConfig
from src.libs.contracts.turn import TurnInput

from ..adaptive_conversation.engine import AdaptiveConversationEngine, AdaptiveSignal
from ..conversation_state.engine import ConversationStateIntelligence, InvalidTransitionError
from ..conversation_state.schema import ConversationState
from ..dialogue_policy.constraints import PolicyConstraintType
from ..dialogue_policy.engine import DialoguePolicyEngine
from ..emotion.engine import EmotionIntelligenceEngine
from ..emotion.result import EmotionSignal
from ..empathy.engine import EmpathyPlanner
from ..entity_extraction.engine import EntityExtractor
from ..entity_extraction.result import ExtractedEntities
from ..entity_extraction.slots import EntityType
from ..goal_planner.engine import GoalPlanner
from ..goal_planner.goals import Goal
from ..intent.engine import IntentEngine
from ..intent.result import IntentResult
from ..negotiation.engine import NegotiationEngine
from ..negotiation.envelope import NegotiationEnvelope as EngineNegotiationEnvelope
from ..negotiation.moves import NegotiationMove
from ..risk.engine import RiskEngine
from ..risk.flags import RiskFlag as EngineRiskFlag
from ..risk.result import RiskAssessment
from ..strategy.actions import StrategyAction as EngineStrategyAction
from ..strategy.engine import StrategyEngine, StrategySelection

# Sales Intelligence Layer (Phase 2) — optional wiring; defaults to None so
# existing tests and pre-Phase-2 callers are unaffected.
try:
    from ..sales.action_planner import SalesActionPlanner
    from ..sales.question_selector import QuestionSelector
    from ..sales.schema import SalesState
    from ..sales.state_updater import SalesStateUpdater

    _SALES_AVAILABLE = True
except ImportError:
    _SALES_AVAILABLE = False
    SalesStateUpdater = None  # type: ignore[misc,assignment]
    QuestionSelector = None  # type: ignore[misc,assignment]
    SalesActionPlanner = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

PLAN_VERSION = 1

# ---------------------------------------------------------------------------
# Engine-internal → contracts type conversions
# ---------------------------------------------------------------------------

_RISK_FLAG_TO_LEVEL: dict[str, RiskLevel] = {
    EngineRiskFlag.ABUSE_DETECTED: RiskLevel.CRITICAL,
    EngineRiskFlag.LEGAL_THREAT: RiskLevel.HIGH,
    EngineRiskFlag.ESCALATION_TRIGGER: RiskLevel.HIGH,
    EngineRiskFlag.DISPUTE_CLAIM: RiskLevel.MEDIUM,
    EngineRiskFlag.HARDSHIP_INDICATOR: RiskLevel.MEDIUM,
    EngineRiskFlag.ELDERLY_VULNERABLE: RiskLevel.MEDIUM,
}

_RISK_FLAG_DESCRIPTIONS: dict[str, str] = {
    EngineRiskFlag.ABUSE_DETECTED: "Abusive or threatening language detected.",
    EngineRiskFlag.LEGAL_THREAT: "Customer threatened legal action.",
    EngineRiskFlag.ESCALATION_TRIGGER: "Escalation warranted by customer language or intent.",
    EngineRiskFlag.DISPUTE_CLAIM: "Customer disputes the debt or account details.",
    EngineRiskFlag.HARDSHIP_INDICATOR: "Financial or personal hardship signal detected.",
    EngineRiskFlag.ELDERLY_VULNERABLE: "Customer may be elderly or otherwise vulnerable.",
}

_POLICY_CONSTRAINT_DESCRIPTIONS: dict[str, str] = {
    PolicyConstraintType.MUST_DISCLOSE_RECORDING: "Disclose that this call is being recorded.",
    PolicyConstraintType.MUST_NOT_THREATEN: "Do not threaten the customer with any legal action.",
    PolicyConstraintType.MUST_NOT_HARASS: "Do not make harassing or intimidating statements.",
    PolicyConstraintType.MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE: "Verify identity before disclosing account details.",
    PolicyConstraintType.MUST_RESPECT_DND: "Respect Do-Not-Disturb preferences.",
    PolicyConstraintType.MUST_REFERENCE_DPD_CORRECTLY: "Reference overdue status using the authoritative DPD figure.",
    PolicyConstraintType.MUST_NOT_MISREPRESENT_AMOUNT: "Never state an amount that differs from the authoritative balance.",
}

_NEG_MOVE_TO_CONTRACT: dict[str, NegotiationMoveType] = {
    NegotiationMove.OFFER: NegotiationMoveType.PARTIAL_PAYMENT,
    NegotiationMove.COUNTER: NegotiationMoveType.PARTIAL_PAYMENT,
    NegotiationMove.ACCEPT: NegotiationMoveType.FULL_PAYMENT,
    NegotiationMove.HOLD: NegotiationMoveType.CALLBACK_SCHEDULE,
    NegotiationMove.DECLINE: NegotiationMoveType.PARTIAL_PAYMENT,
    NegotiationMove.PROPOSE_PTP: NegotiationMoveType.CALLBACK_SCHEDULE,
}

_ENGINE_STRATEGY_TO_LABEL: dict[str, StrategyLabel] = {
    EngineStrategyAction.ASK: StrategyLabel.ASK,
    EngineStrategyAction.VERIFY: StrategyLabel.VERIFY,
    EngineStrategyAction.NEGOTIATE: StrategyLabel.NEGOTIATE,
    EngineStrategyAction.REASSURE: StrategyLabel.REASSURE,
    EngineStrategyAction.ESCALATE: StrategyLabel.ESCALATE,
    EngineStrategyAction.TRANSFER: StrategyLabel.TRANSFER,
    EngineStrategyAction.CLOSE: StrategyLabel.CLOSE,
    EngineStrategyAction.CONFIRM: StrategyLabel.CONFIRM,
}


def _convert_risk_flags(risk: RiskAssessment) -> tuple[ContractsRiskFlag, ...]:
    flags = []
    for flag_enum in risk.flags:
        flags.append(
            ContractsRiskFlag(
                flag_id=flag_enum.value,
                level=_RISK_FLAG_TO_LEVEL.get(flag_enum, RiskLevel.MEDIUM),
                description=_RISK_FLAG_DESCRIPTIONS.get(flag_enum, flag_enum.value),
            )
        )
    return tuple(flags)


def _convert_policy_constraints(
    constraint_types: list[PolicyConstraintType],
) -> tuple[PolicyConstraint, ...]:
    result = []
    for ct in constraint_types:
        desc = _POLICY_CONSTRAINT_DESCRIPTIONS.get(ct, ct.value)
        result.append(
            PolicyConstraint(
                rule_id=ct.value,
                description=desc,
                is_hard_rule=True,
            )
        )
    return tuple(result)


_FINALIZED_COMMITMENT_MOVES = frozenset({NegotiationMove.ACCEPT, NegotiationMove.PROPOSE_PTP})


def _convert_negotiation_envelope(
    eng_env: EngineNegotiationEnvelope,
    move: NegotiationMove,
    proposed_minor: int | None,
    proposed_date: date | None = None,
) -> ContractsNegotiationEnvelope:
    return ContractsNegotiationEnvelope(
        floor_minor=eng_env.floor_amount.amount_minor,
        ceiling_minor=eng_env.ceiling_amount.amount_minor,
        move_type=_NEG_MOVE_TO_CONTRACT.get(move, NegotiationMoveType.PARTIAL_PAYMENT),
        proposed_amount_minor=proposed_minor,
        proposed_date=proposed_date,
        is_finalized_commitment=move in _FINALIZED_COMMITMENT_MOVES,
    )


def _extract_customer_offer_minor(entity_result: ExtractedEntities) -> int | None:
    """Extract a customer-stated offer amount (minor units) from entity slots.

    Prefers PARTIAL_AMOUNT (an explicit partial-payment offer) over the
    generic AMOUNT slot. EntityExtractor normalizes amounts to plain rupee
    digit strings (e.g. "5000" for Rs.5,000), not minor units — this helper
    converts to minor units (paise) for NegotiationEngine.compute_move().
    """
    slot = entity_result.get(EntityType.PARTIAL_AMOUNT) or entity_result.get(EntityType.AMOUNT)
    if slot is None:
        return None
    try:
        return int(slot.normalized) * 100
    except (ValueError, TypeError):
        return None


def _extract_customer_proposed_date(entity_result: ExtractedEntities) -> date | None:
    """Extract a customer-stated promise/payment date from entity slots.

    Prefers PROMISE_DATE over the generic DATE slot. Both are normalized to
    ISO 8601 (YYYY-MM-DD) by EntityExtractor.
    """
    slot = entity_result.get(EntityType.PROMISE_DATE) or entity_result.get(EntityType.DATE)
    if slot is None:
        return None
    try:
        return date.fromisoformat(slot.normalized)
    except ValueError:
        return None


def _convert_emotion(signal: EmotionSignal) -> EmotionSpec:
    return EmotionSpec(
        sentiment=signal.valence,
        arousal=signal.arousal,
        dominant_emotion=signal.dominant_emotion,
    )


def _make_must_say_items(
    policy_constraints: list[PolicyConstraintType],
) -> tuple[MustSayItem, ...]:
    items = []
    if PolicyConstraintType.MUST_DISCLOSE_RECORDING in policy_constraints:
        items.append(
            MustSayItem(
                item_id="RBI_RECORDING_DISCLOSURE",
                text="Yeh call recording ki ja rahi hai.",
                is_exact_match=False,
            )
        )
    return tuple(items)


def _make_must_not_say_items(
    policy_constraints: list[PolicyConstraintType],
) -> tuple[MustNotSayItem, ...]:
    items = []
    if PolicyConstraintType.MUST_NOT_THREATEN in policy_constraints:
        items.append(
            MustNotSayItem(
                item_id="NO_THREAT_OF_LEGAL_ACTION",
                description="Do not threaten legal action, police, or court.",
                pattern=r"\b(police|court|jail|sue|legal action)\b",
            )
        )
    if PolicyConstraintType.MUST_NOT_HARASS in policy_constraints:
        items.append(
            MustNotSayItem(
                item_id="NO_HARASSMENT",
                description="Do not use harassing or intimidating language.",
                pattern=r"\b(stupid|idiot|fool|pagal|bewakoof)\b",
            )
        )
    return tuple(items)


def _make_decision_record(
    call_id: str,
    tenant_id: str,
    source_engine: str,
    decision: str,
    confidence: float,
    evidence: tuple[str, ...] = (),
    reasoning: str = "",
) -> DecisionRecord:
    return DecisionRecord(
        record_id=str(uuid.uuid4()),
        call_id=call_id,
        tenant_id=tenant_id,
        reason=DecisionReason(
            decision_id=str(uuid.uuid4()),
            source_engine=source_engine,
            decision=decision,
            confidence=confidence,
            evidence=evidence,
            reasoning=reasoning,
        ),
    )


class ResponsePlanningEngine:
    """Orchestrates all CIL engines for one turn and assembles a ResponsePlan.

    All engine instances are injected; this class holds no engine-specific
    configuration. Tests may inject mock engines.

    Architecture: V2 Ch1, V2 Ch15.
    Invariants: RI-5 (all customer facts from CustomerContext, never LLM).
    """

    def __init__(
        self,
        intent_engine: IntentEngine,
        entity_extractor: EntityExtractor,
        emotion_engine: EmotionIntelligenceEngine,
        risk_engine: RiskEngine,
        dialogue_policy_engine: DialoguePolicyEngine,
        strategy_engine: StrategyEngine,
        goal_planner: GoalPlanner,
        negotiation_engine: NegotiationEngine,
        empathy_planner: EmpathyPlanner,
        adaptive_conv_engine: AdaptiveConversationEngine,
        # Phase 2: Sales Intelligence Layer — all optional so existing callers
        # and tests remain unaffected (None = sales layer disabled).
        sales_state_updater: "SalesStateUpdater | None" = None,
        question_selector: "QuestionSelector | None" = None,
        sales_action_planner: "SalesActionPlanner | None" = None,
    ) -> None:
        self._intent = intent_engine
        self._entity = entity_extractor
        self._emotion = emotion_engine
        self._risk = risk_engine
        self._policy = dialogue_policy_engine
        self._strategy = strategy_engine
        self._goal = goal_planner
        self._negotiation = negotiation_engine
        self._empathy = empathy_planner
        self._adaptive = adaptive_conv_engine
        # Sales layer (Phase 2)
        self._sales_state_updater = sales_state_updater
        self._question_selector = question_selector
        self._sales_action_planner = sales_action_planner

    def assemble(
        self,
        turn: TurnInput,
        context: CustomerContext | None,
        retrieval: list[Snippet],
        intent_history: list[str] | None = None,
        identity_verified: bool = False,
        silence_duration_ms: int = 0,
        conversation_state_tracker: ConversationStateIntelligence | None = None,
        concession_round: int = 0,
        previous_sales_state: dict | None = None,
    ) -> tuple[ResponsePlan, DecisionEnvelope]:
        """Assemble a ResponsePlan and DecisionEnvelope for one turn.

        Runs engines in the following order (dependencies constrain parallelism):
          Stage 1 (independent): IntentEngine, EntityExtractor, EmotionEngine
          Stage 2 (needs stage 1): RiskEngine, DialoguePolicyEngine
          Stage 3 (needs stage 2): StrategyEngine, GoalPlanner
          Stage 4 (needs stage 3): NegotiationEngine (if strategy==NEGOTIATE)
          Stage 5 (needs emotion): EmpathyPlanner
          Stage 6: AdaptiveConversationEngine

        Args:
            turn: Finalized TurnInput for this turn.
            context: Authoritative CustomerContext (may be None for tests).
            retrieval: Pre-fetched knowledge snippets (from KnowledgeRetrievalService).
            intent_history: List of recent intent label strings for loop detection.
            identity_verified: Whether identity has been verified for this call.
            silence_duration_ms: Customer silence duration in milliseconds.
            conversation_state_tracker: Caller-owned ConversationStateIntelligence
                instance for this call. The same instance must be passed on every
                turn so state persists correctly across the call (mirrors the
                intent_history/silence_duration_ms caller-owned pattern below).
                If None, a fresh tracker (GREETING) is used for this call only —
                safe for single-turn tests, but without cross-turn identity.
            concession_round: Number of negotiation concession rounds already
                completed this call (caller-tracked, incremented once per
                COUNTER move — mirrors intent_history/silence_duration_ms).

        Returns:
            Tuple of (ResponsePlan, DecisionEnvelope).
        """
        decisions: list[DecisionRecord] = []
        call_id = turn.call_id
        tenant_id = turn.tenant_id
        tracker = conversation_state_tracker if conversation_state_tracker is not None else ConversationStateIntelligence()

        # ------------------------------------------------------------------
        # Stage 1: Perception
        # ------------------------------------------------------------------
        intent_result: IntentResult = self._intent.classify(turn)
        entity_result = self._entity.extract(turn)
        emotion_signal: EmotionSignal = self._emotion.analyze(turn)

        decisions.append(
            _make_decision_record(
                call_id=call_id,
                tenant_id=tenant_id,
                source_engine="IntentEngine",
                decision=f"INTENT={intent_result.label.value}",
                confidence=intent_result.confidence,
                evidence=(intent_result.source_span,),
                reasoning=intent_result.reasoning_hint,
            )
        )
        decisions.append(
            _make_decision_record(
                call_id=call_id,
                tenant_id=tenant_id,
                source_engine="EmotionIntelligenceEngine",
                decision=f"SENTIMENT={emotion_signal.sentiment.value} STRESS={emotion_signal.stress_level.value}",
                confidence=0.85,
            )
        )

        # ------------------------------------------------------------------
        # Stage 2: Risk + Policy
        # ------------------------------------------------------------------
        risk_assessment: RiskAssessment = self._risk.evaluate(
            turn=turn,
            sentiment=emotion_signal.sentiment,
            stress_level=emotion_signal.stress_level,
        )
        policy_constraints: list[PolicyConstraintType] = self._policy.evaluate(
            turn=turn,
            context=context,
            risk=risk_assessment,
            turn_index=turn.turn_index,
            identity_verified=identity_verified,
        )

        decisions.append(
            _make_decision_record(
                call_id=call_id,
                tenant_id=tenant_id,
                source_engine="RiskEngine",
                decision=f"FLAGS={[f.value for f in risk_assessment.flags]}",
                confidence=0.9,
                reasoning=f"escalation={risk_assessment.escalation_required}",
            )
        )

        # ------------------------------------------------------------------
        # Stage 3: Strategy + Goal
        # ------------------------------------------------------------------
        strategy_sel: StrategySelection = self._strategy.select(
            primary_intent=intent_result.label,
            conversation_state=tracker.current_state.value,
            risk=risk_assessment,
            stress_level=emotion_signal.stress_level,
            identity_verified=identity_verified,
        )
        goal = self._goal.plan(
            context=context,
            primary_intent=intent_result.label,
            risk=risk_assessment,
            conversation_state=tracker.current_state.value,
            identity_verified=identity_verified,
        )

        decisions.append(
            _make_decision_record(
                call_id=call_id,
                tenant_id=tenant_id,
                source_engine="StrategyEngine",
                decision=f"STRATEGY={strategy_sel.action.value}",
                confidence=strategy_sel.confidence,
                reasoning=strategy_sel.rationale,
            )
        )
        decisions.append(
            _make_decision_record(
                call_id=call_id,
                tenant_id=tenant_id,
                source_engine="GoalPlanner",
                decision=f"GOAL={goal.value}",
                confidence=0.95,
            )
        )

        # ------------------------------------------------------------------
        # Stage 4: Negotiation (only when strategy == NEGOTIATE)
        # ------------------------------------------------------------------
        contracts_neg_envelope: ContractsNegotiationEnvelope | None = None
        neg_move: NegotiationMove | None = None
        if strategy_sel.action == EngineStrategyAction.NEGOTIATE and context is not None:
            eng_env = self._negotiation.build_envelope(context)
            neg_result = self._negotiation.compute_move(
                eng_env,
                customer_offer_minor=_extract_customer_offer_minor(entity_result),
                concession_round=concession_round,
                hardship_verified=EngineRiskFlag.HARDSHIP_INDICATOR in risk_assessment.flags,
                customer_proposed_date=_extract_customer_proposed_date(entity_result),
            )
            neg_move = neg_result.move
            proposed_minor = neg_result.proposed_amount.amount_minor if neg_result.proposed_amount is not None else None
            contracts_neg_envelope = _convert_negotiation_envelope(
                eng_env, neg_result.move, proposed_minor, neg_result.proposed_date
            )
            decisions.append(
                _make_decision_record(
                    call_id=call_id,
                    tenant_id=tenant_id,
                    source_engine="NegotiationEngine",
                    decision=f"MOVE={neg_result.move.value}",
                    confidence=0.9,
                    reasoning=neg_result.rationale,
                )
            )

        # ------------------------------------------------------------------
        # Stage 5: Empathy
        # ------------------------------------------------------------------
        empathy_config: EmpathyConfig = self._empathy.plan(
            stress_level=emotion_signal.stress_level,
            sentiment=emotion_signal.sentiment,
            preferred_language="hi-IN",
        )

        decisions.append(
            _make_decision_record(
                call_id=call_id,
                tenant_id=tenant_id,
                source_engine="EmpathyPlanner",
                decision=f"TONE={empathy_config.tone.value} PACING={empathy_config.pacing.value}",
                confidence=0.88,
            )
        )

        # ------------------------------------------------------------------
        # Stage 6: Adaptive Conversation
        # ------------------------------------------------------------------
        from src.libs.contracts.response_plan import IntentLabel as ContractsIntentLabel

        history_labels = [
            ContractsIntentLabel(h) if isinstance(h, str) else h
            for h in (intent_history or [])
            if isinstance(h, (str, ContractsIntentLabel))
        ]
        adaptive_signal: AdaptiveSignal = self._adaptive.process(
            intent_history=history_labels,
            silence_duration_ms=silence_duration_ms,
        )

        # ------------------------------------------------------------------
        # Stage 7: Sales Intelligence (Phase 2 — skipped when not wired)
        # ------------------------------------------------------------------
        sales_state_dict: dict | None = None
        if (
            self._sales_state_updater is not None
            and self._question_selector is not None
            and self._sales_action_planner is not None
        ):
            updated_sales_state = self._sales_state_updater.update(
                previous_state=previous_sales_state,
                entities=entity_result,
                intent=intent_result,
                conversation_state=self._infer_state(intent_result, risk_assessment),
                strategy=strategy_sel,
                risk=risk_assessment,
            )
            next_question = self._question_selector.select(
                updated_sales_state, intent_result, strategy_sel, risk_assessment
            )
            sales_action = self._sales_action_planner.plan(
                updated_sales_state, intent_result, strategy_sel, risk_assessment, goal, next_question
            )
            updated_sales_state.next_action = sales_action
            updated_sales_state.next_question = next_question
            sales_state_dict = updated_sales_state.to_dict()

        # ------------------------------------------------------------------
        # Assemble ResponsePlan
        # ------------------------------------------------------------------
        plan_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Adjust strategy if adaptive engine recommends escalation.
        final_strategy_label = _ENGINE_STRATEGY_TO_LABEL.get(strategy_sel.action, StrategyLabel.ASK)
        if adaptive_signal.escalation_recommended:
            final_strategy_label = StrategyLabel.ESCALATE

        # ------------------------------------------------------------------
        # Conversation state transition (real ConversationStateIntelligence —
        # replaces the Sprint-012 _infer_state heuristic). The requested next
        # state is derived deterministically from this turn's own decisions,
        # then validated against ALLOWED_TRANSITIONS before being applied;
        # an illegal request is logged and the tracker stays at its current
        # state (RI-5: the LLM never decides state transitions, and neither
        # does an unvalidated guess).
        # ------------------------------------------------------------------
        requested_state = self._determine_next_state(
            current_state=tracker.current_state,
            intent_result=intent_result,
            risk_assessment=risk_assessment,
            goal=goal,
            strategy_label=final_strategy_label,
            negotiation_move=neg_move,
            identity_verified=identity_verified,
        )
        if requested_state != tracker.current_state:
            try:
                tracker.transition(requested_state)
            except InvalidTransitionError as exc:
                logger.warning(
                    "ResponsePlanningEngine: rejected illegal state transition",
                    extra={"call_id": call_id, "error": str(exc)},
                )
        decisions.append(
            _make_decision_record(
                call_id=call_id,
                tenant_id=tenant_id,
                source_engine="ConversationStateIntelligence",
                decision=f"STATE={tracker.current_state.value}",
                confidence=1.0,
                reasoning=f"requested={requested_state.value}",
            )
        )

        facts: dict[str, str | int | float | bool | None] = {}
        if context and context.outstanding:
            facts["outstanding_balance_minor"] = context.outstanding.total_outstanding.amount_minor
        if context and context.primary_loan:
            facts["dpd"] = context.primary_loan.dpd

        response_plan = ResponsePlan(
            plan_id=plan_id,
            version=PLAN_VERSION,
            call_id=call_id,
            tenant_id=tenant_id,
            created_at=now,
            intents=(intent_result.to_signal(),),
            entities={k: v.normalized for k, v in entity_result.slots.items()},
            emotion=_convert_emotion(emotion_signal),
            policy_constraints=_convert_policy_constraints(policy_constraints),
            risk_flags=_convert_risk_flags(risk_assessment),
            goal=goal.value,
            strategy=ContractsStrategyAction(
                action=final_strategy_label,
                rationale=strategy_sel.rationale,
            ),
            negotiation_envelope=contracts_neg_envelope,
            delivery=DeliverySpec(
                language="hi-IN",
                voice_id="veena-default",
                target_speaking_rate=1.0 if empathy_config.pacing.value == "normal" else 0.85,
            ),
            facts=facts,
            retrieval=retrieval,
            must_say=_make_must_say_items(policy_constraints),
            must_not_say=_make_must_not_say_items(policy_constraints),
            sales_state=sales_state_dict,
        )

        # ------------------------------------------------------------------
        # Assemble DecisionEnvelope
        # ------------------------------------------------------------------
        envelope_id = str(uuid.uuid4())
        decision_envelope = DecisionEnvelope(
            envelope_id=envelope_id,
            call_id=call_id,
            tenant_id=tenant_id,
            timestamp=now,
            decisions=tuple(decisions),
            response_plan_id=plan_id,
            governance_verdict=GovernanceVerdict(status=GovernanceStatus.APPROVE),
            correlation_id=turn.correlation_id,
            trace_id=turn.trace_id,
        )

        logger.info(
            "ResponsePlanningEngine: plan assembled",
            extra={
                "plan_id": plan_id,
                "call_id": call_id,
                "strategy": final_strategy_label.value,
                "goal": goal.value,
                "intent": intent_result.label.value,
                "adaptive_action": adaptive_signal.recommended_action,
            },
        )

        return response_plan, decision_envelope

    @staticmethod
    def _determine_next_state(
        current_state: ConversationState,
        intent_result: IntentResult,
        risk_assessment: RiskAssessment,
        goal: Goal,
        strategy_label: StrategyLabel,
        negotiation_move: NegotiationMove | None,
        identity_verified: bool,
    ) -> ConversationState:
        """Determine the requested next ConversationState for this turn.

        Replaces the Sprint-012 ``_infer_state`` heuristic (which returned ad
        hoc strings never validated against the real state machine). This
        method only *proposes* a target state deterministically from the
        turn's own decisions — it never mutates a tracker itself; the caller
        validates the proposal via ``ConversationStateIntelligence.transition()``
        (RI-5/RI-7: no ungoverned state changes).

        Architecture: V2 Ch13 (Conversation State Intelligence).
        """
        from src.libs.contracts.response_plan import IntentLabel

        if EngineRiskFlag.ABUSE_DETECTED in risk_assessment.flags or goal == Goal.TRANSFER_AGENT:
            return ConversationState.ESCALATION
        if intent_result.label in (IntentLabel.DISCONNECT, IntentLabel.CONSENT_REVOKE) or goal == Goal.END_CALL:
            return ConversationState.CLOSING
        if current_state == ConversationState.GREETING:
            return ConversationState.IDENTITY_VERIFICATION
        if current_state == ConversationState.IDENTITY_VERIFICATION:
            return ConversationState.DEBT_DISCUSSION if identity_verified else current_state
        if intent_result.label == IntentLabel.DISPUTE or EngineRiskFlag.DISPUTE_CLAIM in risk_assessment.flags:
            return ConversationState.OBJECTION_HANDLING
        if negotiation_move in (NegotiationMove.ACCEPT, NegotiationMove.PROPOSE_PTP):
            return ConversationState.COMMITMENT_CAPTURE
        if strategy_label == StrategyLabel.NEGOTIATE:
            return ConversationState.NEGOTIATION
        if strategy_label == StrategyLabel.CLOSE:
            return ConversationState.CLOSING
        return current_state
