"""Unit tests for the phone-scoped DND enforcement path (BLOCKER #2).

Distinct file from ``test_dialer.py`` because the DND port lives in its own
module (``src.services.dialer.dnd``) and covers a compliance concern —
RBI FPC / TRAI NDNC — that is orthogonal to the queue/engine mechanics
tested there. Keeping the two apart makes it obvious in test output which
regression touched the regulatory path vs. the dial-loop mechanics.

The engine-level wiring (``DialerEngine`` blocks a DND-matched lead
without calling Twilio, increments ``CALLS_BLOCKED_DND``, and marks the
lead DONE) is covered in ``test_dialer.py::TestDialerEngineDND`` so it
sits next to the other engine tests and shares the same fake fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.dialer.dnd import (
    CsvPhoneDNDList,
    NullPhoneDND,
    PhoneDNDPort,
    _normalise,
)


class TestNormalisation:
    """Every operator/registry hands us phone strings in a different shape.
    The normaliser is the single choke-point that unifies them; a bug
    here would produce silent false-negatives at dial time (the number
    is on the DND list but doesn't match) — that is the exact class of
    compliance failure this port exists to prevent."""

    def test_spaces_stripped(self) -> None:
        assert _normalise("+91 98765 43210") == "+919876543210"

    def test_hyphens_stripped(self) -> None:
        assert _normalise("+91-98765-43210") == "+919876543210"

    def test_parens_stripped(self) -> None:
        assert _normalise("+91 (98765) 43210") == "+919876543210"

    def test_already_normalised_is_stable(self) -> None:
        assert _normalise("+919876543210") == "+919876543210"

    def test_empty_string_returns_empty(self) -> None:
        assert _normalise("") == ""

    def test_only_separators_returns_empty(self) -> None:
        assert _normalise("- () ") == ""


class TestNullPhoneDND:
    def test_never_blocks(self) -> None:
        dnd = NullPhoneDND()
        assert dnd.is_on_dnd("+919876543210") is False
        assert dnd.is_on_dnd("") is False

    def test_conforms_to_port(self) -> None:
        """runtime_checkable protocol — NullPhoneDND must satisfy PhoneDNDPort
        so ``DialerEngine`` can type-hint ``PhoneDNDPort`` and accept it."""
        assert isinstance(NullPhoneDND(), PhoneDNDPort)


class TestCsvPhoneDNDList:
    def test_direct_construction_from_iterable(self) -> None:
        lst = CsvPhoneDNDList(["+919876543210", "+919000000001"])
        assert len(lst) == 2
        assert lst.is_on_dnd("+919876543210") is True
        assert lst.is_on_dnd("+919000000002") is False

    def test_membership_via_normalised_key(self) -> None:
        """A caller looking up '+91 98765 43210' must match a stored
        '+919876543210' — the whole point of the normaliser."""
        lst = CsvPhoneDNDList(["+919876543210"])
        assert lst.is_on_dnd("+91 98765 43210") is True
        assert lst.is_on_dnd("+91-98765-43210") is True

    def test_empty_and_whitespace_entries_skipped(self) -> None:
        """Real NDNC exports contain stray blank lines; without this guard
        the set would gain an empty-string entry that matches every empty
        phone lookup and silently poison the DND check."""
        lst = CsvPhoneDNDList(["+919876543210", "", "   ", "\n"])
        assert len(lst) == 1

    def test_deduplicates(self) -> None:
        lst = CsvPhoneDNDList(["+919876543210", "+919876543210", "+91 98765 43210"])
        assert len(lst) == 1

    def test_from_csv_single_column(self, tmp_path: Path) -> None:
        f = tmp_path / "dnd.csv"
        f.write_text(
            "+919876543210\n"
            "+919000000001\n"
            "+919000000002\n",
            encoding="utf-8",
        )
        lst = CsvPhoneDNDList.from_csv(f)
        assert len(lst) == 3
        assert lst.is_on_dnd("+919876543210")
        assert not lst.is_on_dnd("+919999999999")

    def test_from_csv_skips_non_numeric_header(self, tmp_path: Path) -> None:
        """The TRAI NDNC and most NCCM API exports ship with a header row
        (``phone``, ``msisdn``, etc.). It must be skipped or the set will
        get a bogus 'phone' key."""
        f = tmp_path / "dnd_with_header.csv"
        f.write_text(
            "phone\n"
            "+919876543210\n"
            "+919000000001\n",
            encoding="utf-8",
        )
        lst = CsvPhoneDNDList.from_csv(f)
        assert len(lst) == 2
        assert not lst.is_on_dnd("phone")

    def test_from_csv_ignores_extra_columns(self, tmp_path: Path) -> None:
        """Registry exports commonly ship reason/expiry columns. The Dialer
        only needs boolean membership; extra columns must be ignored, not
        crash the loader."""
        f = tmp_path / "dnd_multi.csv"
        f.write_text(
            "phone,reason,expires_at\n"
            "+919876543210,fully-blocked,2027-01-01\n"
            "+919000000001,partial,2026-06-30\n",
            encoding="utf-8",
        )
        lst = CsvPhoneDNDList.from_csv(f)
        assert len(lst) == 2
        assert lst.is_on_dnd("+919876543210")

    def test_from_csv_tolerates_formatted_numbers(self, tmp_path: Path) -> None:
        """Some manual/legacy exports contain formatted numbers. Loader
        must normalise on ingest, not just on lookup."""
        f = tmp_path / "dnd_formatted.csv"
        f.write_text(
            "+91 98765 43210\n"
            "+91-90000-00001\n",
            encoding="utf-8",
        )
        lst = CsvPhoneDNDList.from_csv(f)
        assert lst.is_on_dnd("+919876543210")
        assert lst.is_on_dnd("+919000000001")

    def test_from_csv_skips_blank_rows(self, tmp_path: Path) -> None:
        f = tmp_path / "dnd_blank.csv"
        f.write_text(
            "+919876543210\n"
            "\n"
            "\n"
            "+919000000001\n",
            encoding="utf-8",
        )
        lst = CsvPhoneDNDList.from_csv(f)
        assert len(lst) == 2

    def test_from_csv_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            CsvPhoneDNDList.from_csv(tmp_path / "does-not-exist.csv")

    def test_conforms_to_port(self) -> None:
        assert isinstance(CsvPhoneDNDList([]), PhoneDNDPort)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
