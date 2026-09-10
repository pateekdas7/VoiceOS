"""Unit tests for the Media Gateway Prometheus metric definitions.

These tests import only ``metrics.py`` — no adapter/audio dependencies —
so they can validate metric shape (name, labels, monotonicity) in
environments where the full ``twilio_ws_entrypoint`` import chain
(numpy, audio codecs, etc.) is not available.
"""

from __future__ import annotations

from src.services.media_gateway import metrics


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]


def test_greeting_outcomes_counter_supports_ok_timeout_error_labels() -> None:
    """All three terminal outcomes must be recordable — otherwise the
    alerting rule on ``outcome="timeout"`` silently reports zero forever."""
    before_ok = _counter_value(metrics.GREETING_OUTCOMES, outcome="ok")
    before_timeout = _counter_value(metrics.GREETING_OUTCOMES, outcome="timeout")
    before_error = _counter_value(metrics.GREETING_OUTCOMES, outcome="error")

    metrics.GREETING_OUTCOMES.labels(outcome="ok").inc()
    metrics.GREETING_OUTCOMES.labels(outcome="timeout").inc()
    metrics.GREETING_OUTCOMES.labels(outcome="error").inc()

    assert _counter_value(metrics.GREETING_OUTCOMES, outcome="ok") == before_ok + 1
    assert _counter_value(metrics.GREETING_OUTCOMES, outcome="timeout") == before_timeout + 1
    assert _counter_value(metrics.GREETING_OUTCOMES, outcome="error") == before_error + 1


def test_greeting_outcomes_metric_name_matches_alerting_contract() -> None:
    """The metric name is a public interface — the ops team's Prometheus
    alert rules key off ``voiceos_media_gateway_greeting_outcomes_total``.
    Renaming it here without coordinated dashboard/alert updates would
    silently break paging on GPU/TTS regressions."""
    assert metrics.GREETING_OUTCOMES._name == "voiceos_media_gateway_greeting_outcomes"


def test_record_bytes_received_accumulates() -> None:
    before = _counter_value(metrics.BYTES_RECEIVED, adapter_type="twilio")
    metrics.record_bytes_received(320, adapter_type="twilio")
    metrics.record_bytes_received(160, adapter_type="twilio")
    assert _counter_value(metrics.BYTES_RECEIVED, adapter_type="twilio") == before + 480


def test_session_admitted_released_moves_gauge() -> None:
    before = metrics.ACTIVE_SESSIONS.labels(adapter_type="twilio")._value.get()  # type: ignore[attr-defined]
    metrics.record_session_admitted("twilio")
    metrics.record_session_admitted("twilio")
    metrics.record_session_released("twilio")
    after = metrics.ACTIVE_SESSIONS.labels(adapter_type="twilio")._value.get()  # type: ignore[attr-defined]
    assert after == before + 1
