"""post_call_summary — pure serialization of SalesState to structured JSON.

No DB, no API, no LLM. Just converts SalesState to a clean dict suitable
for downstream CRM injection, analytics, or human review.

Architecture: VoiceOS Phase 3 Production Action Layer.
"""

from __future__ import annotations

from .schema import LeadStage, SalesAction, SalesState


def _outcome_from_stage(lead_stage: LeadStage) -> str:
    """Derive a call outcome label from the final LeadStage."""
    _STAGE_OUTCOME: dict[LeadStage, str] = {
        LeadStage.NEW: "no_engagement",
        LeadStage.ENGAGED: "engaged",
        LeadStage.QUALIFYING: "qualifying",
        LeadStage.QUALIFIED: "qualified",
        LeadStage.SITE_VISIT_SCHEDULED: "site_visit_scheduled",
        LeadStage.NEGOTIATING: "negotiating",
        LeadStage.CONVERTED: "converted",
        LeadStage.NURTURING: "nurturing",
        LeadStage.DISQUALIFIED: "disqualified",
    }
    return _STAGE_OUTCOME.get(lead_stage, "unknown")


def _recommended_next_action(next_action: SalesAction) -> str:
    """Human-readable recommended next action for CRM agents."""
    _ACTION_LABEL: dict[SalesAction, str] = {
        SalesAction.GREET: "Initial contact — greet customer",
        SalesAction.DISCOVER: "Discover customer needs",
        SalesAction.QUALIFY: "Continue qualification",
        SalesAction.ASK_PURPOSE: "Ask about purchase purpose",
        SalesAction.ASK_LOCATION: "Ask about preferred locations",
        SalesAction.ASK_PROPERTY_TYPE: "Ask about property type preference",
        SalesAction.ASK_BUDGET: "Ask about budget range",
        SalesAction.ASK_TIMELINE: "Ask about purchase timeline",
        SalesAction.ASK_DECISION_MAKER: "Ask about decision maker",
        SalesAction.ASK_FINANCING: "Ask about financing status",
        SalesAction.OFFER_SITE_VISIT: "Offer a site visit",
        SalesAction.CONFIRM_SITE_VISIT: "Confirm site visit details",
        SalesAction.HANDLE_OBJECTION: "Address customer objections",
        SalesAction.CONFIRM_REQUIREMENT: "Confirm customer requirements",
        SalesAction.SCHEDULE_FOLLOWUP: "Schedule follow-up callback",
        SalesAction.HUMAN_HANDOFF: "Transfer to human specialist",
        SalesAction.END_CONVERSATION: "End conversation",
        SalesAction.NURTURE: "Nurture lead with relevant content",
    }
    return _ACTION_LABEL.get(next_action, str(next_action))


def generate_post_call_summary(
    sales_state: SalesState,
    call_id: str = "",
    customer_id: str = "",
    escalated: bool = False,
) -> dict:
    """Serialize SalesState to a clean post-call summary dict.

    Returns a structured dict suitable for CRM ingestion, analytics, or
    supervisor review. No side effects, no LLM calls. Deterministic from
    SalesState.

    Unknown/None values remain null. No values are invented.

    Args:
        sales_state: The final SalesState at call end.
        call_id: The call session identifier (for cross-referencing).
        customer_id: The authoritative customer identifier.
        escalated: Whether this call ended in a human handoff/escalation.

    Returns:
        Dict with sections: lead, requirements, conversation, sales, outcome,
        recommended_next_action (plus call_id, customer_id at root).
    """
    callback_requested = (
        sales_state.last_sales_action == SalesAction.SCHEDULE_FOLLOWUP
        or sales_state.next_action == SalesAction.SCHEDULE_FOLLOWUP
    )
    requested_callback_time_str: str | None = None
    if sales_state.requested_callback_time is not None:
        requested_callback_time_str = sales_state.requested_callback_time.isoformat()

    lead: dict = {
        "stage": sales_state.lead_stage.value,
        "intent": sales_state.lead_intent.value,
        "temperature": sales_state.lead_temperature.value,
        "qualification_score": sales_state.qualification_score,
        "qualification_status": sales_state.qualification_status.value,
    }

    requirements: dict = {
        "budget": (
            {
                "min_rupees": sales_state.budget_min,
                "max_rupees": sales_state.budget_max,
                "currency": sales_state.currency,
            }
            if sales_state.budget_min is not None or sales_state.budget_max is not None
            else None
        ),
        "locations": sales_state.location or None,
        "property_types": sales_state.property_type or None,
        "purpose": sales_state.purpose.value if sales_state.purpose else None,
        "timeline": sales_state.timeline.value if sales_state.timeline else None,
        "decision_maker": (
            sales_state.decision_maker.value if sales_state.decision_maker else None
        ),
        "financing_status": (
            sales_state.financing_status.value if sales_state.financing_status else None
        ),
        "preferred_localities": sales_state.preferred_locality or None,
        "site_visit_interest": (
            sales_state.site_visit_interest.value if sales_state.site_visit_interest else None
        ),
    }

    conversation: dict = {
        "objections": sales_state.objections or None,
        "confirmed_fields": sales_state.confirmed_fields or None,
        "uncertain_fields": sales_state.uncertain_fields or None,
        "escalated": escalated,
        "callback_requested": callback_requested,
        "requested_callback_time": requested_callback_time_str,
    }

    sales: dict = {
        "current_stage": sales_state.current_objective.value,
        "last_action": (
            sales_state.last_sales_action.value if sales_state.last_sales_action else None
        ),
        "next_action": sales_state.next_action.value,
        "next_question": (
            sales_state.next_question.value if sales_state.next_question else None
        ),
    }

    outcome = _outcome_from_stage(sales_state.lead_stage)
    recommended_next = _recommended_next_action(sales_state.next_action)

    return {
        "call_id": call_id,
        "customer_id": customer_id,
        "lead": lead,
        "requirements": requirements,
        "conversation": conversation,
        "sales": sales,
        "outcome": outcome,
        "recommended_next_action": recommended_next,
    }
