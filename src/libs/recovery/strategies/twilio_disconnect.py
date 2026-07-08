"""TwilioDisconnectStrategy — 30s reconnect window, then close the session.

A Twilio Media Streams WebSocket disconnect (network blip, client restart)
gets a bounded grace period to reconnect before the call session is closed
outright — avoids both prematurely dropping a recoverable call and holding
a dead session open indefinitely.

Architecture: V3 Ch7 (Crash Recovery — Twilio disconnect class).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from src.libs.contracts.primitives import CallId

from ..outcome import RecoveryOutcome

DEFAULT_RECONNECT_TIMEOUT_SECONDS = 30.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class TwilioDisconnectStrategy:
    """Waits a bounded window for Twilio reconnection before closing the call."""

    strategy_name = "twilio_disconnect"

    def __init__(
        self,
        *,
        reconnect_timeout_seconds: float = DEFAULT_RECONNECT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._reconnect_timeout_seconds = reconnect_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._sleep_fn = sleep_fn

    async def recover(
        self,
        call_id: CallId,
        reconnect_check: Callable[[], Awaitable[bool]],
    ) -> RecoveryOutcome:
        """Poll ``reconnect_check`` for up to the reconnect window.

        Args:
            call_id: The disconnected call's session id.
            reconnect_check: Async callable returning True once the Twilio
                WebSocket has re-established for this call.

        Returns:
            A successful RecoveryOutcome if reconnection happened within the
            window; an unsuccessful one (session must be closed) otherwise.
        """
        deadline = time.monotonic() + self._reconnect_timeout_seconds
        while time.monotonic() < deadline:
            if await reconnect_check():
                return RecoveryOutcome(success=True, detail={"call_id": call_id, "reconnected": True})
            await self._sleep_fn(self._poll_interval_seconds)
        return RecoveryOutcome(
            success=False,
            detail={"call_id": call_id, "reconnected": False, "action": "session_closed"},
        )
