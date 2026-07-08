"""NegotiationEngine — bounded offer computation for VoiceOS.

Architecture: V2 Ch5.
"""

from .engine import NegotiationBoundaryViolationError, NegotiationEngine, NegotiationResult
from .envelope import NegotiationEnvelope
from .moves import NegotiationMove

__all__ = [
    "NegotiationBoundaryViolationError",
    "NegotiationEngine",
    "NegotiationEnvelope",
    "NegotiationMove",
    "NegotiationResult",
]
