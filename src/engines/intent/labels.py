"""Intent label re-exports for the Intent Engine.

IntentLabel and IntentSignal are the authoritative contract types defined
in Sprint-001 (src/libs/contracts/response_plan.py). This module re-exports
them so engine consumers can import from a single package entry point.

Architecture: V2 Ch3; DocSuite-03 (Data Dictionary — intent label definitions).
"""

from __future__ import annotations

from src.libs.contracts.response_plan import IntentLabel, IntentSignal

__all__ = ["IntentLabel", "IntentSignal"]
