"""GovernanceVerdict / GovernanceStatus — re-exported from the contracts layer.

``GovernanceVerdict``/``GovernanceStatus`` were defined in Sprint-001
(``src/libs/contracts/decision.py``) explicitly for this sprint to
implement against — they are not redefined here, only re-exported, so every
consumer (``DecisionEnvelope``, ``GovernanceLayer``, tests) shares one type.

Architecture: V4 Ch3 (AI Governance).
"""

from __future__ import annotations

from src.libs.contracts.decision import GovernanceStatus, GovernanceVerdict

__all__ = ["GovernanceStatus", "GovernanceVerdict"]

SAFE_FALLBACK_RESPONSE = "Ek pal ke liye ruk jaiye, main is jaankari ki dobara pushti kar raha hoon."
"""Used in place of a BLOCKed clause — never invents new content of its own."""
