"""post_call_summary — pure serialization of SalesState to structured JSON.

No DB, no API, no LLM. Just converts SalesState to a clean dict suitable
for downstream CRM injection, analytics, or human review.

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

from .schema import SalesState


def generate_post_call_summary(sales_state: SalesState) -> dict:
    """Serialize SalesState to a clean post-call summary dict.

    Returns a structured dict suitable for CRM ingestion, analytics, or
    supervisor review. No side effects.

    Args:
        sales_state: The final SalesState at call end.

    Returns:
        Dict with sections: lead, requirements, conversation_quality, score.
    """
    requirements: dict = {
        "budget": {
            "min_rupees": sales_state.budget_min,
            "max_rupees": sales_state.budget_max,
            "currency": sales_state.currency,
        },
        "locations": sales_state.location,
        "property_types": sales_state.property_type,
        "bedrooms": sales_state.bedrooms,
        "purpose": sales_state.purpose.value if sales_state.purpose else None,
        "timeline": sales_state.timeline.value if sales_state.timeline else None,
        "decision_maker": sales_state.decision_maker.value if sales_state.decision_maker else None,
        "financing_status": (
            sales_state.financing_status.value if sales_state.financing_status else None
        ),
        "possession_requirement": sales_state.possession_requirement,
        "preferred_project": sales_state.preferred_project,
        "preferred_localities": sales_state.preferred_locality,
        "site_visit_interest": (
            sales_state.site_visit_interest.value if sales_state.site_visit_interest else None
        ),
        "competitor_consideration": sales_state.competitor_consideration,
    }

    lead: dict = {
        "stage": sales_state.lead_stage.value,
        "intent": sales_state.lead_intent.value,
        "temperature": sales_state.lead_temperature.value,
        "qualification_status": sales_state.qualification_status.value,
        "qualification_score": sales_state.qualification_score,
        "qualification_progress_pct": round(sales_state.qualification_progress * 100, 1),
    }

    conversation_quality: dict = {
        "objections": sales_state.objections,
        "objection_count": sales_state.objection_count,
        "confirmed_fields": sales_state.confirmed_fields,
        "uncertain_fields": sales_state.uncertain_fields,
        "unanswered_required_fields": sales_state.unanswered_required_fields,
        "final_action": sales_state.next_action.value,
    }

    score: dict = {
        "score": sales_state.qualification_score,
        "confidence": round(sales_state.confidence, 3),
        "reasons": sales_state.score_reasons,
    }

    return {
        "lead": lead,
        "requirements": requirements,
        "conversation_quality": conversation_quality,
        "score": score,
    }
