"""FencingToken — rejects stale writes from a superseded lock holder (V3 Ch8).

Complements ``DistributedLock``'s fencing token (Sprint-013, V3 Ch4): the
lock guarantees only one owner holds the resource at a time, but a
network-delayed writer that acquired an *older* lock can still deliver its
write after a *newer* owner has already taken over. ``FencingToken`` is the
last-line check every authoritative write must pass: the token presented
must be >= the highest token already seen for that resource.
"""

from __future__ import annotations


class FencingToken:
    """Stateless fencing-token comparison."""

    @staticmethod
    def validate(token: int, last_seen: int) -> bool:
        """Return ``True`` if ``token`` is fresh (>= ``last_seen``), ``False`` if stale."""
        return token >= last_seen


class FencingTokenTracker:
    """Per-resource last-seen fencing token ledger.

    Wraps :meth:`FencingToken.validate` with per-``resource_id`` state so
    callers do not need to track ``last_seen`` themselves. A successful
    validation advances the tracked ``last_seen`` to ``token``.
    """

    def __init__(self) -> None:
        self._last_seen: dict[str, int] = {}

    def validate(self, resource_id: str, token: int) -> bool:
        """Validate ``token`` against the last-seen token for ``resource_id``.

        Args:
            resource_id: The authoritative resource being written to.
            token: The fencing token presented by the writer (typically
                ``LockToken.fencing_token`` from ``DistributedLock.acquire()``).

        Returns:
            ``True`` if the write may proceed (token accepted and recorded as
            the new last-seen value); ``False`` if the write is stale and
            must be rejected.
        """
        last_seen = self._last_seen.get(resource_id, 0)
        if not FencingToken.validate(token, last_seen):
            return False
        self._last_seen[resource_id] = token
        return True
