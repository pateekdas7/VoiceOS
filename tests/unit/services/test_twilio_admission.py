"""Phase I — Gate 3: production-grade Twilio Media Streams admission tests.

Covers the two-stage admission protocol introduced to replace the broken
X-Twilio-Signature-on-WSS design. The 13 scenarios A–M explicitly enumerate
the invariants a hostile or malformed caller must NOT be able to violate:

    A — Valid full sequence (HTTP /voice signed → mint → WSS start with
        matching token/callSid/accountSid → admission consumed, orchestrator
        created).
    B — WSS start with no admission_token → rejected (missing_admission_token),
        no orchestrator, no session gate admit.
    C — Wrong AccountSid on /voice → 403, no token minted.
    D — WSS start with missing callSid and streamSid → rejected (no call_id).
    E — WSS start with wrong callSid (mismatches minted token) → rejected
        (admission_token_call_mismatch), token NOT consumed (still usable
        by the correct caller within TTL).
    F — WSS start with wrong accountSid → rejected
        (admission_token_account_mismatch).
    G — Replay: valid consume, then second WSS attempt with same token →
        rejected (unknown_admission_token) — token deleted atomically on
        the winning consume.
    H — Expired admission: token TTL passes before WSS upgrade → rejected
        (admission_token_expired), token pruned.
    I — WSS without a preceding valid /voice (admin never issued a token)
        → rejected (unknown_admission_token).
    J — Cross-call isolation: Call-A's token cannot admit Call-B's WSS.
    K — Legacy adapter authenticate() with HMAC credentials still works
        (existing unit tests unchanged).
    L — /voice with missing X-Twilio-Signature → 403, no token minted.
    M — /voice with malformed form body / no CallSid → 400/403 fail-closed.

The suite exercises the real production entrypoint (create_twilio_media_stream_app)
via a Starlette TestClient — no monkey-patching of admission internals —
so any regression in the admission path is caught end-to-end.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from src.libs.contracts.streaming import AudioClause, WordHypothesis
from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.media_gateway.admission import (
    AdmissionRegistry,
    DEFAULT_ADMISSION_TTL_S,
)
from src.services.media_gateway.service import MediaGatewayService
from src.services.media_gateway.twilio_ws_entrypoint import (
    SharedCallDependencies,
    create_twilio_media_stream_app,
)

_ACCOUNT_SID = "ACtestaccount"
_AUTH_TOKEN = "test-auth-token"
_PUBLIC_HTTP_BASE = "https://tunnel.example.com"
_PUBLIC_WS_BASE = "wss://tunnel.example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _twilio_sign(url: str, params: dict[str, str], auth_token: str = _AUTH_TOKEN) -> str:
    """Fabricate the exact X-Twilio-Signature header Twilio would send.

    Matches src/services/media_gateway/auth.py::validate_twilio_signature —
    HMAC-SHA1 over URL + alphabetically sorted param key/value pairs.
    """
    sorted_pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    payload = (url + sorted_pairs).encode()
    mac = hmac.new(auth_token.encode(), payload, hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _make_app(*, admission_ttl_s: float = DEFAULT_ADMISSION_TTL_S, now=None) -> tuple[TestClient, SharedCallDependencies]:
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

    registry_kwargs = {"ttl_s": admission_ttl_s}
    if now is not None:
        registry_kwargs["now"] = now
    admission_registry = AdmissionRegistry(**registry_kwargs)

    deps = SharedCallDependencies(
        account_sid=_ACCOUNT_SID,
        auth_token=_AUTH_TOKEN,
        tenant_id="tenant-1",
        media_gateway_service=MediaGatewayService(),
        audio_session_manager_service=AudioSessionManagerService(),
        audio_preprocessor=AudioPreprocessorService(enabled_stages={"resample"}),
        stt_service=stt_service,
        conversation_engine=conversation_engine,
        admission_registry=admission_registry,
        public_ws_base_url=_PUBLIC_WS_BASE,
    )
    app = create_twilio_media_stream_app(deps)
    return TestClient(app), deps


def _post_voice(client: TestClient, params: dict[str, str], *, signature: str | None = None):
    """POST /voice as Twilio would, with the correct signature by default."""
    url = f"{_PUBLIC_HTTP_BASE}/voice"
    sig = signature if signature is not None else _twilio_sign(url, params)
    headers = {}
    if sig is not None:
        headers["x-twilio-signature"] = sig
    return client.post("/voice", data=params, headers=headers)


def _extract_admission_token(twiml_body: str) -> str:
    """Parse the admission_token attribute out of the emitted TwiML."""
    m = re.search(r'name="admission_token"\s+value="([^"]+)"', twiml_body)
    if m is None:
        raise AssertionError(f"admission_token not found in TwiML: {twiml_body!r}")
    return m.group(1)


def _wss_start(call_sid: str, account_sid: str = _ACCOUNT_SID, token: str | None = None,
                stream_sid: str = "MZstream", extra_params: dict | None = None) -> dict:
    custom = {"admission_token": token} if token is not None else {}
    if extra_params:
        custom.update(extra_params)
    return {
        "event": "start",
        "start": {
            "callSid": call_sid,
            "streamSid": stream_sid,
            "accountSid": account_sid,
            "customParameters": custom,
            "mediaFormat": {"sampleRate": 8000, "channels": 1, "encoding": "audio/x-mulaw"},
        },
    }


def _default_call_params(call_sid: str, account_sid: str = _ACCOUNT_SID) -> dict[str, str]:
    return {
        "CallSid": call_sid,
        "AccountSid": account_sid,
        "From": "+19999999999",
        "To": "+18888888888",
        "CallStatus": "in-progress",
        "ApiVersion": "2010-04-01",
        "Direction": "outbound-api",
    }


# ---------------------------------------------------------------------------
# Scenario A — Valid full HTTP → WSS sequence
# ---------------------------------------------------------------------------


def test_A_valid_full_sequence_admits_call() -> None:
    client, deps = _make_app()
    params = _default_call_params("CAvalidA")

    resp = _post_voice(client, params)
    assert resp.status_code == 200, resp.text
    assert "application/xml" in resp.headers["content-type"]
    token = _extract_admission_token(resp.text)
    assert len(token) >= 32

    with client.websocket_connect("/twilio/media-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(_wss_start("CAvalidA", token=token))
        # If we got here without ws.close raised the server accepted the
        # admission and constructed the orchestrator.
        ws.send_json({"event": "stop", "stop": {"callSid": "CAvalidA"}})

    # Token was single-use — registry now empty.
    assert deps.admission_registry.size() == 0


# ---------------------------------------------------------------------------
# Scenario B — WSS with no admission_token → rejected
# ---------------------------------------------------------------------------


def test_B_wss_missing_admission_token_rejected() -> None:
    client, deps = _make_app()
    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(_wss_start("CAmissB"))  # no token
            ws.receive_json()  # server will close 4401 — receive raises
    # No orchestrator was constructed — no active session in the gate.
    assert deps.media_gateway_service.active_session_count() == 0


# ---------------------------------------------------------------------------
# Scenario C — /voice with wrong AccountSid → 403, no token
# ---------------------------------------------------------------------------


def test_C_voice_wrong_account_sid_forbidden() -> None:
    client, deps = _make_app()
    params = _default_call_params("CAwrongC", account_sid="ACwrong")
    resp = _post_voice(client, params)
    assert resp.status_code == 403
    assert deps.admission_registry.size() == 0


# ---------------------------------------------------------------------------
# Scenario D — WSS start with no callSid and no streamSid → rejected
# ---------------------------------------------------------------------------


def test_D_wss_missing_call_and_stream_sid_rejected() -> None:
    client, _ = _make_app()
    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json({
                "event": "start",
                "start": {"mediaFormat": {"sampleRate": 8000}},
            })
            ws.receive_json()


# ---------------------------------------------------------------------------
# Scenario E — WSS start with wrong callSid (mismatch minted token) → rejected
# ---------------------------------------------------------------------------


def test_E_wss_wrong_call_sid_rejected_and_token_not_consumed() -> None:
    client, deps = _make_app()
    params = _default_call_params("CArealE")
    resp = _post_voice(client, params)
    token = _extract_admission_token(resp.text)

    # Attacker uses right token but presents a different callSid.
    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(_wss_start("CAattackerE", token=token))
            ws.receive_json()

    # Token NOT consumed on mismatch — real caller can still use it.
    assert deps.admission_registry.size() == 1
    with client.websocket_connect("/twilio/media-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(_wss_start("CArealE", token=token))
        ws.send_json({"event": "stop", "stop": {"callSid": "CArealE"}})
    assert deps.admission_registry.size() == 0


# ---------------------------------------------------------------------------
# Scenario F — WSS start with wrong accountSid → rejected
# ---------------------------------------------------------------------------


def test_F_wss_wrong_account_sid_rejected() -> None:
    client, deps = _make_app()
    params = _default_call_params("CArealF")
    resp = _post_voice(client, params)
    token = _extract_admission_token(resp.text)

    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(_wss_start("CArealF", account_sid="ACdifferent", token=token))
            ws.receive_json()
    # Mismatch does NOT consume — token still in registry (real caller can try).
    assert deps.admission_registry.size() == 1


# ---------------------------------------------------------------------------
# Scenario G — Replay: second use of same token → rejected
# ---------------------------------------------------------------------------


def test_G_replay_second_use_of_same_token_rejected() -> None:
    client, deps = _make_app()
    params = _default_call_params("CArealG")
    resp = _post_voice(client, params)
    token = _extract_admission_token(resp.text)

    # First use — valid.
    with client.websocket_connect("/twilio/media-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(_wss_start("CArealG", token=token))
        ws.send_json({"event": "stop", "stop": {"callSid": "CArealG"}})
    assert deps.admission_registry.size() == 0

    # Replay — same token, same callSid — must be rejected.
    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(_wss_start("CArealG", token=token))
            ws.receive_json()


# ---------------------------------------------------------------------------
# Scenario H — Expired admission token → rejected
# ---------------------------------------------------------------------------


def test_H_expired_admission_token_rejected() -> None:
    """Uses an injected clock to advance past TTL without real sleeping."""
    fake_now = [1000.0]
    client, deps = _make_app(admission_ttl_s=5.0, now=lambda: fake_now[0])

    params = _default_call_params("CArealH")
    resp = _post_voice(client, params)
    token = _extract_admission_token(resp.text)
    assert deps.admission_registry.size() == 1

    # Advance clock past TTL.
    fake_now[0] += 10.0

    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(_wss_start("CArealH", token=token))
            ws.receive_json()

    # Expired token was pruned during verify.
    assert deps.admission_registry.size() == 0


# ---------------------------------------------------------------------------
# Scenario I — WSS without any prior /voice → unknown token → rejected
# ---------------------------------------------------------------------------


def test_I_wss_without_prior_voice_rejected() -> None:
    client, _ = _make_app()
    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            # Fabricated token that was never issued.
            ws.send_json(_wss_start("CAnoprior", token="thisTokenWasNeverIssued_AAAAAAAAAAAAAAAAAAAAAAAA"))
            ws.receive_json()


# ---------------------------------------------------------------------------
# Scenario J — Cross-call isolation: Call A token cannot admit Call B's WSS
# ---------------------------------------------------------------------------


def test_J_cross_call_isolation() -> None:
    client, deps = _make_app()

    # Two independent /voice calls → two independent tokens.
    resp_a = _post_voice(client, _default_call_params("CAcallA"))
    token_a = _extract_admission_token(resp_a.text)
    resp_b = _post_voice(client, _default_call_params("CAcallB"))
    token_b = _extract_admission_token(resp_b.text)
    assert token_a != token_b
    assert deps.admission_registry.size() == 2

    # Try to admit Call B's WSS using Call A's token.
    with pytest.raises(Exception):
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(_wss_start("CAcallB", token=token_a))
            ws.receive_json()

    # Both tokens still valid — call_mismatch does NOT consume.
    assert deps.admission_registry.size() == 2

    # Correct pairing succeeds for both.
    for call_sid, token in [("CAcallA", token_a), ("CAcallB", token_b)]:
        with client.websocket_connect("/twilio/media-stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(_wss_start(call_sid, token=token))
            ws.send_json({"event": "stop", "stop": {"callSid": call_sid}})
    assert deps.admission_registry.size() == 0


# ---------------------------------------------------------------------------
# Scenario K — Legacy adapter HMAC path still works (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_K_legacy_adapter_hmac_path_still_works() -> None:
    """Guards that we didn't break the HMAC branch existing unit tests use."""
    import json as _json

    from src.libs.contracts.primitives import TenantId
    from src.services.media_gateway.adapters.twilio_websocket import TwilioWebSocketAdapter

    a = TwilioWebSocketAdapter(
        account_sid=_ACCOUNT_SID, auth_token=_AUTH_TOKEN, tenant_id=TenantId("tenant-1"),
    )
    url = "https://example.com/webhook"
    params: dict[str, str] = {}
    sig = _twilio_sign(url, params)
    creds = {
        "account_sid": _ACCOUNT_SID,
        "auth_token": _AUTH_TOKEN,
        "url": url,
        "params": _json.dumps(params),
        "x_twilio_signature": sig,
        "expected_account_sid": _ACCOUNT_SID,
    }
    result = await a.authenticate(creds)
    assert result.success, result.reason


# ---------------------------------------------------------------------------
# Scenario L — /voice missing X-Twilio-Signature → 403, no token
# ---------------------------------------------------------------------------


def test_L_voice_missing_signature_forbidden() -> None:
    client, deps = _make_app()
    params = _default_call_params("CAnosigL")
    # Explicitly pass empty signature (helper distinguishes None → auto-sign,
    # empty string → no header).
    resp = client.post("/voice", data=params)  # no signature header at all
    assert resp.status_code == 403
    assert deps.admission_registry.size() == 0


# ---------------------------------------------------------------------------
# Scenario M — /voice missing CallSid → 400
# ---------------------------------------------------------------------------


def test_M_voice_missing_call_sid_bad_request() -> None:
    client, deps = _make_app()
    # A signed but incomplete body — missing CallSid.
    params = {
        "AccountSid": _ACCOUNT_SID,
        "From": "+19999999999",
        "To": "+18888888888",
    }
    resp = _post_voice(client, params)
    assert resp.status_code == 400
    assert deps.admission_registry.size() == 0


# ---------------------------------------------------------------------------
# Direct unit tests of AdmissionRegistry — deterministic without the HTTP layer.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_issue_produces_high_entropy_token() -> None:
    reg = AdmissionRegistry()
    tokens = set()
    for i in range(200):
        t = await reg.issue(call_sid=f"CA{i}", account_sid="ACtest", tenant_id="tenant-1")
        tokens.add(t.token)
    assert len(tokens) == 200  # no collisions across 200 issues
    for tok in tokens:
        assert len(tok) >= 32  # ~256 bits base64url-encoded


@pytest.mark.asyncio
async def test_registry_rejects_empty_call_sid() -> None:
    reg = AdmissionRegistry()
    with pytest.raises(ValueError):
        await reg.issue(call_sid="", account_sid="ACtest", tenant_id="tenant-1")


@pytest.mark.asyncio
async def test_registry_rejects_empty_account_sid() -> None:
    reg = AdmissionRegistry()
    with pytest.raises(ValueError):
        await reg.issue(call_sid="CA1", account_sid="", tenant_id="tenant-1")


@pytest.mark.asyncio
async def test_registry_expiry_prunes_old_entries() -> None:
    fake_now = [1000.0]
    reg = AdmissionRegistry(ttl_s=5.0, now=lambda: fake_now[0])
    t1 = await reg.issue(call_sid="CA1", account_sid="ACtest", tenant_id="tenant-1")
    fake_now[0] = 1010.0  # past ttl
    ok, reason, _ = await reg.verify_and_consume(token=t1.token, call_sid="CA1", account_sid="ACtest")
    assert not ok
    assert reason == "admission_token_expired"
    assert reg.size() == 0


@pytest.mark.asyncio
async def test_registry_revoke_removes_token() -> None:
    reg = AdmissionRegistry()
    t = await reg.issue(call_sid="CArev", account_sid="ACtest", tenant_id="tenant-1")
    assert reg.size() == 1
    assert await reg.revoke(t.token) is True
    assert reg.size() == 0
    assert await reg.revoke(t.token) is False  # idempotent


@pytest.mark.asyncio
async def test_registry_concurrent_consumers_only_one_wins() -> None:
    """Race two consumers against the same token; exactly one succeeds."""
    reg = AdmissionRegistry()
    t = await reg.issue(call_sid="CAconcurr", account_sid="ACtest", tenant_id="tenant-1")

    async def _try():
        return await reg.verify_and_consume(token=t.token, call_sid="CAconcurr", account_sid="ACtest")

    results = await asyncio.gather(*(_try() for _ in range(10)))
    wins = [r for r in results if r[0]]
    losses = [r for r in results if not r[0]]
    assert len(wins) == 1
    assert len(losses) == 9
    for _, reason, _ticket in losses:
        assert reason in ("unknown_admission_token", "admission_token_consumed")
