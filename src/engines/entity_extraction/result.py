"""ExtractedValue and ExtractedEntities — entity extraction results.

Architecture: V2 Ch9.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .slots import EntityType


class ExtractedValue(BaseModel):
    """A single extracted entity slot value.

    Carries both the normalized value (e.g., '5000' for an amount)
    and the original surface form as it appeared in the transcript.
    """

    model_config = ConfigDict(frozen=True)

    entity_type: EntityType
    """The type of entity this value represents."""

    normalized: str
    """Normalized canonical value (e.g., '5000', '2026-06-30')."""

    surface_form: str = ""
    """Original text span from the transcript that produced this entity."""

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    """Extraction confidence [0.0, 1.0]. Rule-based extractors return 1.0."""


class ExtractedEntities(BaseModel):
    """The full set of entity slots extracted from one TurnInput.

    Architecture: V2 Ch9.
    """

    model_config = ConfigDict(frozen=True)

    slots: dict[str, ExtractedValue] = Field(default_factory=dict)
    """Extracted slots keyed by EntityType value string (e.g., 'AMOUNT')."""

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    """Overall extraction confidence for this turn."""

    def get(self, entity_type: EntityType) -> ExtractedValue | None:
        """Return the extracted value for an entity type, or None."""
        return self.slots.get(entity_type.value)
