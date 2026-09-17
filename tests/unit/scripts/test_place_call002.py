"""Unit test for place_call002.py's pure TwiML builder (no network, no secrets)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "place_call002.py"
_spec = importlib.util.spec_from_file_location("place_call002", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
place_call002 = importlib.util.module_from_spec(_spec)
sys.modules["place_call002"] = place_call002
_spec.loader.exec_module(place_call002)


def test_build_twiml_embeds_stream_url_and_customer_id() -> None:
    twiml = place_call002.build_twiml("wss://example.trycloudflare.com", "cust-123")
    assert '<Stream url="wss://example.trycloudflare.com/twilio/media-stream">' in twiml
    assert '<Parameter name="customer_id" value="cust-123"/>' in twiml
    assert twiml.startswith("<?xml")
    assert "<Connect>" in twiml and "</Connect>" in twiml


def test_build_twiml_escapes_special_characters_in_customer_id() -> None:
    twiml = place_call002.build_twiml("wss://example.com", 'cust"<>&')
    assert 'value="cust&quot;&lt;&gt;&amp;"' in twiml
