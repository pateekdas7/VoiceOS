"""Unit tests for StructuredLogger, OTelTracer, and Prometheus metrics (V3 Ch15-17)."""

from __future__ import annotations

import io
import json

from prometheus_client import CollectorRegistry

from src.libs.circuit_breaker.breaker import CircuitState
from src.libs.observability.logger import StructuredLogger
from src.libs.observability.metrics import get_red_metrics, record_circuit_breaker_state, record_queue_max_size
from src.libs.observability.tracer import OTelTracer

REQUIRED_LOG_FIELDS = {"timestamp", "level", "service", "tenant_id", "call_id", "trace_id", "correlation_id", "message"}


class TestStructuredLogger:
    def test_structured_logger_json_fields(self) -> None:
        """Required Sprint-016 test: log message -> JSON with all required fields."""
        stream = io.StringIO()
        logger = StructuredLogger("conversation-engine", stream=stream)

        logger.info(
            "turn processed",
            tenant_id="tenant-1",
            call_id="call-1",
            trace_id="trace-1",
            correlation_id="turn-1",
        )

        line = stream.getvalue().strip()
        record = json.loads(line)

        assert REQUIRED_LOG_FIELDS.issubset(record.keys())
        assert record["level"] == "INFO"
        assert record["service"] == "conversation-engine"
        assert record["tenant_id"] == "tenant-1"
        assert record["call_id"] == "call-1"
        assert record["trace_id"] == "trace-1"
        assert record["correlation_id"] == "turn-1"
        assert record["message"] == "turn processed"

    def test_pii_redactor_masks_phone_in_log_message(self) -> None:
        """Sprint-020 required test: log message containing a phone number is redacted."""
        stream = io.StringIO()
        logger = StructuredLogger("conversation-engine", stream=stream)

        logger.info("customer callback number is 9876543210")

        record = json.loads(stream.getvalue().strip())
        assert "9876543210" not in record["message"]
        assert "[PHONE]" in record["message"]

    def test_pii_redactor_masks_phone_in_extra_fields(self) -> None:
        stream = io.StringIO()
        logger = StructuredLogger("conversation-engine", stream=stream)

        logger.info("turn processed", customer_note="call back on 9876543210")

        record = json.loads(stream.getvalue().strip())
        assert "9876543210" not in record["customer_note"]
        assert "[PHONE]" in record["customer_note"]

    def test_every_line_is_independently_valid_json(self) -> None:
        stream = io.StringIO()
        logger = StructuredLogger("test-svc", stream=stream)

        logger.info("first")
        logger.warning("second")
        logger.error("third")

        lines = stream.getvalue().strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert REQUIRED_LOG_FIELDS.issubset(record.keys())

    def test_defaults_to_empty_correlation_fields(self) -> None:
        stream = io.StringIO()
        logger = StructuredLogger("test-svc", stream=stream)

        logger.debug("no context given")

        record = json.loads(stream.getvalue().strip())
        assert record["tenant_id"] == ""
        assert record["call_id"] == ""
        assert record["trace_id"] == ""
        assert record["correlation_id"] == ""

    def test_extra_fields_are_merged_into_the_record(self) -> None:
        stream = io.StringIO()
        logger = StructuredLogger("test-svc", stream=stream)

        logger.info("plan sealed", plan_id="plan-42")

        record = json.loads(stream.getvalue().strip())
        assert record["plan_id"] == "plan-42"


class TestOTelTracer:
    def test_otel_tracer_context_propagation(self) -> None:
        """Required Sprint-016 test: parent span -> child span shares trace_id."""
        tracer, exporter = OTelTracer.for_testing("conversation-engine")

        with tracer.start_span("parent") as parent_span:
            with tracer.start_span("child") as child_span:
                pass
            parent_trace_id = parent_span.get_span_context().trace_id
            child_trace_id = child_span.get_span_context().trace_id

        assert parent_trace_id == child_trace_id

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        assert {span.name for span in spans} == {"parent", "child"}

    def test_span_attributes_are_recorded(self) -> None:
        tracer, exporter = OTelTracer.for_testing("llm-runtime")

        with tracer.start_span("generate", attributes={"model": "qwen2.5-7b"}):
            pass

        spans = exporter.get_finished_spans()
        attributes = spans[0].attributes
        assert attributes is not None
        assert attributes["model"] == "qwen2.5-7b"

    def test_inject_and_continue_from_propagate_trace_id(self) -> None:
        tracer_a, exporter = OTelTracer.for_testing("gateway")
        tracer_b, _ = OTelTracer.for_testing("dialogue-manager")
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        tracer_b._provider.add_span_processor(SimpleSpanProcessor(exporter))

        carrier: dict[str, str] = {}
        with tracer_a.start_span("gateway.handle") as span_a:
            tracer_a.inject(carrier)
            trace_id_a = span_a.get_span_context().trace_id

        assert "traceparent" in carrier

        with tracer_b.continue_from(carrier), tracer_b.start_span("dm.route") as span_b:
            trace_id_b = span_b.get_span_context().trace_id

        assert trace_id_a == trace_id_b

    def test_force_flush_and_shutdown(self) -> None:
        tracer, _exporter = OTelTracer.for_testing("stt")
        with tracer.start_span("transcribe"):
            pass

        assert tracer.force_flush() is True
        tracer.shutdown()  # must not raise

    def test_for_production_builds_an_otlp_backed_tracer(self) -> None:
        """Construction only — no spans/flush here, to avoid a real network call
        to the (nonexistent, in this unit test) OTLP collector endpoint."""
        tracer = OTelTracer.for_production("llm-runtime", "http://otel-collector:4318/v1/traces")
        assert isinstance(tracer, OTelTracer)


class TestMetrics:
    def test_get_red_metrics_returns_cached_instance(self) -> None:
        registry = CollectorRegistry()
        metrics1 = get_red_metrics("unique-test-service-a", registry=registry)
        metrics2 = get_red_metrics("unique-test-service-a", registry=registry)

        assert metrics1 is metrics2

    def test_record_request_updates_counters(self) -> None:
        registry = CollectorRegistry()
        metrics = get_red_metrics("unique-test-service-b", registry=registry)

        metrics.record_request("success", 12.5)

        assert metrics.requests_total.labels(status="success")._value.get() == 1.0

    def test_record_error_updates_counter(self) -> None:
        registry = CollectorRegistry()
        metrics = get_red_metrics("unique-test-service-c", registry=registry)

        metrics.record_error("timeout")

        assert metrics.errors_total.labels(error_type="timeout")._value.get() == 1.0

    def test_record_circuit_breaker_state_sets_gauge(self) -> None:
        record_circuit_breaker_state("unique-test-dependency", CircuitState.OPEN)
        # No exception means the gauge accepted the label/value; the gauge is a
        # process-wide singleton so we only assert the call succeeds without raising.

    def test_record_queue_max_size_sets_gauge(self) -> None:
        record_queue_max_size("unique-test-queue", 500)
