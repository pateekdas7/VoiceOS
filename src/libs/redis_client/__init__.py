"""VoiceOS v2 — Redis client library.

Redis is the hot-state tier: recoverable, non-authoritative, TTL-disciplined
(V3 Ch4). This package provides the pooled connection wrapper, the
distributed lock with fencing tokens, the sliding-window rate limiter, and
the TTL enforcement guard that every Redis SET in the codebase must go
through.

Architecture: V3 Ch4 (Redis Architecture).
"""

from .client import RedisClient
from .health_check import RedisHealthCheck
from .lock import DistributedLock, LockToken
from .rate_limiter import RateLimiter, RateLimitResult
from .ttl_guard import MissingTTLError, TTLGuard

__all__ = [
    "DistributedLock",
    "LockToken",
    "MissingTTLError",
    "RateLimitResult",
    "RateLimiter",
    "RedisClient",
    "RedisHealthCheck",
    "TTLGuard",
]
