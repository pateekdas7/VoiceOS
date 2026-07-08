"""Unit tests for CircuitBreaker CLOSED -> OPEN -> HALF_OPEN -> CLOSED (V3 Ch14)."""

from __future__ import annotations

import asyncio

import pytest

from src.libs.circuit_breaker.breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitOpenError,
    CircuitState,
)


class FakeClock:
    """Deterministic monotonic-clock double for cooldown/window testing."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


async def _fail(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("dependency unavailable")


async def _succeed(*_args: object, **_kwargs: object) -> str:
    return "ok"


class TestCircuitBreakerClosedToOpen:
    async def test_circuit_breaker_open_after_threshold(self) -> None:
        """Required Sprint-016 test: 5 failures -> state=OPEN."""
        breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=5))
        assert breaker.service_name == "llm"

        for _ in range(5):
            with pytest.raises(RuntimeError):
                await breaker.call(_fail)

        assert breaker.state == CircuitState.OPEN

    async def test_stays_closed_below_threshold(self) -> None:
        breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=5))

        for _ in range(4):
            with pytest.raises(RuntimeError):
                await breaker.call(_fail)

        assert breaker.state == CircuitState.CLOSED

    async def test_successes_do_not_count_toward_the_failure_window(self) -> None:
        breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=3))

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await breaker.call(_fail)
        await breaker.call(_succeed)  # clears the accumulated failure window
        with pytest.raises(RuntimeError):
            await breaker.call(_fail)

        assert breaker.state == CircuitState.CLOSED


class TestCircuitBreakerOpenFailsFast:
    async def test_circuit_breaker_open_returns_immediately(self) -> None:
        """Required Sprint-016 test: OPEN -> CircuitOpenError without calling fn."""
        breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=1))
        with pytest.raises(RuntimeError):
            await breaker.call(_fail)
        assert breaker.state == CircuitState.OPEN

        called = False

        async def should_not_run() -> None:
            nonlocal called
            called = True

        with pytest.raises(CircuitOpenError):
            await breaker.call(should_not_run)

        assert called is False


class TestCircuitBreakerHalfOpenRecovery:
    async def test_circuit_breaker_half_open_success(self) -> None:
        """Required Sprint-016 test: after cooldown, success -> state=CLOSED."""
        clock = FakeClock()
        breaker = CircuitBreaker(
            "llm",
            CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=10.0),
            clock=clock,
        )

        with pytest.raises(RuntimeError):
            await breaker.call(_fail)
        assert breaker.state == CircuitState.OPEN

        clock.advance(11.0)  # past cooldown
        result = await breaker.call(_succeed)

        assert result == "ok"
        # mypy over-narrows `breaker.state` across the intervening `await`
        # (it cannot see that CircuitBreaker.call mutates internal state).
        assert breaker.state == CircuitState.CLOSED  # type: ignore[comparison-overlap]

    async def test_half_open_failure_reopens(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            "llm",
            CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=10.0),
            clock=clock,
        )

        with pytest.raises(RuntimeError):
            await breaker.call(_fail)
        clock.advance(11.0)

        with pytest.raises(RuntimeError):
            await breaker.call(_fail)  # the HALF_OPEN probe itself fails

        assert breaker.state == CircuitState.OPEN

    async def test_half_open_rejects_concurrent_second_probe(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            "llm",
            CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=10.0),
            clock=clock,
        )
        with pytest.raises(RuntimeError):
            await breaker.call(_fail)
        clock.advance(11.0)  # past cooldown -> next call() enters HALF_OPEN

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_probe() -> str:
            started.set()
            await release.wait()
            return "ok"

        probe_task = asyncio.create_task(breaker.call(slow_probe))
        await started.wait()
        assert breaker.state == CircuitState.HALF_OPEN

        with pytest.raises(CircuitOpenError):
            await breaker.call(_succeed)  # a second probe while one is in-flight

        release.set()
        result = await probe_task

        assert result == "ok"
        assert breaker.state == CircuitState.CLOSED  # type: ignore[comparison-overlap]

    async def test_stays_open_before_cooldown_elapses(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            "llm",
            CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=30.0),
            clock=clock,
        )
        with pytest.raises(RuntimeError):
            await breaker.call(_fail)

        clock.advance(5.0)  # still within cooldown
        with pytest.raises(CircuitOpenError):
            await breaker.call(_succeed)
        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerSyncCallables:
    async def test_wraps_sync_callables(self) -> None:
        """CircuitBreaker.call() must work with sync functions (e.g. psycopg2 repository calls)."""
        breaker = CircuitBreaker("postgres")

        def sync_query() -> int:
            return 42

        result = await breaker.call(sync_query)

        assert result == 42

    async def test_records_sync_failures(self) -> None:
        breaker = CircuitBreaker("postgres", CircuitBreakerConfig(failure_threshold=1))

        def sync_failing() -> None:
            raise ValueError("db down")

        with pytest.raises(ValueError):
            await breaker.call(sync_failing)

        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerCallSync:
    def test_call_sync_returns_result(self) -> None:
        breaker = CircuitBreaker("redis")

        result = breaker.call_sync(lambda: 7)

        assert result == 7

    def test_call_sync_records_failure_and_opens(self) -> None:
        breaker = CircuitBreaker("redis", CircuitBreakerConfig(failure_threshold=1))

        def sync_failing() -> None:
            raise ConnectionError("redis down")

        with pytest.raises(ConnectionError):
            breaker.call_sync(sync_failing)

        assert breaker.state == CircuitState.OPEN

    def test_call_sync_fails_fast_when_open(self) -> None:
        breaker = CircuitBreaker("redis", CircuitBreakerConfig(failure_threshold=1))

        def sync_failing() -> None:
            raise ConnectionError("redis down")

        with pytest.raises(ConnectionError):
            breaker.call_sync(sync_failing)

        called = False

        def should_not_run() -> int:
            nonlocal called
            called = True
            return 1

        with pytest.raises(CircuitOpenError):
            breaker.call_sync(should_not_run)

        assert called is False


class TestCircuitBreakerStateChangeCallback:
    async def test_on_state_change_invoked_on_transitions(self) -> None:
        transitions: list[tuple[str, CircuitState]] = []
        breaker = CircuitBreaker(
            "redis",
            CircuitBreakerConfig(failure_threshold=1),
            on_state_change=lambda name, state: transitions.append((name, state)),
        )

        with pytest.raises(RuntimeError):
            await breaker.call(_fail)

        assert transitions == [("redis", CircuitState.OPEN)]


class TestCircuitBreakerRegistry:
    async def test_get_or_create_returns_same_instance(self) -> None:
        registry = CircuitBreakerRegistry()

        breaker1 = registry.get_or_create("llm")
        breaker2 = registry.get_or_create("llm")

        assert breaker1 is breaker2

    async def test_per_service_breakers_are_independent(self) -> None:
        registry = CircuitBreakerRegistry(default_config=CircuitBreakerConfig(failure_threshold=1))

        llm_breaker = registry.get_or_create("llm")
        redis_breaker = registry.get_or_create("redis")

        with pytest.raises(RuntimeError):
            await llm_breaker.call(_fail)

        assert llm_breaker.state == CircuitState.OPEN
        assert redis_breaker.state == CircuitState.CLOSED

    def test_get_returns_none_for_unregistered_service(self) -> None:
        registry = CircuitBreakerRegistry()
        assert registry.get("unknown") is None

    def test_all_breakers_snapshot(self) -> None:
        registry = CircuitBreakerRegistry()
        registry.get_or_create("llm")
        registry.get_or_create("tts")

        all_breakers = registry.all_breakers()

        assert set(all_breakers) == {"llm", "tts"}
