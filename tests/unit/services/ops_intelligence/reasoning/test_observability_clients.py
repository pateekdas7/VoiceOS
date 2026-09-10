"""Unit tests for the concrete Prometheus/Loki/Jaeger/EventBus query adapters (ADR-006 Sec 3.2.1).

All tests use httpx.MockTransport -- no real network or live observability
stack required, mirroring tests/unit/services/test_whisper_http_adapter.py's
own MockTransport convention.
"""

from __future__ import annotations

import httpx
import pytest

from src.services.ops_intelligence.reasoning.observability_clients import (
    EventBusQueryAdapter,
    JaegerQueryAdapter,
    LokiQueryAdapter,
    PrometheusQueryAdapter,
)


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real_client_cls = httpx.Client

    def _client_factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs.pop("timeout", None)
        return real_client_cls(transport=transport, timeout=5.0)

    monkeypatch.setattr(httpx, "Client", _client_factory)


class TestPrometheusQueryAdapter:
    def test_instant_returns_sample_from_query_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/query"
            return httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1721908800, "910.5"]}]}},
            )

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = PrometheusQueryAdapter()
        sample = adapter.instant("voiceos_tts_first_clause_latency_ms")
        assert sample is not None
        assert sample.value == pytest.approx(910.5)

    def test_instant_returns_none_on_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": []}})

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = PrometheusQueryAdapter()
        assert adapter.instant("nonexistent_metric") is None

    def test_historical_series_parses_range_query_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/query_range"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {"result": [{"metric": {}, "values": [[1721822400, "10.0"], [1721908800, "12.0"]]}]},
                },
            )

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = PrometheusQueryAdapter()
        series = adapter.historical_series("gpu", days=2)
        assert len(series) == 2
        assert series[0][1] == 10.0
        assert series[1][1] == 12.0

    def test_current_capacity_uses_configured_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_queries: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured_queries.append(dict(request.url.params)["query"])
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"value": [1721908800, "100"]}]}})

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = PrometheusQueryAdapter().with_resource_queries({}, {"gpu": "voiceos_gpu_fleet_vram_budget_mb"})
        capacity = adapter.current_capacity("gpu")
        assert capacity == 100.0
        assert captured_queries == ["voiceos_gpu_fleet_vram_budget_mb"]


class TestLokiQueryAdapter:
    def test_query_extracts_log_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/loki/api/v1/query_range"
            return httpx.Response(
                200,
                json={"data": {"result": [{"stream": {}, "values": [["1721908800000000000", "error: gpu timeout"]]}]}},
            )

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = LokiQueryAdapter()
        lines = adapter.query('{service="tts"} |= "error"')
        assert lines == ("error: gpu timeout",)

    def test_query_respects_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": {"result": [{"stream": {}, "values": [[str(i), f"line {i}"] for i in range(10)]}]}},
            )

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = LokiQueryAdapter()
        lines = adapter.query("{service=\"tts\"}", limit=3)
        assert len(lines) == 3


class TestJaegerQueryAdapter:
    def test_query_extracts_trace_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/traces"
            assert dict(request.url.params)["service"] == "tts"
            return httpx.Response(200, json={"data": [{"traceID": "abc123"}, {"traceID": "def456"}]})

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = JaegerQueryAdapter()
        trace_ids = adapter.query("service=tts")
        assert trace_ids == ("abc123", "def456")

    def test_query_handles_no_traces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        _patch_client(monkeypatch, httpx.MockTransport(_handler))
        adapter = JaegerQueryAdapter()
        assert adapter.query("service=stt") == ()


class TestEventBusQueryAdapter:
    class _FakeEnvelope:
        def __init__(self, event_type: str, tenant_id: str | None, payload: dict[str, object]) -> None:
            self.event_type = event_type
            self.tenant_id = tenant_id
            self.payload = payload

    class _FakeEventBus:
        def __init__(self, entries: list[tuple[str, object]]) -> None:
            self._entries = entries

        def replay_from(self) -> list[tuple[str, object]]:
            return self._entries

    def test_recent_filters_by_event_type(self) -> None:
        entries = [
            ("1", self._FakeEnvelope("compliance.violation_alert", "t1", {"x": 1})),
            ("2", self._FakeEnvelope("saas.call.dispositioned", "t1", {"y": 2})),
        ]
        adapter = EventBusQueryAdapter(self._FakeEventBus(entries))
        results = adapter.recent("compliance.violation_alert", tenant_id=None)
        assert results == ({"x": 1},)

    def test_recent_filters_by_tenant(self) -> None:
        entries = [
            ("1", self._FakeEnvelope("compliance.violation_alert", "t1", {"x": 1})),
            ("2", self._FakeEnvelope("compliance.violation_alert", "t2", {"x": 2})),
        ]
        adapter = EventBusQueryAdapter(self._FakeEventBus(entries))
        results = adapter.recent("compliance.violation_alert", tenant_id="t2")
        assert results == ({"x": 2},)

    def test_recent_respects_limit(self) -> None:
        entries = [("i", self._FakeEnvelope("e", None, {"n": i})) for i in range(10)]
        adapter = EventBusQueryAdapter(self._FakeEventBus(entries))
        results = adapter.recent("e", tenant_id=None, limit=3)
        assert len(results) == 3
