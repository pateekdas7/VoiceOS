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
from datetime import datetime

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
from ..dialogue_policy.constraints import PolicyConstraintType
from ..dialogue_policy.engine import DialoguePolicyEngine
from ..emotion.engine import EmotionIntelligenceEngine
from ..emotion.result import EmotionSignal
from ..empathy.engine import EmpathyPlanner
from ..entity_extraction.engine import EntityExtractor
from ..goal_planner.engine import GoalPlanner
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


def _convert_negotiation_envelope(
    eng_env: EngineNegotiationEnvelope,
    move: NegotiationMove,
    proposed_minor: int | None,
) -> ContractsNegotiationEnvelope:
    return ContractsNegotiationEnvelope(
        floor_minor=eng_env.floor_amount.amount_minor,
        ceiling_minor=eng_env.ceiling_amount.amount_minor,
        move_type=_NEG_MOVE_TO_CONTRACT.get(move, NegotiationMoveType.PARTIAL_PAYMENT),
        proposed_amount_minor=proposed_minor,
    )


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

    def assemble(
        self,
        turn: TurnInput,
        context: CustomerContext | None,
        retrieval: list[Snippet],
        intent_history: list[str] | None = None,
        identity_verified: bool = False,
        silence_duration_ms: int = 0,
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

        Returns:
            Tuple of (ResponsePlan, DecisionEnvelope).
        """
        decisions: list[DecisionRecord] = []
        call_id = turn.call_id
        tenant_id = turn.tenant_id

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
            stress_level=emotion_signal.stress_level,
        )
        policy_constraints: list[PolicyConstraintType] = self._policy.evaluate(
            turn=turn,
            context=context,
            risk=risk_assessment,
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
            conversation_state=self._infer_state(intent_result, risk_assessment),
            risk=risk_assessment,
            stress_level=emotion_signal.stress_level,
            identity_verified=identity_verified,
        )
        goal = self._goal.plan(
            context=context,
            primary_intent=intent_result.label,
            risk=risk_assessment,
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
        if strategy_sel.action == EngineStrategyAction.NEGOTIATE and context is not None:
            eng_env = self._negotiation.build_envelope(context)
            neg_result = self._negotiation.compute_move(eng_env)
            proposed_minor = neg_result.proposed_amount.amount_minor if neg_result.proposed_amount is not None else None
            contracts_neg_envelope = _convert_negotiation_envelope(eng_env, neg_result.move, proposed_minor)
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
        # Assemble ResponsePlan
        # ------------------------------------------------------------------
        plan_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Adjust strategy if adaptive engine recommends escalation.
        final_strategy_label = _ENGINE_STRATEGY_TO_LABEL.get(strategy_sel.action, StrategyLabel.ASK)
        if adaptive_signal.escalation_recommended:
            final_strategy_label = StrategyLabel.ESCALATE

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
    def _infer_state(intent_result: IntentResult, risk: RiskAssessment) -> str:
        """Infer a conversation state label from intent and risk.

        This is a lightweight heuristic used in Sprint-012 before the full
        ConversationStateIntelligence integration. Sprint-013 will replace this
        with Redis-backed state tracking.
        """
        from src.libs.contracts.response_plan import IntentLabel

        from ..risk.flags import RiskFlag as EngineRF

        if EngineRF.ABUSE_DETECTED in risk.flags:
            return "CLOSING"
        if intent_result.label == IntentLabel.DISCONNECT:
            return "CLOSING"
        if intent_result.label == IntentLabel.DISPUTE:
            return "DISPUTE_HANDLING"
        if intent_result.label == IntentLabel.HARDSHIP:
            return "HARDSHIP_HANDLING"
        if intent_result.label in (IntentLabel.PAYMENT, IntentLabel.PROMISE_TO_PAY):
            return "NEGOTIATION"
        if intent_result.label == IntentLabel.IDENTITY_VERIFY:
            return "VERIFICATION"
        return "DEBT_DISCUSSION"
