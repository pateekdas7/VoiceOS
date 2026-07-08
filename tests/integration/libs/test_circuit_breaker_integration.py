"""Integration tests for CircuitBreaker + OTel distributed tracing (Sprint-016).

These run fully in-process — no CPU/GPU infrastructure required (Sprint-016
Phase 1 mock table): dependency failures are simulated via an AsyncMock-style
fake with a configurable fail rate, and traces are captured via OpenTelemetry's
in-memory span exporter rather than a real Jaeger/Tempo collector.
"""

from __future__ import annotations

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.libs.observability.tracer import OTelTracer


class _FlakyDependency:
    """Simulates an external dependency (STT/LLM/TTS/DB) that fails N times then recovers."""

    def __init__(self, fail_count: int) -> None:
        self._remaining_failures = fail_count
        self.call_count = 0

    async def __call__(self) -> str:
        self.call_count += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ConnectionError("simulated dependency failure")
        return "inference-result"


class FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestCircuitBreakerAgainstSimulatedDependencyFailure:
    """Simulates an LLM failure (per Sprint-016 Phase 2 §Integration validation,
    reproduced here without real infrastructure): sustained failures trip the
    breaker, and CircuitOpenError is returned instead of hanging on retries."""

    async def test_breaker_opens_after_sustained_llm_failure_and_fails_fast(self) -> None:
        dependency = _FlakyDependency(fail_count=10)  # never recovers within the test
        breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=5))

        failures = 0
        for _ in range(5):
            try:
                await breaker.call(dependency)
            except ConnectionError:
                failures += 1

        assert failures == 5
        assert breaker.state == CircuitState.OPEN
        calls_before_open = dependency.call_count

        # Further calls must fail fast (CircuitOpenError), never reaching the dependency.
        for _ in range(3):
            try:
                await breaker.call(dependency)
            except CircuitOpenError:
                pass

        assert dependency.call_count == calls_before_open  # no new calls reached the dependency

    async def test_breaker_recovers_once_dependency_comes_back(self) -> None:
        dependency = _FlakyDependency(fail_count=5)
        clock = FakeClock()
        breaker = CircuitBreaker(
            "llm",
            CircuitBreakerConfig(failure_threshold=5, cooldown_seconds=30.0),
            clock=clock,
        )

        for _ in range(5):
            try:
                await breaker.call(dependency)
            except ConnectionError:
                pass
        assert breaker.state == CircuitState.OPEN

        clock.advance(31.0)  # cooldown elapses
        result = await breaker.call(dependency)  # HALF_OPEN probe — dependency has recovered

        assert result == "inference-result"
        # mypy over-narrows `breaker.state` across the intervening `await`
        # (it cannot see that CircuitBreaker.call mutates internal state).
        assert breaker.state == CircuitState.CLOSED  # type: ignore[comparison-overlap]

    async def test_each_dependency_has_an_independent_breaker(self) -> None:
        """Per-service breakers (V3 Ch14 §14.2): STT/LLM/TTS/Postgres/Redis/MongoDB each isolated."""
        llm_breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=1))
        stt_breaker = CircuitBreaker("stt", CircuitBreakerConfig(failure_threshold=1))

        failing = _FlakyDependency(fail_count=10)
        try:
            await llm_breaker.call(failing)
        except ConnectionError:
            pass

        assert llm_breaker.state == CircuitState.OPEN
        assert stt_breaker.state == CircuitState.CLOSED  # unaffected by the LLM breaker tripping


class TestOTelTraceEndToEnd:
    async def test_otel_trace_end_to_end(self) -> None:
        """Required Sprint-016 test: a fake call path across 3 services produces a
        single trace with 3 spans, visible in the (in-memory) collector."""
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        media_gateway_tracer, exporter = OTelTracer.for_testing("media-gateway")
        llm_runtime_tracer, _ = OTelTracer.for_testing("llm-runtime")
        tts_tracer, _ = OTelTracer.for_testing("tts")

        # Share one exporter/collector across all three "services" so the
        # test can assert on a single collected trace, as a real OTel
        # collector would aggregate spans from every service.
        llm_runtime_tracer._provider.add_span_processor(SimpleSpanProcessor(exporter))
        tts_tracer._provider.add_span_processor(SimpleSpanProcessor(exporter))

        carrier_gw_to_llm: dict[str, str] = {}
        with media_gateway_tracer.start_span("gateway.handle_call"):
            media_gateway_tracer.inject(carrier_gw_to_llm)

        carrier_llm_to_tts: dict[str, str] = {}
        with (
            llm_runtime_tracer.continue_from(carrier_gw_to_llm),
            llm_runtime_tracer.start_span("llm.generate"),
        ):
            llm_runtime_tracer.inject(carrier_llm_to_tts)

        with (
            tts_tracer.continue_from(carrier_llm_to_tts),
            tts_tracer.start_span("tts.synthesize"),
        ):
            pass

        spans = exporter.get_finished_spans()

        assert len(spans) == 3
        assert {span.name for span in spans} == {"gateway.handle_call", "llm.generate", "tts.synthesize"}
        trace_ids = {span.context.trace_id for span in spans}
        assert len(trace_ids) == 1, "all 3 spans must belong to a single end-to-end trace"
