"""SalesStateUpdater — derives SalesState from CIL outputs.

This class is the sole source of SalesState mutations. It runs after all CIL
engines and produces an updated SalesState from the previous one (or a fresh
one on first turn). All logic is deterministic; no LLM calls, no network I/O.

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy

from src.engines.entity_extraction.result import ExtractedEntities
from src.engines.intent.result import IntentResult
from src.engines.risk.result import RiskAssessment
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel

from .domains.base import DomainConfig
from .schema import (
    DecisionMaker,
    FinancingStatus,
    LeadIntent,
    LeadStage,
    LeadTemperature,
    PropertyPurpose,
    QualificationStatus,
    QuestionField,
    SalesAction,
    SalesObjective,
    SalesState,
    SiteVisitInterest,
    Timeline,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hinglish keyword maps for extracting sales-specific entities not covered
# by the base EntityExtractor (which is collections-domain focused).
# ---------------------------------------------------------------------------

_PURPOSE_KEYWORDS: dict[str, PropertyPurpose] = {
    "investment": PropertyPurpose.INVESTMENT,
    "invest": PropertyPurpose.INVESTMENT,
    "kiraya": PropertyPurpose.INVESTMENT,  # rent out
    "rent": PropertyPurpose.INVESTMENT,
    "rental": PropertyPurpose.INVESTMENT,
    "self use": PropertyPurpose.SELF_USE,
    "self-use": PropertyPurpose.SELF_USE,
    "khud ke liye": PropertyPurpose.SELF_USE,
    "apne liye": PropertyPurpose.SELF_USE,
    "rehne ke liye": PropertyPurpose.SELF_USE,
    "ghar": PropertyPurpose.SELF_USE,
    "rehna": PropertyPurpose.SELF_USE,
}

_DECISION_MAKER_KEYWORDS: dict[str, DecisionMaker] = {
    "main akela": DecisionMaker.SELF,
    "akela": DecisionMaker.SELF,
    "main khud": DecisionMaker.SELF,
    "khud": DecisionMaker.SELF,
    "myself": DecisionMaker.SELF,
    "i decide": DecisionMaker.SELF,
    "wife": DecisionMaker.SPOUSE,
    "husband": DecisionMaker.SPOUSE,
    "spouse": DecisionMaker.SPOUSE,
    "patni": DecisionMaker.SPOUSE,
    "pati": DecisionMaker.SPOUSE,
    "family": DecisionMaker.FAMILY,
    "parivar": DecisionMaker.FAMILY,
    "parents": DecisionMaker.FAMILY,
    "maa baap": DecisionMaker.FAMILY,
    "investor": DecisionMaker.INVESTOR,
    "partner": DecisionMaker.INVESTOR,
}

_FINANCING_KEYWORDS: dict[str, FinancingStatus] = {
    "home loan": FinancingStatus.HOME_LOAN,
    "bank loan": FinancingStatus.HOME_LOAN,
    "loan": FinancingStatus.HOME_LOAN,
    "karz": FinancingStatus.HOME_LOAN,
    "emi": FinancingStatus.HOME_LOAN,
    "cash": FinancingStatus.CASH,
    "naqad": FinancingStatus.CASH,
    "nakit": FinancingStatus.CASH,
    "full cash": FinancingStatus.CASH,
    "partial": FinancingStatus.PARTIAL,
    "partly": FinancingStatus.PARTIAL,
    "kuch loan": FinancingStatus.PARTIAL,
    "half loan": FinancingStatus.PARTIAL,
}

_SITE_VISIT_KEYWORDS: dict[str, SiteVisitInterest] = {
    "site visit": SiteVisitInterest.INTERESTED,
    "visit": SiteVisitInterest.INTERESTED,
    "dekh sakte": SiteVisitInterest.INTERESTED,
    "dekhna chahta": SiteVisitInterest.INTERESTED,
    "dekhna chahti": SiteVisitInterest.INTERESTED,
    "show me": SiteVisitInterest.INTERESTED,
    "confirmed": SiteVisitInterest.CONFIRMED,
    "confirm": SiteVisitInterest.CONFIRMED,
    "book": SiteVisitInterest.CONFIRMED,
    "schedule": SiteVisitInterest.CONFIRMED,
    "maybe": SiteVisitInterest.MAYBE,
    "soch lete": SiteVisitInterest.MAYBE,
    "baad mein": SiteVisitInterest.MAYBE,
    "nahi": SiteVisitInterest.NOT_INTERESTED,
    "not interested": SiteVisitInterest.NOT_INTERESTED,
    "no visit": SiteVisitInterest.NOT_INTERESTED,
}

# Budget amount extraction: "80 lakh", "1 crore" etc.
_RE_LAKH = re.compile(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac|l\b)", re.IGNORECASE)
_RE_CRORE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:crore|cr\b)", re.IGNORECASE)
_RE_BUDGET_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:lakh|lac|l\b)?\s*(?:se|to|-)\s*(\d+(?:\.\d+)?)\s*(?:lakh|lac|l\b)",
    re.IGNORECASE,
)


def _rupees_from_lakh(val: float) -> int:
    return int(val * 100_000)


def _rupees_from_crore(val: float) -> int:
    return int(val * 10_000_000)


def _extract_budget(text: str) -> tuple[int | None, int | None]:
    """Extract budget range from Hinglish text. Returns (min_rupees, max_rupees)."""
    text_lower = text.lower()

    # Range pattern: "80 se 85 lakh" or "80-85 lakh"
    m = _RE_BUDGET_RANGE.search(text_lower)
    if m:
        lo = float(m.group(1))
        hi = float(m.group(2))
        # Heuristic: if both numbers < 200 assume lakh; large numbers treated as rupees
        if lo < 200 and hi < 200:
            return _rupees_from_lakh(lo), _rupees_from_lakh(hi)
        return int(lo), int(hi)

    # Crore
    mc = _RE_CRORE.search(text_lower)
    if mc:
        val = float(mc.group(1))
        amount = _rupees_from_crore(val)
        # Single figure = both min and max (customer stated a specific number)
        return amount, amount

    # Lakh
    ml = _RE_LAKH.search(text_lower)
    if ml:
        val = float(ml.group(1))
        amount = _rupees_from_lakh(val)
        return amount, amount

    return None, None


def _keyword_match(text: str, keyword_map: dict[str, any]) -> any | None:
    """Return the first matching value from a keyword map (longest-match first)."""
    text_lower = text.lower()
    # Sort by keyword length descending for longest-match
    for kw, val in sorted(keyword_map.items(), key=lambda x: -len(x[0])):
        if kw in text_lower:
            return val
    return None


def _extract_locations(text: str, valid_locations: list[str]) -> list[str]:
    """Extract location mentions from text against known valid locations."""
    text_lower = text.lower()
    found = []
    for loc in valid_locations:
        if loc.lower() in text_lower:
            found.append(loc)
    return found


def _extract_property_types(text: str, valid_types: list[str]) -> list[str]:
    """Extract property type mentions against known valid types.

    Handles both "3BHK" (no space) and "3 BHK" (with space) as equivalent.
    """
    text_upper = text.upper()
    # Also build a space-normalised version for matching "3 BHK" → "3BHK"
    text_nospace = re.sub(r"\s+", "", text_upper)
    found = []
    for pt in valid_types:
        pt_upper = pt.upper()
        pt_nospace = re.sub(r"\s+", "", pt_upper)
        if pt_upper in text_upper or pt_nospace in text_nospace:
            found.append(pt)
    return found


def _extract_timeline(text: str, domain: DomainConfig) -> Timeline | None:
    """Extract timeline from text using domain keyword map."""
    return _keyword_match(text, domain.timeline_keywords)


# ---------------------------------------------------------------------------
# Qualification score algorithm
# ---------------------------------------------------------------------------

# Timeline score adjustments (heuristic, documented)
_TIMELINE_SCORE: dict[Timeline, int] = {
    Timeline.IMMEDIATE: 15,
    Timeline.MONTHS_1_2: 10,
    Timeline.MONTHS_3_6: 5,
    Timeline.EXPLORING: -10,
}

# Intent score adjustments
_INTENT_SCORE: dict[LeadIntent, int] = {
    LeadIntent.HIGH: 10,
    LeadIntent.MEDIUM: 5,
    LeadIntent.LOW: 0,
    LeadIntent.UNKNOWN: 0,
}


def _compute_qualification_score(
    state: SalesState,
    domain: DomainConfig,
) -> tuple[int, list[str]]:
    """Compute qualification score heuristic.

    This is a HEURISTIC, not a probability. It is a human-interpretable
    signal for prioritising leads, not a statistical model output.

    Score components:
      - +weight per confirmed required field (domain-configurable)
      - +/-timeline adjustment
      - +intent adjustment
      - -5 per objection (floored at zero total deduction)

    Max attainable score: sum(field_weights) + 15 (timeline) + 10 (intent)
    """
    reasons: list[str] = []
    score = 0

    for f in domain.required_fields:
        if f.value in state.confirmed_fields:
            w = domain.field_weights.get(f, 10)
            score += w
            reasons.append(f"+{w} {f.value} confirmed")

    # Timeline adjustment
    if state.timeline is not None:
        adj = _TIMELINE_SCORE.get(state.timeline, 0)
        score += adj
        reasons.append(f"{adj:+d} timeline={state.timeline.value}")

    # Intent adjustment
    intent_adj = _INTENT_SCORE.get(state.lead_intent, 0)
    if intent_adj:
        score += intent_adj
        reasons.append(f"{intent_adj:+d} intent={state.lead_intent.value}")

    # Objection deduction (floored at 0 total deduction)
    obj_deduction = min(state.objection_count * 5, score)  # don't drive score negative
    if obj_deduction > 0:
        score -= obj_deduction
        reasons.append(f"-{obj_deduction} objections({state.objection_count})")

    # Clamp to [0, 100]
    score = max(0, min(100, score))
    return score, reasons


class SalesStateUpdater:
    """Derives an updated SalesState from CIL outputs for one turn.

    Takes the previous SalesState (or None for first turn) and all CIL engine
    results, returns a freshly updated SalesState. No mutation of the previous
    state — returns a new instance every time.

    Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
    """

    def __init__(self, domain: DomainConfig) -> None:
        self._domain = domain

    def update(
        self,
        previous_state: SalesState | None,
        entities: ExtractedEntities,
        intent: IntentResult,
        conversation_state: str,
        strategy: StrategySelection,
        risk: RiskAssessment,
    ) -> SalesState:
        """Produce an updated SalesState for this turn.

        Args:
            previous_state: The SalesState from the previous turn, or None.
            entities: ExtractedEntities from the EntityExtractor.
            intent: IntentResult from the IntentEngine.
            conversation_state: String ConversationState from AdaptiveConversationEngine.
            strategy: StrategySelection from StrategyEngine.
            risk: RiskAssessment from RiskEngine.

        Returns:
            A new SalesState reflecting this turn's information.
        """
        # Start from previous state or fresh
        state = deepcopy(previous_state) if previous_state is not None else SalesState()

        # Shift action history
        state.previous_sales_action = state.last_sales_action
        state.last_sales_action = state.next_action  # what we planned last turn

        # Extract from turn transcript via entities (entity extractor gives raw text
        # in source_form) and also directly from the source span in IntentResult.
        # We use the IntentResult.source_span as the transcript proxy since that's
        # what EntityExtractor and IntentEngine both work on.
        transcript = intent.source_span

        self._update_requirements(state, transcript, entities)
        self._update_lead_stage(state, conversation_state)
        self._update_lead_intent(state, intent)
        self._update_objections(state, intent, risk)
        self._update_field_tracking(state)
        self._update_qualification_score(state)
        self._update_lead_temperature(state)
        self._update_qualification_status(state)
        self._update_current_objective(state, intent, risk, strategy)

        logger.debug(
            "SalesStateUpdater: state updated",
            extra={
                "lead_stage": state.lead_stage.value,
                "score": state.qualification_score,
                "confirmed_fields": state.confirmed_fields,
                "intent": intent.label.value,
            },
        )
        return state

    # ------------------------------------------------------------------
    # Private updaters — each mutates the state copy in place
    # ------------------------------------------------------------------

    def _update_requirements(
        self,
        state: SalesState,
        transcript: str,
        entities: ExtractedEntities,
    ) -> None:
        """Extract customer requirements from transcript.

        Existing CONFIRMED fields are only overwritten if the new extraction
        has high confidence AND the value changed — in that case the field
        moves to uncertain_fields until confirmed by a follow-up.
        """
        # Budget
        b_min, b_max = _extract_budget(transcript)
        if b_min is not None or b_max is not None:
            existing_min = state.budget_min
            existing_max = state.budget_max
            new_min = b_min if b_min is not None else state.budget_min
            new_max = b_max if b_max is not None else state.budget_max
            changed = (existing_min != new_min or existing_max != new_max)
            state.budget_min = new_min
            state.budget_max = new_max

            if changed and QuestionField.BUDGET.value in state.confirmed_fields:
                # Customer changed budget — note the change in uncertain_fields
                # but keep it confirmed (the new explicit value is still authoritative)
                if QuestionField.BUDGET.value not in state.uncertain_fields:
                    state.uncertain_fields.append(QuestionField.BUDGET.value)
                # Keep in confirmed — the customer stated a new explicit amount
            else:
                # New extraction or same value — confirm directly
                if QuestionField.BUDGET.value not in state.confirmed_fields:
                    state.confirmed_fields.append(QuestionField.BUDGET.value)
                if QuestionField.BUDGET.value in state.uncertain_fields:
                    state.uncertain_fields.remove(QuestionField.BUDGET.value)

        # Location
        locs = _extract_locations(transcript, self._domain.valid_locations)
        if locs:
            for loc in locs:
                if loc not in state.location:
                    state.location.append(loc)
            if QuestionField.LOCATION.value not in state.confirmed_fields:
                state.confirmed_fields.append(QuestionField.LOCATION.value)

        # Property type
        ptypes = _extract_property_types(transcript, self._domain.valid_property_types)
        if ptypes:
            state.property_type = ptypes
            if QuestionField.PROPERTY_TYPE.value not in state.confirmed_fields:
                state.confirmed_fields.append(QuestionField.PROPERTY_TYPE.value)

        # Purpose
        purpose = _keyword_match(transcript, _PURPOSE_KEYWORDS)
        if purpose is not None:
            state.purpose = purpose
            if QuestionField.PURPOSE.value not in state.confirmed_fields:
                state.confirmed_fields.append(QuestionField.PURPOSE.value)

        # Timeline
        timeline = _extract_timeline(transcript, self._domain)
        if timeline is not None:
            state.timeline = timeline
            if QuestionField.TIMELINE.value not in state.confirmed_fields:
                state.confirmed_fields.append(QuestionField.TIMELINE.value)

        # Decision maker
        dm = _keyword_match(transcript, _DECISION_MAKER_KEYWORDS)
        if dm is not None:
            state.decision_maker = dm
            if QuestionField.DECISION_MAKER.value not in state.confirmed_fields:
                state.confirmed_fields.append(QuestionField.DECISION_MAKER.value)

        # Financing
        financing = _keyword_match(transcript, _FINANCING_KEYWORDS)
        if financing is not None:
            state.financing_status = financing
            if QuestionField.FINANCING.value not in state.confirmed_fields:
                state.confirmed_fields.append(QuestionField.FINANCING.value)

        # Site visit interest
        svi = _keyword_match(transcript, _SITE_VISIT_KEYWORDS)
        if svi is not None:
            state.site_visit_interest = svi
            if QuestionField.SITE_VISIT_INTEREST.value not in state.confirmed_fields:
                state.confirmed_fields.append(QuestionField.SITE_VISIT_INTEREST.value)

    def _update_lead_stage(self, state: SalesState, conversation_state: str) -> None:
        """Map CIL conversation state → LeadStage."""
        mapping = self._domain.lead_stage_from_conversation_state
        mapped = mapping.get(conversation_state)
        if mapped is not None:
            # Only advance stage, never regress (except to DISQUALIFIED)
            stage_order = list(LeadStage)
            current_idx = stage_order.index(state.lead_stage)
            mapped_idx = stage_order.index(mapped)
            if mapped == LeadStage.DISQUALIFIED or mapped_idx > current_idx:
                state.lead_stage = mapped
        else:
            # Unknown state → move to ENGAGED if still at NEW
            if state.lead_stage == LeadStage.NEW:
                state.lead_stage = LeadStage.ENGAGED

    def _update_lead_intent(self, state: SalesState, intent: IntentResult) -> None:
        """Derive LeadIntent from CIL IntentLabel."""
        label = intent.label
        if label in (IntentLabel.PROMISE_TO_PAY, IntentLabel.PAYMENT, IntentLabel.CONSENT_GRANT):
            state.lead_intent = LeadIntent.HIGH
        elif label in (IntentLabel.CALLBACK, IntentLabel.IDENTITY_VERIFY):
            state.lead_intent = LeadIntent.MEDIUM
        elif label in (IntentLabel.DISCONNECT, IntentLabel.CONSENT_REVOKE):
            state.lead_intent = LeadIntent.LOW
        elif label in (IntentLabel.ABUSE,):
            state.lead_intent = LeadIntent.LOW
        elif label in (IntentLabel.OTHER, IntentLabel.SILENCE):
            # Keep existing intent — silence or unclear doesn't reset it
            pass
        else:
            # DISPUTE / HARDSHIP / UNAVAILABLE → keep existing, engagement still present
            if state.lead_intent == LeadIntent.UNKNOWN:
                state.lead_intent = LeadIntent.MEDIUM

    def _update_objections(
        self,
        state: SalesState,
        intent: IntentResult,
        risk: RiskAssessment,
    ) -> None:
        """Track objection signals from intent and risk flags."""
        from src.engines.risk.flags import RiskFlag

        objection_label = None
        if intent.label == IntentLabel.DISPUTE:
            objection_label = "PRICE_OBJECTION"
        elif intent.label == IntentLabel.HARDSHIP:
            objection_label = "BUDGET_CONCERN"
        elif RiskFlag.DISPUTE_CLAIM in risk.flags:
            objection_label = "DISPUTE"
        elif RiskFlag.HARDSHIP_INDICATOR in risk.flags:
            objection_label = "HARDSHIP"

        if objection_label and objection_label not in state.objections:
            state.objections.append(objection_label)

        state.objection_count = len(state.objections)

    def _update_field_tracking(self, state: SalesState) -> None:
        """Update unanswered_required_fields from confirmed set."""
        state.unanswered_required_fields = [
            f.value
            for f in self._domain.required_fields
            if f.value not in state.confirmed_fields
        ]

    def _update_qualification_score(self, state: SalesState) -> None:
        """Recalculate the heuristic qualification score."""
        score, reasons = _compute_qualification_score(state, self._domain)
        state.qualification_score = score
        state.score_reasons = reasons

        # Progress = fraction of required fields confirmed
        required_count = len(self._domain.required_fields)
        confirmed_required = sum(
            1 for f in self._domain.required_fields if f.value in state.confirmed_fields
        )
        state.qualification_progress = confirmed_required / required_count if required_count else 0.0

    def _update_lead_temperature(self, state: SalesState) -> None:
        """Derive LeadTemperature from intent + timeline + score."""
        if state.lead_intent == LeadIntent.LOW:
            state.lead_temperature = LeadTemperature.DISQUALIFIED
            return

        if (
            state.lead_intent == LeadIntent.HIGH
            and state.timeline in (Timeline.IMMEDIATE, Timeline.MONTHS_1_2)
            and state.qualification_score >= 50
        ):
            state.lead_temperature = LeadTemperature.HOT
        elif state.qualification_score >= 30 or state.lead_intent == LeadIntent.MEDIUM:
            state.lead_temperature = LeadTemperature.WARM
        else:
            state.lead_temperature = LeadTemperature.COLD

    def _update_qualification_status(self, state: SalesState) -> None:
        """Derive QualificationStatus from progress."""
        if state.lead_temperature == LeadTemperature.DISQUALIFIED:
            state.qualification_status = QualificationStatus.DISQUALIFIED
        elif state.qualification_progress >= 1.0:
            state.qualification_status = QualificationStatus.FULLY_QUALIFIED
        elif state.qualification_progress > 0.5:
            state.qualification_status = QualificationStatus.PARTIALLY_QUALIFIED
        elif state.qualification_progress > 0.0:
            state.qualification_status = QualificationStatus.IN_PROGRESS
        else:
            state.qualification_status = QualificationStatus.UNSTARTED

    def _update_current_objective(
        self,
        state: SalesState,
        intent: IntentResult,
        risk: RiskAssessment,
        strategy: StrategySelection,
    ) -> None:
        """Set current_objective based on state and signals."""
        from src.engines.risk.flags import RiskFlag
        from src.engines.strategy.actions import StrategyAction

        if risk.escalation_required or RiskFlag.ABUSE_DETECTED in risk.flags:
            state.current_objective = SalesObjective.ESCALATE
        elif intent.label in (IntentLabel.DISPUTE, IntentLabel.HARDSHIP):
            state.current_objective = SalesObjective.HANDLE_OBJECTION
        elif strategy.action == StrategyAction.ESCALATE:
            state.current_objective = SalesObjective.ESCALATE
        elif state.qualification_status == QualificationStatus.FULLY_QUALIFIED:
            if state.site_visit_interest in (SiteVisitInterest.CONFIRMED,):
                state.current_objective = SalesObjective.CONFIRM_SITE_VISIT
            else:
                state.current_objective = SalesObjective.SCHEDULE_SITE_VISIT
        elif state.lead_temperature == LeadTemperature.DISQUALIFIED:
            state.current_objective = SalesObjective.NURTURE
        elif state.qualification_progress > 0.0:
            state.current_objective = SalesObjective.QUALIFY
        else:
            state.current_objective = SalesObjective.DISCOVER
