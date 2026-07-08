"""IdempotencyGuard — exactly-once execution for authoritative effects (V3 Ch8).

Every authoritative write (PTP creation, consent change, decision-envelope
publish) must be guarded so a retried request never duplicates the effect.
``IdempotencyGuard.execute_once()`` is the mandatory entry point;
``IdempotencyKeyBuilder`` derives deterministic keys from call context;
``FencingToken`` rejects stale (out-of-order) writes.

Architecture: V3 Ch8 (Idempotency).
"""

from __future__ import annotations

from .fencing import FencingToken, FencingTokenTracker
from .guard import IdempotencyGuard
from .key_builder import IdempotencyKeyBuilder

__all__ = [
    "FencingToken",
    "FencingTokenTracker",
    "IdempotencyGuard",
    "IdempotencyKeyBuilder",
]
