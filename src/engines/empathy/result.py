"""EmpathyPlanner result type.

Re-exports EmpathyConfig from the contracts layer as the canonical output
type of the EmpathyPlanner. The contracts-layer definition is frozen from
Sprint-001 and shared with AdaptiveProsodyEngine (Sprint-009).

Architecture: V2 Ch14; V1 Ch16; Sprint-001 contracts spec.
"""

from __future__ import annotations

from src.libs.contracts.streaming import EmpathyConfig

__all__ = ["EmpathyConfig"]
