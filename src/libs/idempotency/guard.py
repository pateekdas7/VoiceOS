"""IdempotencyGuard — exactly-once execution for authoritative effects (V3 Ch8).

``execute_once()`` guarantees that a given ``(tenant_id, key)`` pair executes
its effect function at most once, even under concurrent callers, by using the
``idempotency_keys`` table's primary key as an atomic claim: exactly one
concurrent caller's claim insert succeeds (the winner, who executes the
effect and records the result); every other caller's claim insert is a
Postgres no-op (a loser, who polls for the winner's cached result instead of
re-executing).

NOTE on the spec's "wrapped in a single Postgres transaction" phrasing: a
literal single transaction spanning an arbitrary external ``effect_fn`` call
is not achievable (the transaction would have to stay open across whatever
I/O the effect performs). The claim-then-complete pattern implemented here is
the real-world equivalent — the claim step is genuinely atomic (unique
constraint), which is what the "10 concurrent calls -> 1 effect" acceptance
criterion requires.

Architecture: V3 Ch8 (IdempotencyGuard.execute_once()).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

from src.libs.contracts.primitives import TenantId
from src.libs.repositories.idempotency import IdempotencyRepository

T = TypeVar("T")

DEFAULT_TTL_SECONDS = 3_600
DEFAULT_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_POLL_TIMEOUT_SECONDS = 10.0


class IdempotencyGuard:
    """Guarantees at-most-once execution of an authoritative effect."""

    def __init__(
        self,
        repository: IdempotencyRepository,
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    ) -> None:
        self._repo = repository
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_timeout_seconds = poll_timeout_seconds

    async def execute_once(
        self,
        tenant_id: TenantId,
        key: str,
        resource_type: str,
        effect_fn: Callable[[], Awaitable[T]],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> T:
        """Execute ``effect_fn`` exactly once for ``(tenant_id, key)``.

        Args:
            tenant_id: Tenant scope (AR-8).
            key: Deterministic idempotency key (see ``IdempotencyKeyBuilder``).
            resource_type: Stable resource type code (e.g. ``'ptp'``).
            effect_fn: The authoritative effect to run at most once.
            ttl_seconds: How long the key remains valid.

        Returns:
            The result of ``effect_fn()`` — either freshly computed by this
            call (if it won the claim) or the cached result from whichever
            call won it first.

        Raises:
            TimeoutError: If this call lost the claim race and the winner's
                result never appeared within ``poll_timeout_seconds``.
        """
        cached = self._repo.check(tenant_id, key)
        if cached is not None:
            return cast(T, cached)

        claimed = self._repo.claim(tenant_id, key, resource_type, ttl_seconds=ttl_seconds)
        if claimed:
            result = await effect_fn()
            self._repo.complete(tenant_id, key, result)
            return result

        # Lost the claim race — another caller is executing (or has already
        # executed) this effect. Poll for its cached result instead of
        # re-executing effect_fn ourselves.
        deadline = time.monotonic() + self._poll_timeout_seconds
        while time.monotonic() < deadline:
            cached = self._repo.check(tenant_id, key)
            if cached is not None:
                return cast(T, cached)
            await asyncio.sleep(self._poll_interval_seconds)
        raise TimeoutError(f"IdempotencyGuard: timed out waiting for a concurrent claim on key={key!r} to complete")
