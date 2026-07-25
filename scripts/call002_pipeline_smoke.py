"""End-to-end pipeline smoke test — proves a real call would work, WITHOUT placing one.

Drives the REAL CallOrchestrator (the same class the live Twilio WS
entrypoint uses) with synthetic Twilio Media Streams messages, against the
REAL composition root (real Postgres, real GPU STT/LLM/TTS), and asserts
the full path actually produces outbound audio:

    mu-law frames in -> AudioSession -> preprocessor -> VAD -> STT (GPU)
    -> DialogueManager -> ConversationEngine (+ governance/LLM) -> TTS (GPU)
    -> AudioClauses -> mu-law frames back out

Exists because every live Call-002 attempt so far burned a real phone call
to discover a bug that this could have caught for free: the WS upgrade
failing (no websockets lib), the greeting hanging (no timeout), and
STTService.transcribe_stream() being awaited incorrectly (turn loop died
instantly on the customer's first word). Unit tests missed all three
because they mock exactly the boundaries that were broken.

Run on the CPU node with the same env as the WS server:
    sudo -u postgres /tmp/pipeline_smoke_env.sh
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import os
import struct
import sys
import time
from math import pi, sin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployment", "cpu"))

CALL_ID = "SMOKE_TEST_CALL_001"
STREAM_SID = "MZsmoketest001"
TENANT_ID = os.environ.get("DEFAULT_TENANT_ID", "")
CUSTOMER_ID = os.environ.get("CALL002_CUSTOMER_ID", "")


def _tone_mulaw_payload(num_samples: int = 160, freq_hz: float = 220.0, amplitude: int = 14000) -> str:
    """One 20ms frame of loud 8kHz tone, mu-law encoded + base64'd, exactly
    as Twilio sends it. Loud enough to trip EnergyVADModel's speech threshold."""
    samples = [int(amplitude * sin(2 * pi * freq_hz * i / 8000)) for i in range(num_samples)]
    pcm16 = struct.pack(f"<{num_samples}h", *samples)
    return base64.b64encode(audioop.lin2ulaw(pcm16, 2)).decode()


def _silence_mulaw_payload(num_samples: int = 160) -> str:
    pcm16 = b"\x00\x00" * num_samples
    return base64.b64encode(audioop.lin2ulaw(pcm16, 2)).decode()


class _FakeWebSocket:
    """Captures what the orchestrator sends back toward Twilio."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    def media_frames(self) -> list[dict[str, object]]:
        out = []
        for raw in self.sent:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("event") == "media":
                out.append(msg)
        return out


async def main() -> int:
    import app as composition_root

    from src.services.media_gateway.twilio_ws_entrypoint import CallOrchestrator

    print("=" * 70)
    print("Call-002 pipeline smoke test (no phone call placed)")
    print("=" * 70)

    if not TENANT_ID or not CUSTOMER_ID:
        print("ERROR: DEFAULT_TENANT_ID and CALL002_CUSTOMER_ID must be set.", file=sys.stderr)
        return 1

    print("\n[1/5] Building real SharedCallDependencies (Postgres + GPU adapters)...")
    deps = composition_root.build_shared_call_dependencies()
    deps.recording_dir = ""  # don't pollute the real recordings dir with smoke-test output
    print("      OK")

    print("\n[2/5] Resolving real CustomerContext via start_call()...")
    from src.libs.contracts.primitives import TenantId

    context = deps.conversation_engine.start_call(
        tenant_id=TenantId(TENANT_ID), customer_id=CUSTOMER_ID, call_id=CALL_ID
    )
    print(f"      OK — customer={context.primary_party.name}, loans={len(context.loans)}")

    print("\n[3/5] Constructing CallOrchestrator (bypassing Twilio signature auth)...")
    from src.services.media_gateway.adapters.twilio_websocket import TwilioWebSocketAdapter
    from src.services.vad_endpointing.vad_engine import VADEngine

    adapter = TwilioWebSocketAdapter(
        account_sid=deps.account_sid, auth_token=deps.auth_token, tenant_id=deps.tenant_id, call_id=CALL_ID
    )
    vad_model = deps.vad_model_factory() if deps.vad_model_factory is not None else None
    if vad_model is None:
        from src.services.vad_endpointing.vad_engine import EnergyVADModel

        vad_model = EnergyVADModel()

    orch = CallOrchestrator(
        call_id=CALL_ID,
        tenant_id=deps.tenant_id,
        adapter=adapter,
        deps=deps,
        vad_engine=VADEngine(model=vad_model),
        context=context,
    )
    print("      OK")

    print("\n[4/5] Feeding synthetic Twilio messages (greeting + one spoken turn)...")
    fake_ws = _FakeWebSocket()

    orch.feed_message(
        {
            "event": "start",
            "start": {
                "callSid": CALL_ID,
                "streamSid": STREAM_SID,
                "customParameters": {"customer_id": CUSTOMER_ID},
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        }
    )

    tone = _tone_mulaw_payload()
    silence = _silence_mulaw_payload()

    async def _feed_customer_audio() -> None:
        """Feed frames the way a real carrier does -- paced in real time,
        and only AFTER the greeting finishes -- rather than dumping every
        message up front. Dumping them instantly makes _pump_inbound drain
        the whole stream and set _closing before _run_turns ever wakes, so
        the turn is silently dropped and the test proves nothing about the
        turn path (which is exactly the path that was broken in the live
        call)."""
        chunk = 0
        # Wait out the greeting (measured ~19s of synthesis on the real GPU).
        await asyncio.sleep(24.0)
        print("      (greeting window elapsed — now feeding ~600ms of speech)")
        for _ in range(30):  # ~600ms of speech
            orch.feed_message(
                {"event": "media", "media": {"payload": tone, "chunk": str(chunk), "timestamp": str(chunk * 20)}}
            )
            chunk += 1
            await asyncio.sleep(0.02)
        for _ in range(60):  # ~1.2s of silence -> VAD closes the turn
            orch.feed_message(
                {"event": "media", "media": {"payload": silence, "chunk": str(chunk), "timestamp": str(chunk * 20)}}
            )
            chunk += 1
            await asyncio.sleep(0.02)
        # Give the turn (STT -> engine -> TTS) time to run before hanging up.
        print("      (turn audio sent — waiting for the pipeline to answer)")
        await asyncio.sleep(45.0)
        orch.feed_message({"event": "stop", "stop": {"callSid": CALL_ID}})
        orch.feed_message(None)

    feeder = asyncio.create_task(_feed_customer_audio())

    start = time.monotonic()
    try:
        await asyncio.wait_for(orch.run(fake_ws), timeout=180.0)
    except TimeoutError:
        print("      ERROR: orchestrator.run() did not finish within 180s", file=sys.stderr)
        return 1
    finally:
        if not feeder.done():
            feeder.cancel()
        await asyncio.gather(feeder, return_exceptions=True)
    elapsed = time.monotonic() - start
    print(f"      run() completed in {elapsed:.1f}s")

    print("\n[5/5] Verifying outbound audio actually reached the (fake) carrier...")
    media = fake_ws.media_frames()
    total_payload_bytes = sum(len(base64.b64decode(m["media"]["payload"])) for m in media)  # type: ignore[index]
    print(f"      outbound media frames: {len(media)}")
    print(f"      outbound mu-law bytes: {total_payload_bytes} (~{total_payload_bytes / 8000:.1f}s of audio)")
    print(f"      turns processed:       {orch._turn_index}")

    if not media:
        print("\nFAIL: zero outbound audio frames — the customer would hear nothing.", file=sys.stderr)
        return 1
    if orch._turn_index == 0:
        print(
            "\nFAIL: greeting audio went out, but the customer's turn was never processed "
            "(STT -> engine -> TTS) — a real caller would be ignored after the greeting.",
            file=sys.stderr,
        )
        return 1

    print("\nPASS — greeting AND a real customer turn both produced outbound audio.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
