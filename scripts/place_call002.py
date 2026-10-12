"""Places the real, live, free-form Call-002 trial via the Twilio REST API.

Builds inline TwiML that connects the call directly to the Path-A WS
entrypoint's Media Streams route (no <Say>/<Gather> — this is Call-002's
whole point: exercise the consolidated Path-A runtime, not the old
Path-B/conv_server.py <Gather> flow) and passes customer_id as a
<Stream><Parameter> so the WS entrypoint can assemble the real,
authoritative CustomerContext (see twilio_ws_entrypoint.py's start_call()
wiring).

Reads every credential/number from the environment -- nothing is
hardcoded or written to any file this script controls, per this project's
"never commit API keys" rule (CLAUDE.md Security section). Required env
vars:
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_FROM_NUMBER      e.g. +19203157310
    TWILIO_TO_NUMBER        e.g. +919911954448
    PUBLIC_WS_BASE_URL      e.g. wss://random-words.trycloudflare.com
    CALL002_CUSTOMER_ID     the real customers.customer_id row to resolve

Safety gate: prints the exact TwiML and request before sending, and
refuses to place the call unless --confirm is passed on the command line
-- this makes "accidentally ran the script" and "deliberately confirmed
placing a real call to a real phone" two distinct, explicit actions.
"""

from __future__ import annotations

import os
import sys
from xml.sax.saxutils import escape

_ATTR_ENTITIES = {'"': "&quot;"}


def _escape_attr(value: str) -> str:
    return escape(value, _ATTR_ENTITIES)

import httpx

_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"ERROR: required environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def build_twiml(ws_base_url: str, customer_id: str) -> str:
    stream_url = f"{ws_base_url}/twilio/media-stream"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Connect><Stream url="{_escape_attr(stream_url)}">'
        f'<Parameter name="customer_id" value="{_escape_attr(customer_id)}"/>'
        "</Stream></Connect>"
        "</Response>"
    )


def main() -> None:
    confirm = "--confirm" in sys.argv[1:]

    account_sid = _required_env("TWILIO_ACCOUNT_SID")
    auth_token = _required_env("TWILIO_AUTH_TOKEN")
    from_number = _required_env("TWILIO_FROM_NUMBER")
    to_number = _required_env("TWILIO_TO_NUMBER")
    ws_base_url = _required_env("PUBLIC_WS_BASE_URL")
    customer_id = _required_env("CALL002_CUSTOMER_ID")

    twiml = build_twiml(ws_base_url, customer_id)

    print("=" * 70)
    print("Call-002 — free-form live trial")
    print("=" * 70)
    print(f"From:        {from_number}")
    print(f"To:          {to_number}")
    print(f"Stream URL:  {ws_base_url}/twilio/media-stream")
    print(f"customer_id: {customer_id}")
    print("\nTwiML to be sent:")
    print(twiml)
    print()

    if not confirm:
        print("Dry run only (pass --confirm to actually place the call). No request sent.")
        return

    resp = httpx.post(
        f"{_TWILIO_API_BASE}/Accounts/{account_sid}/Calls.json",
        auth=(account_sid, auth_token),
        data={"To": to_number, "From": from_number, "Twiml": twiml},
        timeout=30.0,
    )
    print(f"Twilio API response: HTTP {resp.status_code}")
    print(resp.text)
    resp.raise_for_status()
    call_sid = resp.json().get("sid", "")
    print(f"\nCall placed. Twilio Call SID: {call_sid}")


if __name__ == "__main__":
    main()
