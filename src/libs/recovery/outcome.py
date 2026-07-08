"""RecoveryOutcome — the uniform result type every recovery strategy returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RecoveryOutcome:
    """Result of a single recovery strategy execution."""

    success: bool
    detail: dict[str, Any] = field(default_factory=dict)
