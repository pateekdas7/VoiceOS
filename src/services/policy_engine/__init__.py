"""PolicyEngine — the enterprise Policy Decision Point (PDP) for VoiceOS.

Governs authorization, RBI/DPDP regulatory compliance, AI governance
(Law of Authority), and conversational rules through one unified
PERMIT/DENY/REQUIRE/FORBID DSL.

Architecture: V4 Ch4 (Policy Engine Architecture).
"""

from __future__ import annotations

from .break_glass import BreakGlassDirective, BreakGlassPolicy
from .decision import PolicyDecision, PolicyOutcome
from .engine import PolicyEngine
from .inheritance import PolicyInheritance
from .policy_set import PolicySet
from .rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule
from .service import PolicyEngineService

__all__ = [
    "BreakGlassDirective",
    "BreakGlassPolicy",
    "PolicyCondition",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyEngine",
    "PolicyEngineService",
    "PolicyInheritance",
    "PolicyOutcome",
    "PolicyRequest",
    "PolicyRule",
    "PolicySet",
]
