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

SAFE_FALLBACK_RESPONSE = "Ek pal ke liye ruk jaiye, main is jaankari ki dobara pushti kar rahi hoon."
"""Used in place of a BLOCKed clause — never invents new content of its own.

Feminine grammar ("kar rahi hoon", not "kar raha hoon"): this constant is
spoken by the Kavya persona (V2 Ch13, a female agent) whenever it's
substituted for LLM/governance-rejected content, so it must comply with
the same persona rules RegisterGuard enforces on every other reply — found
via Path-A Call-002 readiness validation (scripts/path_a_llm_fallback_
validation.py): RegisterGuard correctly rejected a real LLM-generated
reply and substituted this constant, which was itself grammatically
masculine (a pre-Kavya-persona Sprint-018 default nothing had corrected
since). Fixed here rather than special-cased in RegisterGuard — the
constant is the single source of truth for "safe" text; it should not
need special-casing to comply with the same rules it triggers."""
