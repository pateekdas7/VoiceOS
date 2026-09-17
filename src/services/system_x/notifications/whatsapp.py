"""WhatsAppNotifier — sends incident notifications via Twilio WhatsApp API."""
from __future__ import annotations

import logging

import httpx

_log = logging.getLogger("system_x.notifications.whatsapp")

_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


class WhatsAppNotifier:
    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        """
        from_number: Twilio WhatsApp-enabled number in E.164 format, e.g. +14155238886
        """
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number

    async def send(self, to_number: str, body: str) -> None:
        """Send a WhatsApp message. to_number must be E.164 format."""
        url = f"{_TWILIO_API_BASE}/Accounts/{self._account_sid}/Messages.json"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                auth=(self._account_sid, self._auth_token),
                data={
                    "From": f"whatsapp:{self._from_number}",
                    "To": f"whatsapp:{to_number}",
                    "Body": body,
                },
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Twilio error {resp.status_code}: {resp.text[:200]}")
        _log.info("whatsapp sent to=%s", to_number)


__all__ = ["WhatsAppNotifier"]
