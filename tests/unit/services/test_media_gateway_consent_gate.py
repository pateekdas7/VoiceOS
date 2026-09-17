"""Consent-revocation enforcement at the WebSocket-open boundary (NEEDS-WORK #9).

Two-part test:

1. The consent port module itself (``NullCustomerConsent`` + Protocol
   conformance + metric wiring). These are direct-import unit tests —
   no numpy/opentelemetry chain required.

2. A source-level tripwire for ``twilio_ws_entrypoint.py`` that asserts
   the consent-revoked check is wired *before* ``CallOrchestrator.create``
   and *before* ``start_call``. Source-level because the entrypoint's
   runtime import chain pulls in numpy (audio_preprocessing) which is
   not installable in the local test env, and because the wiring itself
   is a construction-time contract — if any refactor moves the consent
   check to after the greeting starts (or drops it entirely), that call
   silently loses the last defense-in-depth gate against DPDP-violating
   audio being played to a revoked customer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.media_gateway import metrics as _m
from src.services.media_gateway.consent_gate import (
    CustomerConsentPort,
    NullCustomerConsent,
)

_ENTRYPOINT = (
    Path(__file__).resolve().parents[3]
    / "src" / "services" / "media_gateway" / "twilio_ws_entrypoint.py"
)


class TestNullCustomerConsent:
    def test_never_revoked(self) -> None:
        gate = NullCustomerConsent()
        assert gate.is_revoked("t-001", "cust-1") is False
        assert gate.is_revoked("", "") is False

    def test_conforms_to_port(self) -> None:
        assert isinstance(NullCustomerConsent(), CustomerConsentPort)


class TestCustomConsentPort:
    def test_arbitrary_impl_satisfies_port(self) -> None:
        class _AlwaysRevoked:
            def is_revoked(self, tenant_id: str, customer_id: str) -> bool:
                return True

        assert isinstance(_AlwaysRevoked(), CustomerConsentPort)
        assert _AlwaysRevoked().is_revoked("t", "c") is True


class TestConsentBlockMetric:
    def test_counter_exists_with_expected_name(self) -> None:
        """Metric name is part of the alerting contract — Grafana panels
        and PromQL rules will reference this exact string."""
        assert _m.CALLS_BLOCKED_CONSENT_REVOKED._name == (
            "voiceos_media_gateway_calls_blocked_consent_revoked"
        )

    def test_recorder_increments_labelled_counter(self) -> None:
        tenant = "t-consent-test"
        before = _m.CALLS_BLOCKED_CONSENT_REVOKED.labels(tenant_id=tenant)._value.get()
        _m.record_call_blocked_consent_revoked(tenant)
        _m.record_call_blocked_consent_revoked(tenant)
        after = _m.CALLS_BLOCKED_CONSENT_REVOKED.labels(tenant_id=tenant)._value.get()
        assert after - before == 2


class TestEntrypointWiring:
    """Source-level tripwire — the entrypoint is not importable in the
    local test env (numpy chain), but the wiring is fully expressible
    in source-string assertions."""

    def _src(self) -> str:
        return _ENTRYPOINT.read_text(encoding="utf-8")

    def test_consent_gate_field_declared_on_shared_deps(self) -> None:
        src = self._src()
        assert "consent_gate:" in src, (
            "SharedCallDependencies must expose ``consent_gate`` so a real "
            "Postgres-backed adapter can be wired at process startup."
        )

    def test_consent_check_runs_before_orchestrator_create(self) -> None:
        """If the check happens after CallOrchestrator.create() then session
        allocation, adapter connect, and greeting synthesis all run for a
        customer whose consent is revoked — that is the exact failure this
        gate exists to prevent."""
        src = self._src()
        check_pos = src.index("is_revoked(deps.tenant_id, customer_id)")
        create_pos = src.index("await CallOrchestrator.create(")
        assert check_pos < create_pos, (
            "Consent revocation check must precede CallOrchestrator.create() — "
            "otherwise a revoked customer still triggers session allocation."
        )

    def test_consent_check_runs_before_start_call(self) -> None:
        """CRM start_call() spins up per-call state and emits an audit event.
        Revoked customers must not generate that trail — the check has to
        happen before start_call() is invoked."""
        src = self._src()
        check_pos = src.index("is_revoked(deps.tenant_id, customer_id)")
        start_call_pos = src.index("conversation_engine.start_call(")
        assert check_pos < start_call_pos, (
            "Consent revocation check must precede conversation_engine.start_call() — "
            "otherwise a revoked customer generates CRM/audit trails they shouldn't."
        )

    def test_revoked_customer_closes_websocket_4003(self) -> None:
        """The rejection path must actually terminate the WebSocket. 4003
        is the same code used for auth rejection (AR-2) — reusing it keeps
        Twilio's error handling identical for both compliance paths."""
        src = self._src()
        # Search only within the consent block for websocket.close(code=4003)
        block_start = src.index("if consent_gate is None:")
        block_end = src.index("context = None", block_start)
        block = src[block_start:block_end]
        assert "websocket.close(code=4003)" in block, (
            "Consent revocation must close the WebSocket with code=4003."
        )
        assert "return" in block, (
            "Consent revocation must return before the greeting starts."
        )

    def test_revocation_increments_metric(self) -> None:
        src = self._src()
        block_start = src.index("if consent_gate is None:")
        block_end = src.index("context = None", block_start)
        block = src[block_start:block_end]
        assert "record_call_blocked_consent_revoked" in block, (
            "Revocation path must increment CALLS_BLOCKED_CONSENT_REVOKED — "
            "otherwise ops has no signal when an upstream consent surface "
            "flips every customer to REVOKED."
        )

    def test_consent_gate_defaults_to_null_when_unwired(self) -> None:
        """A deployment without a consent adapter wired must not fail-closed
        (would take every call down) — NullCustomerConsent is the sentinel."""
        src = self._src()
        assert "NullCustomerConsent" in src

    def test_consent_lookup_failure_is_fail_open(self) -> None:
        """If a real Postgres-backed adapter throws, the call must still
        connect — the phone-DND at dial time and schedule-time DND port
        remain in force, so a broken consent adapter degrades gracefully
        rather than blocking the whole tenant."""
        src = self._src()
        block_start = src.index("if consent_gate is None:")
        block_end = src.index("context = None", block_start)
        block = src[block_start:block_end]
        assert "except Exception" in block, (
            "consent_gate lookup must be wrapped in try/except so a broken "
            "adapter doesn't take every call down (fail-open, documented)."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
