"""DialerQueue — priority queue backed by a Redis sorted set.

Score = -lead_score so ZPOPMIN returns the highest-priority lead first.
Key pattern: ``dialer:queue:{tenant_id}:{campaign_id}``.

Architecture: V5 Ch6 (Campaign Engine — Dialer, ADR-005 §15).
"""

from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger("voiceos.dialer.queue")

_KEY_TTL_SECONDS = 86_400 * 2  # 48 h — cleaned up when session ends anyway


class DialerQueue:
    """Redis sorted set queue for dialer lead prioritisation.

    Args:
        redis: Raw ``redis.Redis`` client (sync, used in a thread-pool or
               via ``asyncio.to_thread`` from the DialerEngine).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(tenant_id: str, campaign_id: str) -> str:
        return f"dialer:queue:{tenant_id}:{campaign_id}"

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def push_leads(
        self,
        tenant_id: str,
        campaign_id: str,
        leads: list[dict[str, Any]],
    ) -> int:
        """Push leads into the queue. Skips duplicates (NX semantics).

        Each lead dict must contain at minimum: lead_id, phone, score.
        Extra keys (name, pipeline_id, …) are preserved for the engine.

        Returns the number of leads actually added.
        """
        if not leads:
            return 0

        key = self._key(tenant_id, campaign_id)
        mapping: dict[str, float] = {}
        for lead in leads:
            member = json.dumps({
                "lead_id": lead["lead_id"],
                "phone": lead["phone"],
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "name": lead.get("name", ""),
                "pipeline_id": lead.get("pipeline_id"),
            }, ensure_ascii=False)
            # Negative score → ZPOPMIN gives highest priority first
            mapping[member] = -float(lead.get("score", 0))

        added = self._redis.zadd(key, mapping, nx=True)
        self._redis.expire(key, _KEY_TTL_SECONDS)
        _log.debug("queue push tenant=%s campaign=%s added=%d", tenant_id, campaign_id, added)
        return added

    # ------------------------------------------------------------------
    # Read / pop path
    # ------------------------------------------------------------------

    def pop_next(self, tenant_id: str, campaign_id: str) -> dict[str, Any] | None:
        """Atomically pop and return the highest-priority lead, or None."""
        key = self._key(tenant_id, campaign_id)
        result = self._redis.zpopmin(key, 1)
        if not result:
            return None
        member_bytes, _score = result[0]
        member = member_bytes.decode() if isinstance(member_bytes, bytes) else member_bytes
        return json.loads(member)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def size(self, tenant_id: str, campaign_id: str) -> int:
        return self._redis.zcard(self._key(tenant_id, campaign_id))

    def is_empty(self, tenant_id: str, campaign_id: str) -> bool:
        return self.size(tenant_id, campaign_id) == 0

    def clear(self, tenant_id: str, campaign_id: str) -> None:
        self._redis.delete(self._key(tenant_id, campaign_id))
        _log.info("queue cleared tenant=%s campaign=%s", tenant_id, campaign_id)
