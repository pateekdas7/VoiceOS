"""CircuitBreaker — CLOSED -> OPEN -> HALF_OPEN state machine (V3 Ch14).

Wraps any external/cross-service call (STT, LLM, TTS, Postgres, Redis,
MongoDB, ...) with fail-fast protection: sustained failures trip the
breaker OPEN so callers fail immediately instead of queuing behind a dead
dependency; after a cooldown, a single HALF_OPEN probe call decides whether
to close (recovered) or re-open (still down).

Architecture: V3 Ch14 (Circuit Breakers) §14.9, §14.12.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar, overload

T = TypeVar("T")

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_WINDOW_SECONDS = 30.0
DEFAULT_COOLDOWN_SECONDS = 30.0


class CircuitState(StrEnum):
    """The three breaker states (V3 Ch14 §14.6)."""

    CLOSED = "closed"
    """Normal operation — calls pass through."""

    OPEN = "open"
    """Failing fast — no calls reach the dependency."""

    HALF_OPEN = "half_open"
    """Cooldown elapsed — a single probe call is admitted to test recovery."""


class CircuitOpenError(Exception):
    """Raised when :meth:`CircuitBreaker.call` is invoked while the breaker is OPEN.

    Raised immediately, without invoking the wrapped call — the defining
    fail-fast property of an open breaker (V3 Ch14 §14.14: "< 1 ms, no
    dependency call").
    """

    def __init__(self, service_name: str) -> None:
        super().__init__(f"Circuit breaker for '{service_name}' is OPEN — failing fast")
        self.service_name = service_name


@dataclass
class CircuitBreakerConfig:
    """Per-service breaker thresholds (V3 Ch14 §14.13)."""

    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    """Number of failures within ``window_seconds`` that trips the breaker OPEN."""

    window_seconds: float = DEFAULT_WINDOW_SECONDS
    """Rolling window over which failures are counted."""

    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    """How long the breaker stays OPEN before admitting a HALF_OPEN probe."""


class CircuitBreaker:
    """A single per-service circuit breaker.

    ``call()`` accepts both sync and async callables — the wrapped function's
    return value is awaited only if it is awaitable, so the same breaker
    can guard an async HTTP adapter call and a sync Postgres repository call.
    """

    def __init__(
        self,
        service_name: str,
        config: CircuitBreakerConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_state_change: Callable[[str, CircuitState], None] | None = None,
    ) -> None:
        """
        Args:
            service_name: Identifies the dependency this breaker guards
                (e.g. ``"llm"``, ``"postgres"``, ``"redis"``).
            config: Thresholds; defaults to 5 failures / 30s window / 30s cooldown.
            clock: Monotonic time source (injectable for deterministic tests).
            on_state_change: Optional callback invoked with
                ``(service_name, new_state)`` on every transition — the wiring
                point for Prometheus's ``circuit_breaker_state`` gauge.
        """
        self._service_name = service_name
        self._config = config or CircuitBreakerConfig()
        self._clock = clock
        self._on_state_change = on_state_change

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_timestamps: list[float] = []
        self._opened_at: float = 0.0
        self._half_open_probe_in_flight = False

    @property
    def service_name(self) -> str:
        """The dependency name this breaker guards."""
        return self._service_name

    @property
    def state(self) -> CircuitState:
        """Current breaker state."""
        return self._state

    @overload
    async def call(self, fn: Callable[..., Awaitable[T]], *args: object, **kwargs: object) -> T: ...
    @overload
    async def call(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T: ...
    async def call(self, fn: Callable[..., object], *args: object, **kwargs: object) -> object:
        """Invoke ``fn(*args, **kwargs)`` through the breaker.

        Args:
            fn: The wrapped call — sync or async.

        Returns:
            The result of ``fn``.

        Raises:
            CircuitOpenError: If the breaker is OPEN (or already probing in
                HALF_OPEN) — ``fn`` is never invoked in this case.
            Exception: Whatever ``fn`` itself raises, after recording the failure.
        """
        self._before_call()
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            self._record_failure(self._clock())
            raise
        else:
            self._record_success()
            return result

    def call_sync(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        """Synchronous counterpart to :meth:`call`, for genuinely sync dependencies
        (e.g. psycopg2/redis-py clients that are not awaitable).

        Same semantics as :meth:`call`: fails fast with :class:`CircuitOpenError`
        while OPEN, and shares breaker state with any concurrent ``call()`` use
        on this same instance.
        """
        self._before_call()
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self._record_failure(self._clock())
            raise
        else:
            self._record_success()
            return result

    def _before_call(self) -> None:
        """Shared CLOSED/OPEN/HALF_OPEN admission check for call()/call_sync()."""
        now = self._clock()
        self._maybe_end_cooldown(now)

        if self._state == CircuitState.OPEN:
            raise CircuitOpenError(self._service_name)

        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_probe_in_flight:
                raise CircuitOpenError(self._service_name)
            self._half_open_probe_in_flight = True

    def _maybe_end_cooldown(self, now: float) -> None:
        if self._state == CircuitState.OPEN and (now - self._opened_at) >= self._config.cooldown_seconds:
            self._transition(CircuitState.HALF_OPEN)
            self._half_open_probe_in_flight = False

    def _record_failure(self, now: float) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_probe_in_flight = False
            self._transition(CircuitState.OPEN)
            self._opened_at = now
            self._failure_timestamps.clear()
            return

        self._failure_timestamps.append(now)
        self._failure_timestamps = [t for t in self._failure_timestamps if now - t <= self._config.window_seconds]
        if len(self._failure_timestamps) >= self._config.failure_threshold:
            self._transition(CircuitState.OPEN)
            self._opened_at = now
            self._failure_timestamps.clear()

    def _record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_probe_in_flight = False
            self._transition(CircuitState.CLOSED)
        self._failure_timestamps.clear()

    def _transition(self, new_state: CircuitState) -> None:
        self._state = new_state
        if self._on_state_change is not None:
            self._on_state_change(self._service_name, new_state)


@dataclass
class CircuitBreakerRegistry:
    """Per-service breaker registry — one breaker per external dependency.

    V3 Ch14 §14.2: "each external dependency (STT, LLM, TTS, Postgres,
    Redis, MongoDB) has its own breaker." Services obtain their breaker via
    :meth:`get_or_create` instead of constructing ``CircuitBreaker`` directly,
    so the same named dependency always shares one breaker instance.
    """

    default_config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    on_state_change: Callable[[str, CircuitState], None] | None = None
    _breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def get_or_create(self, service_name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
        """Return the existing breaker for ``service_name``, creating it on first use."""
        if service_name not in self._breakers:
            self._breakers[service_name] = CircuitBreaker(
                service_name,
                config or self.default_config,
                on_state_change=self.on_state_change,
            )
        return self._breakers[service_name]

    def get(self, service_name: str) -> CircuitBreaker | None:
        """Return the breaker for ``service_name`` if it has been created, else None."""
        return self._breakers.get(service_name)

    def all_breakers(self) -> dict[str, CircuitBreaker]:
        """A snapshot of every registered breaker, keyed by service name."""
        return dict(self._breakers)
