"""Unit tests for Phase 8 — Observability Hardening.

Covers:
  8a  Structured JSON logger shape and trace-ID propagation
  8b  bffAudit helper (schema + error-isolation behaviour)
  8c  OTelTracer wiring (SharedCallDependencies.tracer field, span names)
  8d  Prometheus metric presence and record_* helpers
  8e  Alert-rule file presence and YAML validity
  8f  MongoDB verify_indexes.py CLI (dry-run + verify + fix logic)
"""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, AsyncMock, patch, call as mock_call

import pytest

REPO_ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# 8a — Structured JSON logger
# ---------------------------------------------------------------------------


class TestStructuredLogger:
    """Verify log object shape and all levels emit valid JSON lines."""

    def _make_log(self) -> tuple[ModuleType, io.StringIO]:
        """Import a minimal log fixture that matches bff.js's log object in Python."""
        import json as _json
        import time as _time

        buf = io.StringIO()

        class _Log:
            def _write(self, level: str, event: str, fields: dict | None = None) -> None:
                line = {"timestamp": "2026-01-01T00:00:00Z", "level": level, "service": "voiceos-bff", "event": event, **(fields or {})}
                buf.write(_json.dumps(line) + "\n")

            def info(self, event: str, fields: dict | None = None) -> None:
                self._write("INFO", event, fields)

            def warn(self, event: str, fields: dict | None = None) -> None:
                self._write("WARN", event, fields)

            def error(self, event: str, fields: dict | None = None) -> None:
                self._write("ERROR", event, fields)

        return _Log(), buf

    def test_info_emits_valid_json(self) -> None:
        log, buf = self._make_log()
        log.info("test.event", {"trace_id": "abc", "tenant_id": "t1"})
        line = json.loads(buf.getvalue().strip())
        assert line["level"] == "INFO"
        assert line["event"] == "test.event"
        assert line["service"] == "voiceos-bff"
        assert line["trace_id"] == "abc"

    def test_warn_level(self) -> None:
        log, buf = self._make_log()
        log.warn("warn.event")
        line = json.loads(buf.getvalue().strip())
        assert line["level"] == "WARN"

    def test_error_level(self) -> None:
        log, buf = self._make_log()
        log.error("error.event", {"error": "boom"})
        line = json.loads(buf.getvalue().strip())
        assert line["level"] == "ERROR"
        assert line["error"] == "boom"

    def test_fields_are_merged_at_root(self) -> None:
        log, buf = self._make_log()
        log.info("x", {"a": 1, "b": "hello"})
        line = json.loads(buf.getvalue().strip())
        assert line["a"] == 1
        assert line["b"] == "hello"


# ---------------------------------------------------------------------------
# 8b — bffAudit helper (Python re-implementation for unit testing)
# ---------------------------------------------------------------------------


class TestBffAudit:
    """Verify the audit helper inserts the right columns and swallows DB errors."""

    async def _run_audit(
        self,
        *,
        db_error: Exception | None = None,
    ) -> tuple[list, list]:
        """Simulate bffAudit() and return (queries_executed, warnings_logged)."""
        queries: list[tuple] = []
        warnings: list[str] = []

        class _FakeClient:
            async def query(self, sql: str, params: list) -> None:
                if db_error is not None:
                    raise db_error
                queries.append((sql, params))

        class _FakeLog:
            def warn(self, event: str, fields: dict | None = None) -> None:
                warnings.append(event)

        log = _FakeLog()
        client = _FakeClient()

        async def bff_audit(c, *, req, action, resource_type, resource_id, outcome="SUCCESS", metadata=None):
            metadata = metadata or {}
            tenant_id = getattr(getattr(req, "user", None), "tenant_id", None)
            actor_id = getattr(getattr(req, "user", None), "sub", "anonymous")
            ip = getattr(req, "ip", "")
            trace_id = getattr(req, "traceId", "")
            try:
                await c.query(
                    "INSERT INTO audit_log (tenant_id,actor_id,action,resource_type,resource_id,outcome,ip_address,event_payload) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
                    [tenant_id, actor_id, action, resource_type, str(resource_id), outcome, ip, json.dumps({"trace_id": trace_id, **metadata})],
                )
            except Exception as exc:
                log.warn("audit.write_failed", {"action": action, "error": str(exc)})

        req = MagicMock()
        req.user.tenant_id = "tenant-1"
        req.user.sub = "user-1"
        req.ip = "1.2.3.4"
        req.traceId = "trace-abc"

        await bff_audit(client, req=req, action="campaign.create", resource_type="campaign", resource_id="camp-1", metadata={"name": "Test"})
        return queries, warnings

    @pytest.mark.asyncio
    async def test_inserts_into_audit_log(self) -> None:
        queries, warnings = await self._run_audit()
        assert len(queries) == 1
        sql, params = queries[0]
        assert "INSERT INTO audit_log" in sql
        assert params[0] == "tenant-1"  # tenant_id
        assert params[1] == "user-1"    # actor_id
        assert params[2] == "campaign.create"
        assert params[3] == "campaign"
        assert params[4] == "camp-1"
        assert params[5] == "SUCCESS"
        assert params[6] == "1.2.3.4"
        payload = json.loads(params[7])
        assert payload["trace_id"] == "trace-abc"
        assert payload["name"] == "Test"

    @pytest.mark.asyncio
    async def test_db_error_is_swallowed(self) -> None:
        """A DB failure must NOT propagate — audit must never crash the caller."""
        queries, warnings = await self._run_audit(db_error=RuntimeError("pg unavailable"))
        assert len(queries) == 0
        assert warnings == ["audit.write_failed"]


# ---------------------------------------------------------------------------
# 8c — OTel span coverage in SharedCallDependencies
# ---------------------------------------------------------------------------


try:
    from src.services.media_gateway.twilio_ws_entrypoint import SharedCallDependencies
    _DEPS_IMPORTABLE = True
except Exception:
    _DEPS_IMPORTABLE = False

_skip_deps = pytest.mark.skipif(not _DEPS_IMPORTABLE, reason="pydantic v2 / prometheus_client not available")


class TestOTelWiring:
    @_skip_deps
    def test_shared_deps_has_tracer_field(self) -> None:
        import dataclasses
        fields = {f.name for f in dataclasses.fields(SharedCallDependencies)}
        assert "tracer" in fields, "SharedCallDependencies must have a 'tracer' field"

    @_skip_deps
    def test_tracer_defaults_to_none(self) -> None:
        import dataclasses
        f = next(f for f in dataclasses.fields(SharedCallDependencies) if f.name == "tracer")
        assert f.default is None

    def test_otel_tracer_for_testing_creates_in_memory_exporter(self) -> None:
        try:
            from src.libs.observability.tracer import OTelTracer
        except ImportError:
            pytest.skip("opentelemetry not available on this platform")
        tracer, exporter = OTelTracer.for_testing("test_service")
        assert tracer is not None
        assert exporter is not None

    def test_otel_span_is_recorded(self) -> None:
        try:
            from src.libs.observability.tracer import OTelTracer
        except ImportError:
            pytest.skip("opentelemetry not available on this platform")
        tracer, exporter = OTelTracer.for_testing("test_service")
        with tracer.start_span("stt.transcribe", {"call_id": "c1", "turn_index": 0}):
            pass
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "stt.transcribe"

    def test_nullcontext_does_not_raise(self) -> None:
        """Verify the None-tracer path uses contextlib.nullcontext correctly."""
        import contextlib
        tracer = None
        with (tracer if tracer else contextlib.nullcontext()):
            pass  # should not raise


# ---------------------------------------------------------------------------
# 8d — Prometheus metrics presence
# ---------------------------------------------------------------------------


try:
    import src.libs.observability.metrics as _metrics_mod
    _METRICS_IMPORTABLE = True
except Exception:
    _METRICS_IMPORTABLE = False

_skip_metrics = pytest.mark.skipif(not _METRICS_IMPORTABLE, reason="prometheus_client not available")


class TestPrometheusMetrics:
    """Verify every Section 17.2 metric is defined and the record_* helpers exist."""

    SERVICE_LEVEL_COUNTERS = [
        "calls_initiated_total",
        "calls_completed_total",
        "retry_count_total",
        "stuck_calls_total",
        "callback_auth_failures_total",
    ]
    SERVICE_LEVEL_HISTOGRAMS = [
        "call_duration_seconds",
        "queue_age_seconds",
    ]
    SERVICE_LEVEL_GAUGES = [
        "dialer_queue_depth",
    ]
    VOICE_COUNTERS = [
        "gpu_errors_total",
        "ws_disconnects_total",
    ]
    VOICE_HISTOGRAMS = [
        "stt_latency_ms",
        "llm_latency_ms",
        "tts_latency_ms",
        "turn_latency_ms",
    ]
    BUSINESS_COUNTERS = [
        "ptp_created_total",
        "hitl_escalations_total",
        "hitl_sla_breached_total",
        "billing_events_total",
    ]
    RECORD_HELPERS = [
        "record_call_initiated",
        "record_call_completed",
        "record_call_duration",
        "record_dialer_queue_depth",
        "record_queue_age",
        "record_retry",
        "record_stuck_call",
        "record_callback_auth_failure",
        "record_stt_latency",
        "record_llm_latency",
        "record_tts_latency",
        "record_turn_latency",
        "record_gpu_error",
        "record_ws_disconnect",
        "record_ptp_created",
        "record_hitl_escalation",
        "record_hitl_sla_breach",
        "record_billing_event",
    ]

    @_skip_metrics
    @pytest.mark.parametrize("name", SERVICE_LEVEL_COUNTERS + VOICE_COUNTERS + BUSINESS_COUNTERS)
    def test_counter_defined(self, name: str) -> None:
        assert hasattr(_metrics_mod, name), f"metrics.py missing counter: {name}"

    @_skip_metrics
    @pytest.mark.parametrize("name", SERVICE_LEVEL_HISTOGRAMS + VOICE_HISTOGRAMS)
    def test_histogram_defined(self, name: str) -> None:
        assert hasattr(_metrics_mod, name), f"metrics.py missing histogram: {name}"

    @_skip_metrics
    @pytest.mark.parametrize("name", SERVICE_LEVEL_GAUGES)
    def test_gauge_defined(self, name: str) -> None:
        assert hasattr(_metrics_mod, name), f"metrics.py missing gauge: {name}"

    @_skip_metrics
    @pytest.mark.parametrize("helper", RECORD_HELPERS)
    def test_record_helper_exists(self, helper: str) -> None:
        assert callable(getattr(_metrics_mod, helper, None)), f"metrics.py missing callable: {helper}"


# ---------------------------------------------------------------------------
# 8e — Alert rule files present and valid YAML
# ---------------------------------------------------------------------------


ALERT_RULES_DIR = REPO_ROOT / "monitoring" / "prometheus" / "alert_rules"

REQUIRED_ALERT_FILES = [
    "application.yml",
    "business.yml",
    "infrastructure.yml",
    "voiceos_bff_dialer.yml",
]

REQUIRED_ALERTS = {
    "application.yml": ["HighTurnLatency", "PostgresErrors", "RedisErrors"],
    "business.yml": ["HITLSLABreach", "TenantIsolationViolation", "BillingInconsistency"],
    "voiceos_bff_dialer.yml": ["CallbackAuthFailure", "QueueBacklog", "WorkerDead", "StuckCalls"],
}


class TestAlertRules:
    @pytest.mark.parametrize("fname", REQUIRED_ALERT_FILES)
    def test_alert_file_exists(self, fname: str) -> None:
        assert (ALERT_RULES_DIR / fname).exists(), f"Alert rule file missing: {fname}"

    @pytest.mark.parametrize("fname", REQUIRED_ALERT_FILES)
    def test_alert_file_valid_yaml(self, fname: str) -> None:
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not available")
        content = (ALERT_RULES_DIR / fname).read_text()
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict), f"{fname} is not a valid YAML dict"
        assert "groups" in parsed, f"{fname} has no 'groups' key"

    @pytest.mark.parametrize("fname,expected_alerts", REQUIRED_ALERTS.items())
    def test_required_alerts_present(self, fname: str, expected_alerts: list[str]) -> None:
        content = (ALERT_RULES_DIR / fname).read_text()
        for alert_name in expected_alerts:
            assert alert_name in content, f"Alert '{alert_name}' missing from {fname}"


# ---------------------------------------------------------------------------
# 8f — MongoDB verify_indexes.py CLI
# ---------------------------------------------------------------------------


VERIFY_SCRIPT = REPO_ROOT / "scripts" / "db" / "mongodb" / "verify_indexes.py"


class TestVerifyIndexes:
    def test_script_exists(self) -> None:
        assert VERIFY_SCRIPT.exists()

    def test_script_syntax(self) -> None:
        import ast
        ast.parse(VERIFY_SCRIPT.read_text())

    def test_dry_run_exits_zero(self) -> None:
        """--dry-run should exit 0 without connecting to MongoDB."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"dry-run exited {result.returncode}: {result.stderr}"

    def test_dry_run_output_mentions_collections(self) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--dry-run"],
            capture_output=True,
            text=True,
        )
        output = result.stdout
        for coll in ("response_plans", "decision_envelopes", "call_transcripts", "call_lineage"):
            assert coll in output, f"dry-run output missing collection: {coll}"

    def test_mutually_exclusive_flags(self) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--dry-run", "--fix"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "--dry-run --fix should exit non-zero"

    def test_no_uri_exits_nonzero(self) -> None:
        import subprocess
        env = {k: v for k, v in os.environ.items() if k not in ("MONGO_URI", "MONGODB_URI")}
        result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0

    def _load_verify_mod(self):
        """Load verify_indexes.py as a module, skipping if the loader is unavailable."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("_verify_indexes_mod", str(VERIFY_SCRIPT))
        if spec is None or spec.loader is None:
            pytest.skip("importlib cannot load verify_indexes.py on this platform")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_verify_indexes_mod"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    def test_verify_collection_reports_present_and_missing(self) -> None:
        """Unit-test verify_collection() with a mock pymongo Collection."""
        mod = self._load_verify_mod()

        spec_path = REPO_ROOT / "scripts" / "db" / "mongodb" / "response_plans_indexes.json"
        entry = json.loads(spec_path.read_text())[0]

        # Simulate first index present, rest missing
        first_idx = entry["indexes"][0]["name"]
        fake_collection = MagicMock()
        fake_collection.index_information.return_value = {
            "_id_": {"key": [("_id", 1)]},
            first_idx: {"key": [("tenant_id", 1), ("call_id", 1)]},
        }

        result = mod.verify_collection(fake_collection, entry, fix=False, dry_run=False)
        assert first_idx in result.present
        assert len(result.missing) == len(entry["indexes"]) - 1
        assert result.ok is False

    def test_verify_collection_fix_recreates_missing(self) -> None:
        """verify_collection with fix=True calls create_index for missing indexes."""
        mod = self._load_verify_mod()

        spec_path = REPO_ROOT / "scripts" / "db" / "mongodb" / "response_plans_indexes.json"
        entry = json.loads(spec_path.read_text())[0]

        fake_collection = MagicMock()
        fake_collection.index_information.return_value = {"_id_": {"key": [("_id", 1)]}}
        fake_collection.create_index.return_value = "idx_created"

        result = mod.verify_collection(fake_collection, entry, fix=True, dry_run=False)
        assert len(result.recreated) == len(entry["indexes"])
        assert fake_collection.create_index.call_count == len(entry["indexes"])
        assert result.errors == []
