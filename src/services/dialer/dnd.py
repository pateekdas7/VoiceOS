"""Phone-number DND enforcement at dial time (V4 Ch2; RBI FPC / TRAI NDNC).

The pre-existing ``services.campaign_management.scheduler.DNDStatusPort`` is
customer-scoped and backed by ``ConsentType.CONTACT == REVOKED`` — a
consent-derived signal, not a regulatory registry. This module adds the
complementary phone-scoped enforcement path the Dialer needs immediately
before it hands a number to Twilio:

- ``PhoneDNDPort``  — structural protocol; ``is_on_dnd(phone) -> bool``.
- ``CsvPhoneDNDList`` — sensible default; loads an E.164 phone-per-line CSV
  (the shape both the TRAI NDNC monthly extract and most Indian NCCM API
  exports produce) into an in-memory set for O(1) dial-time lookup.
- ``NullPhoneDND`` — never-blocks stub, used when no DND source is
  configured (dev/test environments) so ``DialerEngine`` can accept a
  non-None port unconditionally and skip the ``if is not None`` branch
  on every dial.

An NCCM-API-backed adapter can be added later without touching the port
or the Dialer wiring — it just needs to implement ``is_on_dnd()``.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

_log = logging.getLogger("voiceos.dialer.dnd")

# Strip everything except a leading '+' and digits so that "+91 98765 43210",
# "+91-98765-43210", and "+919876543210" all normalise to the same key.
_NORMALISE_RE = re.compile(r"[^\d+]")


def _normalise(phone: str) -> str:
    return _NORMALISE_RE.sub("", phone or "").strip()


@runtime_checkable
class PhoneDNDPort(Protocol):
    """Structural port for a regulatory DND lookup keyed by phone number.

    Distinct from ``services.campaign_management.scheduler.DNDStatusPort``
    (customer-id-keyed, consent-derived) — the compliance registry the
    Dialer enforces is phone-number-keyed by definition.
    """

    def is_on_dnd(self, phone: str) -> bool: ...


class NullPhoneDND:
    """Never-blocks default. Used when no DND registry is configured.

    Chosen over ``None`` in ``DialerEngine`` so the dial-time check is
    always a single method call — no ``if self._dnd is not None`` branch
    to skip on hot path — while still allowing dev/local runs where the
    NDNC CSV isn't available.
    """

    def is_on_dnd(self, phone: str) -> bool:  # noqa: ARG002 — port shape
        return False


class CsvPhoneDNDList:
    """DND list loaded from a CSV file at construction (single-column or with header).

    Loading semantics (deliberately strict, deliberately explicit):
      * File is opened once; every non-empty cell in the first column is
        normalised via :func:`_normalise` and inserted into a set.
      * If the first row's first cell doesn't normalise to a digit
        sequence (e.g. header "phone"), it's skipped.
      * Blank lines and lines whose normalised phone would be empty are
        skipped without error — real NDNC exports occasionally contain
        stray blank lines that would otherwise silently poison the set
        with an empty-string entry that matches every empty phone lookup.

    The whole file is loaded in memory because the current TRAI NDNC
    monthly extract is ~250 MB gzipped / ~2 GB uncompressed — well within
    a single dialer node's RAM budget — and per-dial lookup must be
    microseconds, not a disk seek. A future streaming/paged adapter can be
    added if the registry ever outgrows RAM.
    """

    def __init__(self, phones: Iterable[str]) -> None:
        self._phones: set[str] = {
            n for n in (_normalise(p) for p in phones) if n
        }
        _log.info("CsvPhoneDNDList loaded phones=%d", len(self._phones))

    @classmethod
    def from_csv(cls, path: str | Path) -> "CsvPhoneDNDList":
        """Construct from a CSV file.

        The first column of each row is treated as the phone number.
        Additional columns (e.g. ``reason``, ``expires_at``) are ignored —
        the Dialer only needs a boolean membership answer.
        """
        path = Path(path)
        phones: list[str] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            for i, row in enumerate(reader):
                if not row:
                    continue
                cell = row[0].strip()
                if not cell:
                    continue
                normalised = _normalise(cell)
                # Skip a header row when the first row's first cell is
                # non-numeric (e.g. "phone", "msisdn").
                if i == 0 and not normalised.lstrip("+").isdigit():
                    continue
                if not normalised:
                    continue
                phones.append(normalised)
        return cls(phones)

    def is_on_dnd(self, phone: str) -> bool:
        return _normalise(phone) in self._phones

    def __len__(self) -> int:
        return len(self._phones)
