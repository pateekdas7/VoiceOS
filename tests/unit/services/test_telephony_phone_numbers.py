"""Tests for tenant-scoped telephony number resolution."""

from unittest.mock import MagicMock

from src.services.telephony.phone_numbers import TelephonyNumberResolver


def test_resolves_active_number_to_tenant() -> None:
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = ("tenant-a", "+919900000001", "twilio", "PN123", None)
    result = TelephonyNumberResolver(conn).resolve_for_call("+919900000001", direction="inbound")
    assert result is not None
    assert result.tenant_id == "tenant-a"
    assert result.e164_number == "+919900000001"
    cur.execute.assert_called_once()


def test_unassigned_number_is_rejected() -> None:
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = None
    assert TelephonyNumberResolver(conn).resolve_for_call("+919900000099", direction="outbound") is None


def test_direction_controls_capability_column() -> None:
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = ("tenant-a", "+919900000001", "twilio", "PN123", "campaign-a")
    TelephonyNumberResolver(conn).resolve_for_call("+919900000001", direction="outbound")
    query, params = cur.execute.call_args.args
    assert "outbound_enabled" in query
    assert params == ("+919900000001", False)
