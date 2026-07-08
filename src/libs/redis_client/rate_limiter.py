"""RateLimiter — sliding-window rate limiting on Redis sorted sets.

Implements V3 Ch4 §4.12's rate-limiting algorithm ("token-bucket via atomic
Lua scripts") using a sliding-window log: each request is a sorted-set
member scored by its timestamp; entries older than the window are pruned
before counting, and the whole check-and-increment happens atomically in
one Lua script to avoid a race between concurrent callers.

Architecture: V3 Ch4 §4.7 (rate_limit), §4.12, §4.13 (ingress_per_tenant_rps).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

_KEY_PREFIX = "voiceos:ratelimit:"

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window)
    return {1, count + 1}
else
    return {0, count}
end
"""


def _fake_sliding_window_handler(client: Any, keys: list[str], args: list[str]) -> list[int]:
    """FakeRedisClient emulation of ``_SLIDING_WINDOW_SCRIPT``."""
    key = keys[0]
    now = float(args[0])
    window = float(args[1])
    limit = int(float(args[2]))
    member = args[3]
    client.zremrangebyscore(key, float("-inf"), now - window)
    count = client.zcard(key)
    if count < limit:
        client.zadd(key, {member: now})
        client.expire(key, int(window))
        return [1, count + 1]
    return [0, count]


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a single rate-limit check."""

    allowed: bool
    remaining: int
    retry_after_ms: int


class RateLimiter:
    """Sliding-window rate limiter, per-tenant and per-user (V3 Ch4).

    Usage:
        limiter = RateLimiter(redis_client)
        result = limiter.check(f"tenant:{tenant_id}", limit=200, window_seconds=1)
        if not result.allowed:
            ...  # reject / backpressure, retry after result.retry_after_ms
    """

    def __init__(self, redis: Any) -> None:
        """
        Args:
            redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``).
        """
        self._redis = redis
        register = getattr(redis, "register_recognized_script", None)
        if callable(register):
            register(_SLIDING_WINDOW_SCRIPT, _fake_sliding_window_handler)

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Check (and, if allowed, consume) one unit of ``key``'s rate budget.

        Args:
            key: Rate-limit scope, e.g. ``f"tenant:{tenant_id}"`` or
                 ``f"tenant:{tenant_id}:user:{user_id}"``.
            limit: Maximum requests allowed within the window.
            window_seconds: Sliding window duration in seconds.

        Returns:
            RateLimitResult with whether this request is allowed, how many
            requests remain in the current window, and (if blocked) how long
            until the oldest entry ages out of the window.
        """
        zkey = f"{_KEY_PREFIX}{key}"
        now = time.time()
        member = f"{now}:{uuid.uuid4()}"

        allowed_flag, count_after = self._redis.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            zkey,
            str(now),
            str(window_seconds),
            str(limit),
            member,
        )
        allowed = bool(int(allowed_flag))
        remaining = max(limit - int(count_after), 0)

        retry_after_ms = 0
        if not allowed:
            retry_after_ms = self._retry_after_ms(zkey, now, window_seconds)

        return RateLimitResult(allowed=allowed, remaining=remaining, retry_after_ms=retry_after_ms)

    def _retry_after_ms(self, zkey: str, now: float, window_seconds: int) -> int:
        oldest = self._redis.zrange(zkey, 0, 0, withscores=True)
        if not oldest:
            return 0
        _member, oldest_score = oldest[0]
        retry_after_s = max(0.0, (float(oldest_score) + window_seconds) - now)
        return int(retry_after_s * 1000)
