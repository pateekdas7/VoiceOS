"""ReasoningAdapter protocol (ADR-006 Sec 3.2/3.3).

The reasoning model is given the evidence bundle's ``verified_facts``
*already assembled by deterministic code* (``evidence_bundler.py``) -- it
never fetches its own telemetry and never adds a ``VerifiedFact`` of its
own. Its only outputs are ``hypotheses`` (explicitly labeled inference,
never presented as fact), a ``recommendation``, a ``confidence_level``, and
a human-readable ``narrative`` -- this is the structural enforcement of
"the LLM never invents facts" for the ops-intelligence domain (ADR-006
Sec 3.2.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.services.ops_intelligence.models import ConfidenceLevel, Hypothesis, InsightCategory, VerifiedFact


@dataclass(frozen=True)
class NarrationRequest:
    """Everything the reasoning model is allowed to see for one analysis pass.

    ``verified_facts`` is the complete, closed set of evidence -- the model
    reasons over exactly this and nothing else (no live tool calls back out
    to Prometheus/Loki/Jaeger from inside the model turn).
    """

    category: InsightCategory
    tenant_id: str | None
    verified_facts: tuple[VerifiedFact, ...]
    candidate_affected_components: tuple[str, ...]


@dataclass(frozen=True)
class NarrationResult:
    """The reasoning model's entire contribution to an Insight."""

    hypotheses: tuple[Hypothesis, ...]
    recommendation: str | None
    confidence_level: ConfidenceLevel
    narrative: str
    model: str
    prompt_version: str
    total_tokens: int = 0
    """Input + output tokens actually billed for this call (0 if unknown/failed) --
    feeds tenant-scoped usage metering (ADR-006 Sec 13.10)."""


class ReasoningAdapter(Protocol):
    """Replaceable reasoning-model adapter -- the only code path that calls an LLM in ``reasoning/``."""

    async def narrate(self, request: NarrationRequest) -> NarrationResult: ...


__all__ = ["NarrationRequest", "NarrationResult", "ReasoningAdapter"]
