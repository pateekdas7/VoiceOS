"""SalesState schema — Phase 2 Sales Intelligence Layer.

All fields are nullable/unknown by default. The SalesStateUpdater populates
them incrementally from CIL outputs. No field is ever invented — all values
originate from customer utterances (via EntityExtractor) or CIL signals.

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class LeadStage(StrEnum):
    """Where the lead is in the funnel."""

    NEW = "NEW"
    ENGAGED = "ENGAGED"
    QUALIFYING = "QUALIFYING"
    QUALIFIED = "QUALIFIED"
    SITE_VISIT_SCHEDULED = "SITE_VISIT_SCHEDULED"
    NEGOTIATING = "NEGOTIATING"
    CONVERTED = "CONVERTED"
    NURTURING = "NURTURING"
    DISQUALIFIED = "DISQUALIFIED"


class LeadIntent(StrEnum):
    """Customer's apparent purchase intent."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class LeadTemperature(StrEnum):
    """Combined urgency+intent signal."""

    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"
    DISQUALIFIED = "DISQUALIFIED"


class QualificationStatus(StrEnum):
    """Whether the lead has enough info to be acted on."""

    UNSTARTED = "UNSTARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PARTIALLY_QUALIFIED = "PARTIALLY_QUALIFIED"
    FULLY_QUALIFIED = "FULLY_QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"


class SalesObjective(StrEnum):
    """High-level sales objective for this turn."""

    DISCOVER = "DISCOVER"
    QUALIFY = "QUALIFY"
    PITCH = "PITCH"
    HANDLE_OBJECTION = "HANDLE_OBJECTION"
    SCHEDULE_SITE_VISIT = "SCHEDULE_SITE_VISIT"
    CONFIRM_SITE_VISIT = "CONFIRM_SITE_VISIT"
    NURTURE = "NURTURE"
    CLOSE = "CLOSE"
    ESCALATE = "ESCALATE"


class SalesAction(StrEnum):
    """The single most appropriate action for this turn."""

    GREET = "GREET"
    DISCOVER = "DISCOVER"
    QUALIFY = "QUALIFY"
    ASK_PURPOSE = "ASK_PURPOSE"
    ASK_LOCATION = "ASK_LOCATION"
    ASK_PROPERTY_TYPE = "ASK_PROPERTY_TYPE"
    ASK_BUDGET = "ASK_BUDGET"
    ASK_TIMELINE = "ASK_TIMELINE"
    ASK_DECISION_MAKER = "ASK_DECISION_MAKER"
    ASK_FINANCING = "ASK_FINANCING"
    OFFER_SITE_VISIT = "OFFER_SITE_VISIT"
    CONFIRM_SITE_VISIT = "CONFIRM_SITE_VISIT"
    HANDLE_OBJECTION = "HANDLE_OBJECTION"
    CONFIRM_REQUIREMENT = "CONFIRM_REQUIREMENT"
    SCHEDULE_FOLLOWUP = "SCHEDULE_FOLLOWUP"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    END_CONVERSATION = "END_CONVERSATION"
    NURTURE = "NURTURE"


class QuestionField(StrEnum):
    """Which qualification field to ask for next."""

    PURPOSE = "PURPOSE"
    LOCATION = "LOCATION"
    PROPERTY_TYPE = "PROPERTY_TYPE"
    BUDGET = "BUDGET"
    TIMELINE = "TIMELINE"
    DECISION_MAKER = "DECISION_MAKER"
    FINANCING = "FINANCING"
    PREFERRED_LOCALITY = "PREFERRED_LOCALITY"
    SITE_VISIT_INTEREST = "SITE_VISIT_INTEREST"
    COMPETITOR_CONSIDERATION = "COMPETITOR_CONSIDERATION"


class PropertyPurpose(StrEnum):
    """Why the customer wants to buy."""

    SELF_USE = "SELF_USE"
    INVESTMENT = "INVESTMENT"
    UNKNOWN = "UNKNOWN"


class Timeline(StrEnum):
    """Purchase urgency."""

    IMMEDIATE = "IMMEDIATE"
    MONTHS_1_2 = "MONTHS_1_2"
    MONTHS_3_6 = "MONTHS_3_6"
    EXPLORING = "EXPLORING"


class DecisionMaker(StrEnum):
    """Who makes the final call."""

    SELF = "SELF"
    SPOUSE = "SPOUSE"
    FAMILY = "FAMILY"
    INVESTOR = "INVESTOR"
    UNKNOWN = "UNKNOWN"


class FinancingStatus(StrEnum):
    """How the customer plans to pay."""

    CASH = "CASH"
    HOME_LOAN = "HOME_LOAN"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class SiteVisitInterest(StrEnum):
    """Customer's appetite for a site visit."""

    INTERESTED = "INTERESTED"
    MAYBE = "MAYBE"
    CONFIRMED = "CONFIRMED"
    NOT_INTERESTED = "NOT_INTERESTED"


# ---------------------------------------------------------------------------
# SalesState dataclass
# ---------------------------------------------------------------------------


@dataclass
class SalesState:
    """Per-call Sales Intelligence state.

    Updated incrementally by SalesStateUpdater on every turn. All customer
    requirement fields start as None/unknown — they are populated only from
    confirmed customer utterances, never invented.

    Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
    """

    # Lead funnel
    lead_stage: LeadStage = LeadStage.NEW
    lead_intent: LeadIntent = LeadIntent.UNKNOWN
    lead_temperature: LeadTemperature = LeadTemperature.COLD
    qualification_status: QualificationStatus = QualificationStatus.UNSTARTED
    qualification_score: int = 0  # 0-100 heuristic, NOT a probability
    qualification_progress: float = 0.0  # fraction of required fields known

    # Current sales objective
    current_objective: SalesObjective = SalesObjective.DISCOVER
    next_action: SalesAction = SalesAction.GREET
    next_question: QuestionField | None = None  # field to ask this turn, or None

    # Customer requirements — ALL nullable (never invent values)
    budget_min: int | None = None  # rupees
    budget_max: int | None = None  # rupees
    currency: str = "INR"
    location: list[str] = field(default_factory=list)  # multiple acceptable
    property_type: list[str] | None = None  # ["2BHK", "3BHK"] etc.
    bedrooms: int | None = None
    purpose: PropertyPurpose | None = None
    timeline: Timeline | None = None
    decision_maker: DecisionMaker | None = None
    financing_status: FinancingStatus | None = None
    possession_requirement: str | None = None
    preferred_project: str | None = None
    preferred_locality: list[str] = field(default_factory=list)
    site_visit_interest: SiteVisitInterest | None = None
    competitor_consideration: bool | None = None

    # Conversation quality tracking
    objections: list[str] = field(default_factory=list)  # objection labels seen
    objection_count: int = 0
    confirmed_fields: list[str] = field(default_factory=list)  # high-confidence
    uncertain_fields: list[str] = field(default_factory=list)  # mentioned but unconfirmed
    unanswered_required_fields: list[str] = field(default_factory=list)

    # Callback scheduling
    requested_callback_time: datetime | None = None
    """Customer-requested callback datetime (populated from DATE entity when
    CALLBACK intent is detected). Asia/Kolkata timezone. None when the customer
    requested a callback but did not specify a time, or when no callback was
    requested this call.

    Phase 3: used by SalesProductionActionDispatcher to invoke CallbackScheduler.
    """

    # History
    last_sales_action: SalesAction | None = None
    previous_sales_action: SalesAction | None = None

    # Explainability
    confidence: float = 0.0  # 0.0-1.0
    score_reasons: list[str] = field(default_factory=list)  # why the score is what it is

    def to_dict(self) -> dict:
        """Serialize to a plain dict for Redis/JSON storage."""
        return {
            "lead_stage": self.lead_stage.value,
            "lead_intent": self.lead_intent.value,
            "lead_temperature": self.lead_temperature.value,
            "qualification_status": self.qualification_status.value,
            "qualification_score": self.qualification_score,
            "qualification_progress": self.qualification_progress,
            "current_objective": self.current_objective.value,
            "next_action": self.next_action.value,
            "next_question": self.next_question.value if self.next_question else None,
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "currency": self.currency,
            "location": self.location,
            "property_type": self.property_type,
            "bedrooms": self.bedrooms,
            "purpose": self.purpose.value if self.purpose else None,
            "timeline": self.timeline.value if self.timeline else None,
            "decision_maker": self.decision_maker.value if self.decision_maker else None,
            "financing_status": self.financing_status.value if self.financing_status else None,
            "possession_requirement": self.possession_requirement,
            "preferred_project": self.preferred_project,
            "preferred_locality": self.preferred_locality,
            "site_visit_interest": self.site_visit_interest.value if self.site_visit_interest else None,
            "competitor_consideration": self.competitor_consideration,
            "requested_callback_time": (
                self.requested_callback_time.isoformat()
                if self.requested_callback_time is not None
                else None
            ),
            "objections": self.objections,
            "objection_count": self.objection_count,
            "confirmed_fields": self.confirmed_fields,
            "uncertain_fields": self.uncertain_fields,
            "unanswered_required_fields": self.unanswered_required_fields,
            "last_sales_action": self.last_sales_action.value if self.last_sales_action else None,
            "previous_sales_action": self.previous_sales_action.value if self.previous_sales_action else None,
            "confidence": self.confidence,
            "score_reasons": self.score_reasons,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SalesState":
        """Deserialize from a plain dict (inverse of to_dict)."""
        s = cls()
        s.lead_stage = LeadStage(data.get("lead_stage", LeadStage.NEW))
        s.lead_intent = LeadIntent(data.get("lead_intent", LeadIntent.UNKNOWN))
        s.lead_temperature = LeadTemperature(data.get("lead_temperature", LeadTemperature.COLD))
        s.qualification_status = QualificationStatus(
            data.get("qualification_status", QualificationStatus.UNSTARTED)
        )
        s.qualification_score = data.get("qualification_score", 0)
        s.qualification_progress = data.get("qualification_progress", 0.0)
        s.current_objective = SalesObjective(data.get("current_objective", SalesObjective.DISCOVER))
        s.next_action = SalesAction(data.get("next_action", SalesAction.GREET))
        nq = data.get("next_question")
        s.next_question = QuestionField(nq) if nq else None
        s.budget_min = data.get("budget_min")
        s.budget_max = data.get("budget_max")
        s.currency = data.get("currency", "INR")
        s.location = data.get("location", [])
        s.property_type = data.get("property_type")
        s.bedrooms = data.get("bedrooms")
        purpose = data.get("purpose")
        s.purpose = PropertyPurpose(purpose) if purpose else None
        timeline = data.get("timeline")
        s.timeline = Timeline(timeline) if timeline else None
        dm = data.get("decision_maker")
        s.decision_maker = DecisionMaker(dm) if dm else None
        fs = data.get("financing_status")
        s.financing_status = FinancingStatus(fs) if fs else None
        s.possession_requirement = data.get("possession_requirement")
        s.preferred_project = data.get("preferred_project")
        s.preferred_locality = data.get("preferred_locality", [])
        svi = data.get("site_visit_interest")
        s.site_visit_interest = SiteVisitInterest(svi) if svi else None
        s.competitor_consideration = data.get("competitor_consideration")
        _rct = data.get("requested_callback_time")
        if _rct:
            try:
                s.requested_callback_time = datetime.fromisoformat(_rct)
            except (ValueError, TypeError):
                s.requested_callback_time = None
        else:
            s.requested_callback_time = None
        s.objections = data.get("objections", [])
        s.objection_count = data.get("objection_count", 0)
        s.confirmed_fields = data.get("confirmed_fields", [])
        s.uncertain_fields = data.get("uncertain_fields", [])
        s.unanswered_required_fields = data.get("unanswered_required_fields", [])
        lsa = data.get("last_sales_action")
        s.last_sales_action = SalesAction(lsa) if lsa else None
        psa = data.get("previous_sales_action")
        s.previous_sales_action = SalesAction(psa) if psa else None
        s.confidence = data.get("confidence", 0.0)
        s.score_reasons = data.get("score_reasons", [])
        return s
