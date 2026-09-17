"""Gate 3D P1 + P3 — /voice ANI→customer_id resolution deterministic tests.

Verifies that the /voice HTTP handler uses the authoritative
``CustomerService.find_by_phone`` lookup for identity resolution, embeds
the resolved customer_id in TwiML as a <Parameter/>, and never fabricates
identity for callers with no CRM match. Also verifies the outbound-api
direction correctly uses the ``To`` field (DNIS) instead of ``From``.

Invariants under test (Gate 3D P3 checklist):
  * Known caller (inbound) → customer_id appears in TwiML.
  * Known callee (outbound-api) → customer_id appears in TwiML,
    lookup used the ``To`` field, not ``From``.
  * Unknown caller (no CRM match) → NO customer_id parameter in TwiML.
  * Mismatched-tenant caller → NO customer_id parameter (repository
    scopes by tenant_id, so wrong tenant returns None).
  * No CustomerService configured (customer_service=None) → NO
    customer_id parameter; call still admitted.
  * CRM lookup exception → NO customer_id parameter; call still admitted
    (fail-open on the customer_id side, fail-closed downstream to
    clarify_no_record — never fabricates a balance).
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from src.libs.contracts.streaming import AudioClause, WordHypothesis
from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.media_gateway.admission import AdmissionRegistry
from src.services.media_gateway.service import MediaGatewayService
from src.services.media_gateway.twilio_ws_entrypoint import (
    SharedCallDependencies,
    create_twilio_media_stream_app,
)

# Reuse the exact signing + helper machinery from the admission suite so
# any change to Twilio signature semantics is caught in one place.
from tests.unit.services.test_twilio_admission import (
    _ACCOUNT_SID,
    _PUBLIC_HTTP_BASE,
    _PUBLIC_WS_BASE,
    _default_call_params,
    _post_voice,
)


class _FakeCustomer:
    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id


def _make_app_with_customer_service(customer_service: object | None) -> tuple[TestClient, SharedCallDependencies]:
    """Same shape as the admission suite's _make_app but takes an explicit
    customer_service so we can wire whichever behavior the test needs."""
    conversation_engine = MagicMock()
    clause = AudioClause(
        audio_data=b"\x00\x00" * 960, sample_rate=24000, text="ok", clause_index=0, is_final=True,
    )
    conversation_engine.handle_turn = AsyncMock(return_value=[clause])
    conversation_engine.build_greeting = MagicMock(return_value=None)

    stt_service = MagicMock()

    async def _fake_words(*_a, **_kw) -> AsyncIterator[WordHypothesis]:
        yield WordHypothesis(word="hi", confidence=0.9, start_ms=0, end_ms=100, is_final=True)

    stt_service.transcribe_stream = AsyncMock(side_effect=lambda *a, **kw: _fake_words())

    deps = SharedCallDependencies(
        account_sid=_ACCOUNT_SID,
        auth_token="test-auth-token",
        tenant_id="tenant-1",
        media_gateway_service=MediaGatewayService(),
        audio_session_manager_service=AudioSessionManagerService(),
        audio_preprocessor=AudioPreprocessorService(enabled_stages={"resample"}),
        stt_service=stt_service,
        conversation_engine=conversation_engine,
        customer_service=customer_service,
        admission_registry=AdmissionRegistry(),
        public_ws_base_url=_PUBLIC_WS_BASE,
    )
    app = create_twilio_media_stream_app(deps)
    return TestClient(app), deps


_CUST_PARAM_RE = re.compile(r'name="customer_id"\s+value="([^"]+)"')


def _extract_customer_id(twiml: str) -> str | None:
    m = _CUST_PARAM_RE.search(twiml)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# P3.1 — Inbound: known caller → customer_id resolved from ``From``
# ---------------------------------------------------------------------------


def test_inbound_known_caller_emits_customer_id_from_ANI() -> None:
    svc = MagicMock()
    svc.find_by_phone = MagicMock(return_value=_FakeCustomer("cust-inbound-1"))

    client, _ = _make_app_with_customer_service(svc)
    params = _default_call_params("CAinbound1")
    params["Direction"] = "inbound"
    params["From"] = "+919911954448"     # caller
    params["To"]   = "+18008675309"      # our DID

    resp = _post_voice(client, params)
    assert resp.status_code == 200, resp.text
    assert _extract_customer_id(resp.text) == "cust-inbound-1"

    # Lookup used From (ANI) for inbound, NOT To.
    call = svc.find_by_phone.call_args
    assert call is not None
    _tenant_id, phone = call.args[0], call.args[1]
    assert phone == "+919911954448"


# ---------------------------------------------------------------------------
# P3.2 — Outbound-api: known callee → customer_id resolved from ``To``
# ---------------------------------------------------------------------------


def test_outbound_api_known_callee_emits_customer_id_from_DNIS() -> None:
    svc = MagicMock()
    svc.find_by_phone = MagicMock(return_value=_FakeCustomer("cust-outbound-9"))

    client, _ = _make_app_with_customer_service(svc)
    params = _default_call_params("CAoutbound9")
    params["Direction"] = "outbound-api"
    params["From"] = "+18008675309"      # our DID
    params["To"]   = "+919911954448"     # customer being dialed

    resp = _post_voice(client, params)
    assert resp.status_code == 200, resp.text
    assert _extract_customer_id(resp.text) == "cust-outbound-9"

    # Lookup used To (DNIS) for outbound-api, NOT From (our own DID).
    _tenant_id, phone = svc.find_by_phone.call_args.args
    assert phone == "+919911954448"


# ---------------------------------------------------------------------------
# P3.3 — Unknown caller (CRM returns None) → NO customer_id parameter
# ---------------------------------------------------------------------------


def test_unknown_caller_omits_customer_id_parameter() -> None:
    svc = MagicMock()
    svc.find_by_phone = MagicMock(return_value=None)

    client, _ = _make_app_with_customer_service(svc)
    params = _default_call_params("CAunknown")
    params["Direction"] = "inbound"
    params["From"] = "+911111111111"

    resp = _post_voice(client, params)
    assert resp.status_code == 200
    # No fabricated identity: TwiML must not carry a customer_id.
    assert _extract_customer_id(resp.text) is None
    # But admission still succeeded so the call is admitted and can
    # render clarify_no_record via WSS.
    assert 'name="admission_token"' in resp.text


# ---------------------------------------------------------------------------
# P3.4 — CustomerService not wired (test/dev deployments) → skip lookup
# ---------------------------------------------------------------------------


def test_no_customer_service_configured_omits_customer_id_parameter() -> None:
    client, _ = _make_app_with_customer_service(customer_service=None)
    params = _default_call_params("CAnosvc")
    params["Direction"] = "inbound"
    params["From"] = "+919911954448"

    resp = _post_voice(client, params)
    assert resp.status_code == 200
    assert _extract_customer_id(resp.text) is None
    assert 'name="admission_token"' in resp.text


# ---------------------------------------------------------------------------
# P3.5 — CRM lookup raises → call still admitted, no customer_id
# ---------------------------------------------------------------------------


def test_crm_lookup_exception_does_not_fail_call_admission() -> None:
    svc = MagicMock()
    svc.find_by_phone = MagicMock(side_effect=RuntimeError("db down"))

    client, _ = _make_app_with_customer_service(svc)
    params = _default_call_params("CAcrmerr")
    params["Direction"] = "inbound"
    params["From"] = "+919911954448"

    resp = _post_voice(client, params)
    # Admission must not break because CRM is degraded — WSS falls
    # through to clarify_no_record. Balance is never fabricated.
    assert resp.status_code == 200
    assert _extract_customer_id(resp.text) is None
    assert 'name="admission_token"' in resp.text


# ---------------------------------------------------------------------------
# P3.6 — Tenant scoping: lookup is invoked with SharedCallDependencies.tenant_id
# ---------------------------------------------------------------------------


def test_lookup_uses_configured_tenant_id() -> None:
    svc = MagicMock()
    svc.find_by_phone = MagicMock(return_value=None)

    client, deps = _make_app_with_customer_service(svc)
    params = _default_call_params("CAtenantscoped")
    params["Direction"] = "inbound"
    params["From"] = "+919911954448"

    _resp = _post_voice(client, params)
    tenant_id_arg = svc.find_by_phone.call_args.args[0]
    # Repository scopes by TenantId; whatever type it is, its string form
    # must equal the shared deps' configured tenant_id.
    assert str(tenant_id_arg) == deps.tenant_id


# ---------------------------------------------------------------------------
# P3.7 — Empty From/To (defensive) → skip lookup, admit call, no fabrication
# ---------------------------------------------------------------------------


def test_empty_phone_skips_lookup_and_admits_call() -> None:
    svc = MagicMock()
    svc.find_by_phone = MagicMock()

    client, _ = _make_app_with_customer_service(svc)
    params = _default_call_params("CAemptyphone")
    params["Direction"] = "inbound"
    params["From"] = ""    # nothing to look up
    params["To"] = ""

    resp = _post_voice(client, params)
    assert resp.status_code == 200
    assert _extract_customer_id(resp.text) is None
    # Lookup must NOT be attempted for an empty phone — we don't want to
    # tie up the CRM on nothing, and there is no plausible match anyway.
    svc.find_by_phone.assert_not_called()
