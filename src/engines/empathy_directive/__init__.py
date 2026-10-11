"""EmpathyDirectiveComposer — lexical, turn-scoped empathy layer for the
scripted-response golden path.

Additive to src.engines.empathy.EmpathyPlanner, not a replacement — see
src/engines/empathy_directive/engine.py for how the two coexist.

Architecture: V2 Ch14 (EmpathyPlanner); V1 Ch20 (AdaptiveProsodyEngine).
"""

from __future__ import annotations

from src.engines.empathy_directive.directive import EmpathyDirective, EmpathyState
from src.engines.empathy_directive.engine import EmpathyDirectiveComposer, EmpathyStateClassifier

__all__ = [
    "EmpathyDirective",
    "EmpathyDirectiveComposer",
    "EmpathyState",
    "EmpathyStateClassifier",
]
