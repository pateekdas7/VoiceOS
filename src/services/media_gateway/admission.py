"""Admission-token registry for Twilio Media Streams WebSocket admission.

Twilio never sends X-Twilio-Signature on the WebSocket handshake for a
Media Streams `<Connect><Stream>` — only on the HTTP `/voice` webhook that
returns the TwiML. Attempting HMAC validation on the WSS upgrade fails
against every real Twilio connection (the header is unconditionally empty)
while unit tests that fabricate the header falsely pass, hiding the defect.

This module implements the second half of a two-stage admission protocol
that treats HTTP and WSS as distinct protocol stages:

  Stage 1 — HTTP POST /voice
      Twilio calls the webhook with an X-Twilio-Signature header covering
      the URL and POST form params (validated in the entrypoint using the
      existing `validate_twilio_signature` primitive). On success the
      entrypoint extracts CallSid + AccountSid from the form body, mints a
      cryptographically-strong single-use admission token bound to
      (call_sid, account_sid, tenant_id) via `AdmissionRegistry.issue`,
      and embeds it in the TwiML response as
      `<Stream><Parameter name="admission_token" value="…"/>`.

  Stage 2 — WSS /twilio/media-stream
      On the `start` message the entrypoint reads `admission_token` out of
      `start.customParameters` and calls `verify_and_consume(...)` — the
      token must exist, not have expired, not have been used, and its
      bound call_sid + account_sid must match those in the `start`
      message. Consumption is atomic under an asyncio.Lock so a replay
      cannot race between two WebSocket connections. Failure closes the
      WebSocket immediately, before any session resource is allocated
      (AR-2 auth-before-allocation preserved).

Security properties:
    * Cryptographic strength: `secrets.token_urlsafe(32)` → ~256 bits of
      entropy, URL-safe (goes through Twilio's TwiML parameter path
      un-mangled).
    * Single-use: consumption is atomic; a replay against the same token
      returns `admission_token_consumed`.
    * TTL-bound: default 60 seconds from issue — the WSS upgrade happens
      seconds after the TwiML response in Twilio's flow, so this is
      comfortable headroom.
    * Call-bound: every ticket is scoped to one CallSid; a token issued
      for Call A cannot admit Call B's WebSocket (cross-call isolation).
    * Account-bound: every ticket is scoped to one AccountSid; a token
      minted under one tenant's Twilio account cannot admit a
      cross-account connection.
    * Fail-closed: every mismatch/absence returns success=False with a
      distinct machine-readable reason; the entrypoint never opens a
      session on a False result.

Architecture: V1 Ch3 (Media Gateway admission);
              V6 Ch4 AR-2 (auth-before-allocation).
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Callable

DEFAULT_ADMISSION_TTL_S: float = 60.0
"""Default admission-token lifetime. Twilio issues the WSS upgrade within
a few seconds of the TwiML response; 60s is comfortable headroom that
still bounds the window in which a leaked token would be usable."""


@dataclass(frozen=True)
class AdmissionTicket:
    """An issued admission-token record.

    Immutable — mutation (consumption) lives on the private wrapper stored
    inside the registry so the public shape stays a stable value type for
    logging, metrics, and tests.
    """

    token: str
    call_sid: str
    account_sid: str
    tenant_id: str
    issued_at: float
    expires_at: float


@dataclass
class _TicketEntry:
    """Registry-internal mutable wrapper around a ticket."""

    ticket: AdmissionTicket
    consumed: bool = False


class AdmissionRegistry:
    """In-memory single-use admission-token registry.

    Every method is safe to call from concurrent asyncio tasks — the
    internal asyncio.Lock serializes all mutations, and reads happen only
    under the same lock so an in-flight consumer cannot observe a token
    mid-consumption.

    Not persisted across process restarts: an admission token minted by a
    previous process is not honored by a fresh process. This is
    intentional — Twilio's WSS upgrade always follows the TwiML response
    on the same node, so cross-process persistence is not required and
    would only enlarge the attack surface. If the process dies between
    minting and WSS-upgrade, Twilio's request-timeout naturally cleans up
    the call attempt.

    The `now` injection point is only used in tests to drive deterministic
    TTL scenarios; production always uses the monotonic default.
    """

    def __init__(
        self,
        ttl_s: float = DEFAULT_ADMISSION_TTL_S,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive, got {ttl_s!r}")
        self._ttl_s = float(ttl_s)
        self._now = now
        self._store: dict[str, _TicketEntry] = {}
        self._lock = asyncio.Lock()

    @property
    def ttl_s(self) -> float:
        """Admission-token lifetime in seconds."""
        return self._ttl_s

    async def issue(
        self,
        *,
        call_sid: str,
        account_sid: str,
        tenant_id: str,
    ) -> AdmissionTicket:
        """Mint a fresh admission token bound to (call_sid, account_sid, tenant_id).

        Called from the HTTP /voice webhook handler after
        `validate_twilio_signature` succeeds and CallSid/AccountSid have
        been extracted from Twilio's POST form body.

        Raises:
            ValueError: When call_sid or account_sid is empty — an
                admission token that isn't call-bound would silently
                admit any subsequent WebSocket, which is exactly the
                defect this module exists to close.

        Returns:
            The issued AdmissionTicket. Callers embed `ticket.token` in
            the TwiML `<Parameter name="admission_token" value="…"/>`.
        """
        if not call_sid:
            raise ValueError("admission issue requires non-empty call_sid")
        if not account_sid:
            raise ValueError("admission issue requires non-empty account_sid")

        token = secrets.token_urlsafe(32)
        issued_at = self._now()
        ticket = AdmissionTicket(
            token=token,
            call_sid=call_sid,
            account_sid=account_sid,
            tenant_id=tenant_id,
            issued_at=issued_at,
            expires_at=issued_at + self._ttl_s,
        )
        async with self._lock:
            self._prune_expired_locked(issued_at)
            self._store[token] = _TicketEntry(ticket=ticket)
        return ticket

    async def verify_and_consume(
        self,
        *,
        token: str,
        call_sid: str,
        account_sid: str,
    ) -> tuple[bool, str, AdmissionTicket | None]:
        """Atomically verify and consume an admission token.

        Every failure mode returns a distinct reason string so operators
        and metrics can distinguish replays from expiry, wrong-call from
        wrong-account, etc. The reason strings are stable and consumed
        by tests + Media Gateway rejection metrics.

        Returns:
            (True, "ok", ticket) on success. The ticket is deleted from
                the store as part of the same atomic step, so a replay
                against the same token returns "unknown_admission_token".
            (False, "missing_admission_token", None) — no token supplied.
            (False, "unknown_admission_token", None) — token never
                issued (or already consumed / expired-and-pruned).
            (False, "admission_token_expired", None) — issued but past
                its expires_at.
            (False, "admission_token_consumed", None) — the atomic
                consume-then-delete step lost a race with another
                consumer; the second caller sees this reason.
            (False, "admission_token_call_mismatch", None) — the WS
                start message's callSid does not match the ticket's.
            (False, "admission_token_account_mismatch", None) — the WS
                start message's accountSid does not match the ticket's.
        """
        if not token:
            return False, "missing_admission_token", None

        now = self._now()
        async with self._lock:
            entry = self._store.get(token)
            if entry is None:
                return False, "unknown_admission_token", None
            if entry.consumed:
                # Defense-in-depth: consumed entries are also deleted from
                # the store on the winning consume, so this branch is
                # only reachable through pathological races that leave a
                # stale entry — still fail closed.
                return False, "admission_token_consumed", None
            if now > entry.ticket.expires_at:
                del self._store[token]
                return False, "admission_token_expired", None
            if entry.ticket.call_sid != call_sid:
                # Do NOT consume on mismatch — the correct caller may
                # still arrive with the same (valid) token within the
                # TTL window. Fail closed for the wrong caller only.
                return False, "admission_token_call_mismatch", None
            if entry.ticket.account_sid != account_sid:
                return False, "admission_token_account_mismatch", None

            entry.consumed = True
            del self._store[token]
            return True, "ok", entry.ticket

    async def revoke(self, token: str) -> bool:
        """Remove a token from the registry unconditionally.

        Used by tests and by administrative teardown flows to invalidate
        an outstanding admission (e.g. when the /voice handler's caller
        gives up before the WSS upgrade). Returns True when a token was
        actually removed.
        """
        async with self._lock:
            return self._store.pop(token, None) is not None

    def size(self) -> int:
        """Current number of outstanding (unconsumed, unexpired) tickets.

        Intentionally synchronous — used from metrics-gauge readers that
        cannot await. Because callers only observe the count (not the
        contents) a snapshot without the lock is acceptable.
        """
        return len(self._store)

    def _prune_expired_locked(self, now: float) -> None:
        """Remove expired entries. Caller must hold `_lock`."""
        expired = [tok for tok, entry in self._store.items() if now > entry.ticket.expires_at]
        for tok in expired:
            del self._store[tok]
