"""Phase 3 — G9 (BackgroundHandlerRegistry) + G4 (cardinality guard) tests.

Run:
    python3 -m pytest tests/unit/monitoring/test_phase3_registry_and_cardinality.py --noconftest -v
"""
from __future__ import annotations

import pytest

from src.libs.observability.metrics import (
    BackgroundHandlerRegistry,
    MetricCardinalityError,
    assert_label_cardinality_safe,
    startup_handlers_gauge,
)


# ──────────────────────── G9 — BackgroundHandlerRegistry ────────────────────────


def test_registry_starts_empty() -> None:
    r = BackgroundHandlerRegistry()
    assert len(r) == 0
    assert r.names() == ()


def test_registry_register_sets_gauge_and_tracks_name() -> None:
    r = BackgroundHandlerRegistry()
    r.register("test_handler_a", lambda: None)
    assert "test_handler_a" in r.names()
    assert len(r) == 1
    # The gauge must advertise this registered handler.
    sample_value = startup_handlers_gauge.labels(name="test_handler_a")._value.get()
    assert sample_value == 1.0


def test_registry_start_all_invokes_each_handler_in_order() -> None:
    r = BackgroundHandlerRegistry()
    calls: list[str] = []
    r.register("first", lambda: calls.append("first"))
    r.register("second", lambda: calls.append("second"))
    r.register("third", lambda: calls.append("third"))
    r.start_all()
    assert calls == ["first", "second", "third"]


def test_registry_names_preserves_insertion_order() -> None:
    r = BackgroundHandlerRegistry()
    r.register("gpu_poller", lambda: None)
    r.register("daily_aggregation", lambda: None)
    r.register("escalation_scheduler", lambda: None)
    assert r.names() == ("gpu_poller", "daily_aggregation", "escalation_scheduler")


# ──────────────────────── G4 — Cardinality guard ────────────────────────────────


def test_valid_metric_passes() -> None:
    # Low-cardinality, correctly-prefixed metric.
    assert_label_cardinality_safe("voiceos_requests_total", ("service", "method"))


def test_missing_voiceos_prefix_rejected() -> None:
    with pytest.raises(MetricCardinalityError, match="must start with 'voiceos_'"):
        assert_label_cardinality_safe("requests_total", ("service",))


def test_tenant_id_label_rejected() -> None:
    with pytest.raises(MetricCardinalityError, match="high-cardinality label"):
        assert_label_cardinality_safe("voiceos_calls_total", ("tenant_id",))


def test_call_id_label_rejected() -> None:
    with pytest.raises(MetricCardinalityError, match="call_id"):
        assert_label_cardinality_safe("voiceos_call_duration_seconds", ("call_id",))


def test_multiple_offenders_reported_sorted() -> None:
    with pytest.raises(MetricCardinalityError) as excinfo:
        assert_label_cardinality_safe(
            "voiceos_whatever_total",
            ("tenant_id", "trace_id", "service"),
        )
    # Both offenders must be surfaced so operators can fix in one pass.
    msg = str(excinfo.value)
    assert "tenant_id" in msg
    assert "trace_id" in msg
    assert "service" not in msg  # legitimate dim should not be flagged


def test_empty_labels_passes() -> None:
    assert_label_cardinality_safe("voiceos_singleton_gauge", ())
