"""IdempotencyKeyBuilder — deterministic idempotency keys from call context (V3 Ch8).

The same ``(call_id, turn_id, effect_name)`` triple always yields the same
key, so a retried request against the same authoritative effect collides
with the original in the ``idempotency_keys`` table instead of duplicating
the effect.
"""

from __future__ import annotations

from src.libs.contracts.primitives import CallId


class IdempotencyKeyBuilder:
    """Builds deterministic idempotency keys from call context."""

    @staticmethod
    def build(call_id: CallId, turn_id: str, effect_name: str) -> str:
        """Return the deterministic key for one authoritative effect.

        Args:
            call_id: The call the effect belongs to.
            turn_id: The turn within the call that triggered the effect.
            effect_name: Stable name of the effect (e.g. ``'ptp_create'``,
                ``'decision_envelope_publish'``).
        """
        return f"{call_id}:{turn_id}:{effect_name}"
