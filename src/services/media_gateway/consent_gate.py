"""Customer-consent enforcement at call open (V4 Ch2 — RBI FPC / DPDP).

The scheduler already consults a customer-scoped DND port at *schedule*
time (``services.campaign_management.scheduler.DNDStatusPort``). This
module adds the complementary *runtime* check the Media Gateway runs
immediately before it accepts a WebSocket and starts the greeting — the
defense-in-depth path that catches a consent revocation that landed
after the call was scheduled but before it actually opened, or an
inbound call that never went through the scheduler at all.

Design:

- ``CustomerConsentPort`` — structural protocol; a single boolean method
  ``is_revoked(tenant_id, customer_id) -> bool``. Keeping the surface
  minimal means a real adapter (Postgres-backed, Redis-cached) can be
  plugged in later without touching the entrypoint.
- ``NullCustomerConsent`` — never-revoked default. Used when no consent
  port is wired (dev/test) so the entrypoint's call path is unconditional
  — no ``if consent_gate is not None`` branch to skip.

The metric ``voiceos_media_gateway_calls_blocked_consent_revoked_total``
increments on every rejection so ops can alert on unexpected spikes
(e.g. an upstream export that flipped every customer to REVOKED).
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

_log = logging.getLogger("voiceos.media_gateway.consent")


@runtime_checkable
class CustomerConsentPort(Protocol):
    """Runtime consent lookup keyed by (tenant_id, customer_id).

    Distinct from the phone-scoped ``PhoneDNDPort`` (regulatory NDNC
    registry) and from ``DNDStatusPort`` (schedule-time consent check) —
    this is the *call-open* gate: what is this specific customer's
    consent status *right now*, at the moment they answered.
    """

    def is_revoked(self, tenant_id: str, customer_id: str) -> bool: ...


class NullCustomerConsent:
    """Never-revoked default. Used when no consent registry is configured.

    Chosen over ``None`` in ``SharedCallDependencies`` for the same
    reason ``NullPhoneDND`` exists in the dialer: the call-open check
    is always a single method call, no ``if consent_gate is not None``
    branch on the hot path, and dev/local runs work without a Postgres
    consent adapter wired up.
    """

    def is_revoked(self, tenant_id: str, customer_id: str) -> bool:  # noqa: ARG002 — port shape
        return False
