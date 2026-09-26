"""TwilioOutboundCallService — places outbound calls via Twilio REST API.

Uses httpx (same library as WhatsAppNotifier) with Basic auth
(account_sid, auth_token). Returns the Twilio call SID on success so
the DialerEngine can track call lifecycle via the status callback.

Architecture: V5 Ch6 (Campaign Engine — Dialer); V1 Ch26 (Twilio).
"""

from __future__ import annotations

import logging

import httpx

_log = logging.getLogger("voiceos.dialer.outbound_call")

_TWILIO_BASE = "https://api.twilio.com/2010-04-01"


class CallPlacementError(RuntimeError):
    """Raised when Twilio rejects or fails to place an outbound call."""


class TwilioOutboundCallService:
    """Places outbound PSTN calls through Twilio's REST Calls API.

    Args:
        account_sid:  Twilio Account SID (AC…).
        auth_token:   Twilio Auth Token.
        caller_id:    E.164 Twilio number to call from (+91…, +1…).
        twiml_app_url: URL Twilio GETs to retrieve the TwiML for the call.
                       Must be HTTPS-reachable by Twilio's infrastructure.
                       In production this is the CPU node's
                       /twilio/twiml-connect endpoint or a pre-recorded
                       TwiML Bin URL.
        status_callback_url: HTTPS URL Twilio POSTs call-status events to
                             (queued → ringing → in-progress → completed/…).
        timeout_s:    httpx request timeout (Twilio usually responds in <2s).
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        caller_id: str,
        twiml_app_url: str,
        status_callback_url: str,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._caller_id = caller_id
        self._twiml_app_url = twiml_app_url
        self._status_callback_url = status_callback_url
        self._timeout = timeout_s

    async def place_call(
        self,
        to_number: str,
        *,
        lead_id: str,
        campaign_id: str,
        tenant_id: str,
    ) -> str:
        """Place an outbound call. Returns the Twilio call SID on success.

        Raises:
            CallPlacementError: on any Twilio 4xx/5xx or network failure.
        """
        url = f"{_TWILIO_BASE}/Accounts/{self._account_sid}/Calls.json"
        payload = {
            "From": self._caller_id,
            "To": to_number,
            "Url": self._twiml_app_url,
            "StatusCallback": self._status_callback_url,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": "initiated ringing answered completed",
            # Pass context through so the status callback can update the
            # correct lead record without a separate Redis lookup round-trip.
            "StatusCallbackParameter": f"lead_id={lead_id}&campaign_id={campaign_id}&tenant_id={tenant_id}",
            "MachineDetection": "Enable",
            "AsyncAmd": "true",
            "AsyncAmdStatusCallback": self._status_callback_url,
            "Record": "false",
            "Timeout": "30",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url,
                    auth=(self._account_sid, self._auth_token),
                    data=payload,
                )
        except httpx.RequestError as exc:
            raise CallPlacementError(f"network error placing call to {to_number}: {exc}") from exc

        if resp.status_code >= 400:
            raise CallPlacementError(
                f"Twilio {resp.status_code} placing call to {to_number}: {resp.text[:300]}"
            )

        data = resp.json()
        call_sid: str = data["sid"]
        _log.info(
            "call placed to=%s lead_id=%s campaign_id=%s call_sid=%s",
            to_number, lead_id, campaign_id, call_sid,
        )
        return call_sid
