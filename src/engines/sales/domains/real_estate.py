"""RealEstateDomainConfig — Delhi-NCR real estate sales domain.

All values are DATA, not logic. The sales engines consume this configuration
without knowing anything about real estate specifically.

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engines.sales.schema import LeadStage, QuestionField, Timeline

from .base import DomainConfig


@dataclass
class RealEstateDomainConfig(DomainConfig):
    """Domain configuration for Delhi-NCR residential real estate.

    Fields ordered by qualification priority (most important first). The
    QuestionSelector uses this ordering to decide what to ask next.
    """

    # Required fields in priority order — PURPOSE first because it determines
    # investment vs self-use framing for the entire conversation.
    _required_fields: list[QuestionField] = field(
        default_factory=lambda: [
            QuestionField.PURPOSE,
            QuestionField.LOCATION,
            QuestionField.PROPERTY_TYPE,
            QuestionField.BUDGET,
            QuestionField.TIMELINE,
            QuestionField.DECISION_MAKER,
            QuestionField.FINANCING,
        ]
    )

    _optional_fields: list[QuestionField] = field(
        default_factory=lambda: [
            QuestionField.PREFERRED_LOCALITY,
            QuestionField.SITE_VISIT_INTEREST,
            QuestionField.COMPETITOR_CONSIDERATION,
        ]
    )

    # Dependencies: BUDGET can only be asked after LOCATION is confirmed
    # (budget is highly location-specific in NCR). FINANCING after BUDGET
    # (relevance depends on budget scale).
    _dependencies: dict[QuestionField, list[QuestionField]] = field(
        default_factory=lambda: {
            QuestionField.BUDGET: [QuestionField.LOCATION],
            QuestionField.FINANCING: [QuestionField.BUDGET],
            QuestionField.PREFERRED_LOCALITY: [QuestionField.LOCATION],
            QuestionField.SITE_VISIT_INTEREST: [QuestionField.PROPERTY_TYPE],
        }
    )

    # Budget label → (min_rupees, max_rupees)
    _budget_ranges: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {
            "₹40-60L": (4_000_000, 6_000_000),
            "₹60-80L": (6_000_000, 8_000_000),
            "₹80L-1Cr": (8_000_000, 10_000_000),
            "₹1-1.5Cr": (10_000_000, 15_000_000),
            "₹1.5-2Cr": (15_000_000, 20_000_000),
            "₹2Cr+": (20_000_000, 999_999_999),
        }
    )

    _valid_locations: list[str] = field(
        default_factory=lambda: [
            "Noida",
            "Noida Extension",
            "Greater Noida",
            "Ghaziabad",
            "Gurugram",
            "Gurgaon",
            "Dwarka",
            "Delhi",
            "Faridabad",
            "Indirapuram",
            "Vaishali",
            "Raj Nagar Extension",
        ]
    )

    _valid_property_types: list[str] = field(
        default_factory=lambda: ["1BHK", "2BHK", "3BHK", "4BHK", "Villa", "Plot", "Studio"]
    )

    # Keywords for Timeline extraction from Hinglish utterances.
    # Judgment call: "exploring" / "bas dekhna hai" maps to EXPLORING, not MONTHS_3_6,
    # because it signals low urgency more than a concrete timeframe.
    _timeline_keywords: dict[str, Timeline] = field(
        default_factory=lambda: {
            "immediately": Timeline.IMMEDIATE,
            "abhi": Timeline.IMMEDIATE,
            "jaldi": Timeline.IMMEDIATE,
            "urgent": Timeline.IMMEDIATE,
            "asap": Timeline.IMMEDIATE,
            "1-2 months": Timeline.MONTHS_1_2,
            "1-2 mahine": Timeline.MONTHS_1_2,
            "ek do mahine": Timeline.MONTHS_1_2,
            "do mahine": Timeline.MONTHS_1_2,
            "2 months": Timeline.MONTHS_1_2,
            "2 mahine": Timeline.MONTHS_1_2,
            "agle mahine": Timeline.MONTHS_1_2,
            "3-6 months": Timeline.MONTHS_3_6,
            "3-6 mahine": Timeline.MONTHS_3_6,
            "teen mahine": Timeline.MONTHS_3_6,
            "chhe mahine": Timeline.MONTHS_3_6,
            "6 months": Timeline.MONTHS_3_6,
            "exploring": Timeline.EXPLORING,
            "just looking": Timeline.EXPLORING,
            "bas dekhna": Timeline.EXPLORING,
            "soch rahe": Timeline.EXPLORING,
            "research": Timeline.EXPLORING,
        }
    )

    # Maps CIL ConversationState → LeadStage. States not listed default to ENGAGED.
    _lead_stage_map: dict[str, LeadStage] = field(
        default_factory=lambda: {
            "GREETING": LeadStage.NEW,
            "VERIFICATION": LeadStage.NEW,
            "DEBT_DISCUSSION": LeadStage.QUALIFYING,
            "NEGOTIATION": LeadStage.NEGOTIATING,
            "PROMISE_TO_PAY": LeadStage.CONVERTED,
            "DISPUTE_HANDLING": LeadStage.NURTURING,
            "HARDSHIP_HANDLING": LeadStage.NURTURING,
            "CLOSING": LeadStage.DISQUALIFIED,
            "POST_CALL": LeadStage.DISQUALIFIED,
        }
    )

    # Field weights for qualification score. PURPOSE and BUDGET are highest-value
    # because they determine product fit and financial viability.
    _field_weights: dict[QuestionField, int] = field(
        default_factory=lambda: {
            QuestionField.PURPOSE: 12,
            QuestionField.LOCATION: 15,
            QuestionField.PROPERTY_TYPE: 12,
            QuestionField.BUDGET: 15,
            QuestionField.TIMELINE: 12,
            QuestionField.DECISION_MAKER: 10,
            QuestionField.FINANCING: 10,
        }
    )

    @property
    def required_fields(self) -> list[QuestionField]:
        return self._required_fields

    @property
    def optional_fields(self) -> list[QuestionField]:
        return self._optional_fields

    @property
    def dependencies(self) -> dict[QuestionField, list[QuestionField]]:
        return self._dependencies

    @property
    def budget_ranges(self) -> dict[str, tuple[int, int]]:
        return self._budget_ranges

    @property
    def valid_locations(self) -> list[str]:
        return self._valid_locations

    @property
    def valid_property_types(self) -> list[str]:
        return self._valid_property_types

    @property
    def timeline_keywords(self) -> dict[str, Timeline]:
        return self._timeline_keywords

    @property
    def lead_stage_from_conversation_state(self) -> dict[str, LeadStage]:
        return self._lead_stage_map

    @property
    def field_weights(self) -> dict[QuestionField, int]:
        return self._field_weights
