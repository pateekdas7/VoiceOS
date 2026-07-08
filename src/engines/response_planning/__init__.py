"""Response Planning Engine — CIL pipeline orchestrator.

Assembles all perception and decision engine outputs into a sealed
ResponsePlan and DecisionEnvelope.

Architecture: V2 Ch1, V2 Ch15.
"""

from __future__ import annotations

from .engine import ResponsePlanningEngine

__all__ = ["ResponsePlanningEngine"]
