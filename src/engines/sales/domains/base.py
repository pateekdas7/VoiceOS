"""DomainConfig — abstract base for sales domain configuration.

All domain-specific data (required fields, question dependencies, valid values,
lead stage mappings) lives in a concrete DomainConfig subclass. The sales
engines (SalesStateUpdater, QuestionSelector, SalesActionPlanner) receive a
DomainConfig at construction time so they remain domain-agnostic.

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.engines.sales.schema import LeadStage, QuestionField, Timeline


class DomainConfig(ABC):
    """Abstract base for domain-specific sales configuration.

    Subclasses supply data, not logic. All fields should be plain Python data
    structures (lists, dicts) — no methods with business logic belong here.
    """

    @property
    @abstractmethod
    def required_fields(self) -> list[QuestionField]:
        """Required qualification fields in priority order (highest first)."""
        ...

    @property
    @abstractmethod
    def optional_fields(self) -> list[QuestionField]:
        """Optional qualification fields."""
        ...

    @property
    @abstractmethod
    def dependencies(self) -> dict[QuestionField, list[QuestionField]]:
        """Prerequisite field graph: {field → [must be confirmed before asking]}."""
        ...

    @property
    @abstractmethod
    def budget_ranges(self) -> dict[str, tuple[int, int]]:
        """Human-readable budget label → (min_rupees, max_rupees) pairs."""
        ...

    @property
    @abstractmethod
    def valid_locations(self) -> list[str]:
        """Accepted location names for this domain."""
        ...

    @property
    @abstractmethod
    def valid_property_types(self) -> list[str]:
        """Accepted property type strings (e.g. '2BHK', 'Villa')."""
        ...

    @property
    @abstractmethod
    def timeline_keywords(self) -> dict[str, Timeline]:
        """Keyword/phrase → Timeline enum mapping for extraction."""
        ...

    @property
    @abstractmethod
    def lead_stage_from_conversation_state(self) -> dict[str, LeadStage]:
        """ConversationState string → LeadStage mapping."""
        ...

    @property
    def field_weights(self) -> dict[QuestionField, int]:
        """Qualification score weight per field (default = 10 each).

        Override to give some fields higher weight in the heuristic score.
        """
        return {f: 10 for f in self.required_fields}
