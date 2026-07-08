"""OTelTracer — OpenTelemetry tracer with W3C Trace Context propagation (V3 Ch17).

Each ``OTelTracer`` owns its own ``TracerProvider`` (not the OTel global) so
that multiple services/tests can each run an independent tracer in the same
process without clobbering a shared global provider. Span parenting still
works correctly across ``OTelTracer`` instances because OpenTelemetry
context propagation is carried on ``contextvars``, not on the provider.

Architecture: V3 Ch17 (Tracing) §17.7, §17.12.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager

from opentelemetry import context as otel_context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()


class OTelTracer:
    """A per-service OpenTelemetry tracer with an isolated (non-global) provider."""

    def __init__(self, service_name: str, exporter: SpanExporter, *, use_batch_processor: bool = False) -> None:
        """
        Args:
            service_name: Recorded as the ``service.name`` resource attribute.
            exporter: Where finished spans are sent (InMemorySpanExporter for
                tests, an OTLP exporter for production — see
                :meth:`for_testing` / :meth:`for_production`).
            use_batch_processor: True for async batched export (production);
                False for synchronous export (tests — spans are visible
                immediately after the span ends).
        """
        self._service_name = service_name
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        processor = BatchSpanProcessor(exporter) if use_batch_processor else SimpleSpanProcessor(exporter)
        provider.add_span_processor(processor)
        self._provider = provider
        self._tracer = provider.get_tracer(service_name)

    @classmethod
    def for_testing(cls, service_name: str) -> tuple[OTelTracer, InMemorySpanExporter]:
        """Build a tracer backed by an in-memory exporter, for unit/integration tests."""
        exporter = InMemorySpanExporter()
        return cls(service_name, exporter, use_batch_processor=False), exporter

    @classmethod
    def for_production(cls, service_name: str, otlp_endpoint: str) -> OTelTracer:
        """Build a tracer that batches spans to a real OTel collector via OTLP/HTTP."""
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return cls(service_name, OTLPSpanExporter(endpoint=otlp_endpoint), use_batch_processor=True)

    @contextmanager
    def start_span(self, name: str, attributes: dict[str, str | int | float | bool] | None = None) -> Iterator[Span]:
        """Start a span as a child of whatever span is current on this context.

        Args:
            name: Span name (ideally one per V1 Ch23 latency-budget line).
            attributes: Span attributes (ids, model names, sizes — never PII).

        Yields:
            The started (and current) Span.
        """
        with self._tracer.start_as_current_span(name, attributes=attributes or {}) as span:
            yield span

    def inject(self, carrier: MutableMapping[str, str]) -> None:
        """Inject the current span's context into ``carrier`` as W3C ``traceparent`` headers."""
        _PROPAGATOR.inject(carrier)

    @contextmanager
    def continue_from(self, carrier: MutableMapping[str, str]) -> Iterator[None]:
        """Attach the trace context extracted from ``carrier`` for the duration of the block.

        Use at a service boundary: extract the inbound ``traceparent`` header
        and wrap subsequent ``start_span`` calls in this context manager so
        the new spans become children of the calling service's span,
        continuing one trace across the boundary.
        """
        ctx = _PROPAGATOR.extract(carrier)
        token = otel_context.attach(ctx)
        try:
            yield
        finally:
            otel_context.detach(token)

    def force_flush(self, timeout_millis: int = 5000) -> bool:
        """Flush any pending span exports (call before process shutdown)."""
        return self._provider.force_flush(timeout_millis)

    def shutdown(self) -> None:
        """Shut down the tracer's span processors/exporter."""
        self._provider.shutdown()


__all__ = ["InMemorySpanExporter", "OTelTracer"]
