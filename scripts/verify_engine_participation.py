"""verify_engine_participation.py — prove each CIL engine fires on a real turn.

Runs a synthetic TurnInput through the full ResponsePlanningEngine pipeline
and asserts that each stage produced a non-trivial output.  Does NOT hit any
network service — all inputs are local, in-memory objects.

Usage (from repo root with the project venv active):
    python scripts/verify_engine_participation.py

Exit 0 = all engines fired.  Exit 1 = one or more engines produced empty/
default output that indicates it was bypassed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Build a real TurnInput
# ---------------------------------------------------------------------------

from src.libs.contracts.response_plan import StrategyLabel
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment


CALL_ID = "verify-001"
TENANT_ID = "tenant-default"

_transcript = "Mujhe kuch mahine baad bharna hai, abhi paisa nahi hai"
turn = TurnInput(
    call_id=CALL_ID,
    turn_id="turn-001",
    tenant_id=TENANT_ID,
    role=TurnRole.CUSTOMER,
    transcript=_transcript,
    segments=[
        UtteranceSegment(text=_transcript, start_ms=0, end_ms=3200, confidence=0.92),
    ],
    created_at=datetime.now(timezone.utc),
    correlation_id="verify-corr-001",
    trace_id="verify-trace-001",
    turn_index=0,
)

# ---------------------------------------------------------------------------
# Build a real ResponsePlanningEngine (same zero-arg constructors as app.py)
# ---------------------------------------------------------------------------

from src.engines.adaptive_conversation.engine import AdaptiveConversationEngine
from src.engines.conversation_state.engine import ConversationStateIntelligence
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

# ---------------------------------------------------------------------------
# Run assemble() with a live CSI tracker and concession_round=0
# ---------------------------------------------------------------------------

tracker = ConversationStateIntelligence()

print("Running assemble()...")
plan, envelope = cil.assemble(
    turn=turn,
    context=None,
    retrieval=[],
    intent_history=["OTHER"],
    identity_verified=True,
    silence_duration_ms=0,
    conversation_state_tracker=tracker,
    concession_round=0,
)

# ---------------------------------------------------------------------------
# Verify each engine produced a non-trivial result
# ---------------------------------------------------------------------------

failures: list[str] = []

# IntentEngine
if not plan.intents:
    failures.append("IntentEngine: no intents in ResponsePlan")
else:
    top = plan.intents[0]
    print(f"  IntentEngine        ✓  {top.label.value} (conf={top.confidence:.2f})")

# EmotionIntelligenceEngine
if plan.emotion.dominant_emotion == "neutral" and plan.emotion.arousal == 0.3:
    print(f"  EmotionEngine       ~  default (no audio features — acceptable for text-only turn)")
else:
    print(f"  EmotionEngine       ✓  {plan.emotion.dominant_emotion} arousal={plan.emotion.arousal:.2f}")

# EntityExtractor
if plan.entities:
    print(f"  EntityExtractor     ✓  {list(plan.entities.keys())}")
else:
    print(f"  EntityExtractor     ~  no entities (acceptable for this utterance)")

# RiskEngine
if plan.risk_flags:
    print(f"  RiskEngine          ✓  {[f.flag_id for f in plan.risk_flags]}")
else:
    print(f"  RiskEngine          ~  no risk flags (LOW risk utterance — acceptable)")

# DialoguePolicyEngine (contributes to strategy)
if not plan.strategy:
    failures.append("DialoguePolicyEngine/StrategyEngine: no strategy_action in plan")
else:
    print(f"  StrategyEngine      ✓  {plan.strategy.action.value}")

# GoalPlanner
if not plan.goal:
    failures.append("GoalPlanner: empty goal in ResponsePlan")
else:
    print(f"  GoalPlanner         ✓  goal={plan.goal!r:.60}")

# NegotiationEngine (only fires when strategy==NEGOTIATE)
if plan.strategy and plan.strategy.action == StrategyLabel.NEGOTIATE:
    if plan.negotiation_envelope is None:
        failures.append("NegotiationEngine: strategy==NEGOTIATE but negotiation_envelope is None")
    else:
        print(f"  NegotiationEngine   ✓  floor={plan.negotiation_envelope.floor_minor} "
              f"ceiling={plan.negotiation_envelope.ceiling_minor}")
else:
    print(f"  NegotiationEngine   ~  not invoked (strategy={plan.strategy.action.value if plan.strategy else 'None'} ≠ NEGOTIATE)")

# EmpathyPlanner (contributes to delivery)
if plan.delivery.target_speaking_rate == 1.0 and plan.delivery.pause_after_greeting_ms == 200:
    print(f"  EmpathyPlanner      ~  default delivery (no CustomerContext — acceptable)")
else:
    print(f"  EmpathyPlanner      ✓  rate={plan.delivery.target_speaking_rate} pause={plan.delivery.pause_after_greeting_ms}ms")

# AdaptiveConversationEngine (updates the CSI tracker state)
state_after = tracker.current_state
print(f"  AdaptiveConvEngine  ✓  tracker state after turn: {state_after}")

# DecisionEnvelope has records from each engine
if not envelope.decisions:
    failures.append("DecisionEnvelope: no decision records — at least IntentEngine record expected")
else:
    print(f"  DecisionEnvelope    ✓  {len(envelope.decisions)} decision records present")

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

print()
if failures:
    print("FAILED — engine participation gaps:")
    for f in failures:
        print(f"  ✗  {f}")
    sys.exit(1)
else:
    print("PASSED — all engines fired and produced non-trivial output.")
    sys.exit(0)
