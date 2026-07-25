"""Twilio Media Streams WebSocket entrypoint (Path-A consolidation, Phase 4).

The missing telephony transport layer. `TwilioWebSocketAdapter`'s own
docstring has said since Sprint-004 that "the WebSocket server layer
(added in a later deployment sprint) calls put_message()" — that server
layer never existed anywhere in this repo until this file. This is also
the first file in the repo that drives a real call from raw carrier audio
all the way through AudioSessionManager -> AudioPreprocessor -> VAD ->
STT -> DialogueManager -> ConversationEngine -> TTS -> back to the
carrier, in one real, live, bidirectional loop.

Unlike src/services/admin_portal/api.py, api_platform/api.py, and
hitl/review_api.py (each explicitly documented as "a library ASGI app,
not bound to a live listener" pending Sprint-026 K8s/Helm), this app is
meant to actually be served — create_twilio_media_stream_app() returns a
Starlette app with a real WebSocketRoute a `uvicorn.run()` can bind.

Per-call pipeline (CallOrchestrator), three concurrent tasks for the
lifetime of one WebSocket connection:
  A) _pump_inbound   — adapter.receive_frame() -> mu-law->PCM16LE decode
                        -> AudioSession.push_frame() (jitter/PLC) ->
                        AudioPreprocessor (8kHz->16kHz) -> VAD -> routes
                        frames into the active turn's queue; handles
                        BargeinDetected (flush playback) and
                        VADSpeechEnd (close the turn's audio stream).
  B) _run_turns       — waits for a turn's audio to be ready, runs
                        STT -> DialogueManager.ingest_stream() -> TurnInput
                        -> ConversationEngine.handle_turn() -> AudioClauses
                        -> resample/mu-law-encode -> adapter.send_frame().
  C) _pump_outbound   — drains adapter.drain_outbound() and forwards to
                        the real WebSocket.

Known gap this file does NOT attempt to fix: VAD-driven turn segmentation
means STT only starts after VADSpeechEnd closes a turn's frame queue
(WhisperHTTPAdapter itself is also non-streaming at the HTTP level, per
Phase 3) — true incremental/streaming STT during speech is a further
enhancement, not required for Phase 4's scope (build the transport).

Architecture: V1 Ch3 (Media Gateway), V1 Ch4-9 (audio pipeline through
Dialogue Manager), V2 Ch1 (Conversation Engine); V6 Ch4 AR-2
(auth-before-allocation).
"""

from __future__ import annotations

import audioop
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.context import CustomerContext
from src.libs.contracts.events.audio_events import BargeinDetected, VADSpeechEnd, VADSpeechStart
from src.libs.contracts.streaming import WordHypothesis
from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.media_gateway.adapters.twilio_websocket import TwilioWebSocketAdapter
from src.services.media_gateway.protocol import ADAPTER_TYPE_TWILIO
from src.services.media_gateway.service import MediaGatewayService
from src.services.playback.output import AudioOutput
from src.services.playback.scheduler import PlaybackScheduler
from src.services.vad_endpointing.bargein_detector import BargeinDetector
from src.services.vad_endpointing.backchannel import BackchannelDiscriminator
from src.services.vad_endpointing.endpoint_detector import EndpointDetector
from src.services.vad_endpointing.service import VADEndpointingService
from src.services.vad_endpointing.vad_engine import VADEngine, VADModelProtocol

if TYPE_CHECKING:
    from src.services.conversation_engine.engine import ConversationEngine
    from src.services.dialogue_manager.service import DialogueManager
    from src.services.stt.service import STTService

logger = logging.getLogger("voiceos.media_gateway.twilio_ws")

_TWILIO_MULAW_CONFIG = AudioConfig(sample_rate=SampleRate.RATE_8K, encoding=Encoding.MULAW, channels=1)
_PCM16_8K_CONFIG = AudioConfig(sample_rate=SampleRate.RATE_8K, encoding=Encoding.PCM16LE, channels=1)


# ---------------------------------------------------------------------------
# Shared (constructed once, reused across every call) dependencies
# ---------------------------------------------------------------------------


@dataclass
class SharedCallDependencies:
    """Dependencies constructed once at process startup and shared across
    every call — everything here is either stateless or internally
    call_id-keyed. Per-call-only state (AudioSession, VADEndpointingService,
    DialogueManager, PlaybackScheduler) is constructed fresh inside
    CallOrchestrator.create() for each new connection instead."""

    account_sid: str
    auth_token: str
    tenant_id: str
    media_gateway_service: MediaGatewayService
    audio_session_manager_service: AudioSessionManagerService
    audio_preprocessor: AudioPreprocessorService
    stt_service: STTService
    conversation_engine: ConversationEngine
    vad_model_factory: type[VADModelProtocol] | Any = None  # see build_vad_model() in deployment/cpu/app.py
    language: str = "hi"
    speak_greeting: bool = True
    """Path-A Phase 6g: speak ConversationEngine.build_greeting() once at
    call start, before the customer's first turn. True by default; a caller
    that already spoke a greeting through some other channel (e.g. TwiML
    <Say> before <Connect><Stream>) can set this False to avoid a duplicate."""
    public_ws_base_url: str = ""
    """The externally-reachable base URL Twilio's <Connect><Stream url="..."/>
    actually points at (e.g. "wss://random-words.trycloudflare.com" behind a
    tunnel, since this process itself only ever sees the internal
    ws://0.0.0.0:<port> address it's bound to). Twilio's X-Twilio-Signature
    HMAC is computed over the exact URL it was told to connect to (Twilio
    Media Streams WebSocket security docs: the signature covers the full
    request URL); if this process instead used the internal address it sees
    locally, the signature would never validate and every real connection
    would be rejected at the AR-2 auth gate before a single frame is
    processed. Empty string (the default) preserves pre-existing behavior —
    reconstructing the URL from what the ASGI server itself observed — for
    tests and any deployment that terminates TLS with correct proxy-header
    forwarding configured elsewhere instead."""


# ---------------------------------------------------------------------------
# Mu-law <-> PCM16LE helpers (Twilio wire format <-> internal canonical format)
# ---------------------------------------------------------------------------


def _mulaw_frame_to_pcm16le(frame: AudioFrame) -> AudioFrame:
    """Decode a Twilio mu-law AudioFrame to PCM16LE at the same 8 kHz rate.

    AudioPreprocessorService.process_frame() requires PCM16LE input (it
    resamples 8kHz->16kHz internally) — TwilioWebSocketAdapter yields raw
    mu-law bytes (see _handle_media()), so this decode step must happen
    before frames reach AudioSession/AudioPreprocessor. No such conversion
    existed anywhere in the repo prior to this file.
    """
    pcm16 = audioop.ulaw2lin(frame.pcm_data, 2)
    return frame.model_copy(update={"pcm_data": pcm16, "config": _PCM16_8K_CONFIG})


def _clause_to_mulaw_frame(pcm_ulaw: bytes, seq: int, rtp_ts: int) -> AudioFrame:
    return AudioFrame(
        pcm_data=pcm_ulaw,
        seq=seq,
        rtp_ts=rtp_ts,
        recv_ts=time.time(),
        config=_TWILIO_MULAW_CONFIG,
    )


async def _queue_to_frame_gen(queue: Any) -> AsyncIterator[AudioFrame]:
    """Consume an asyncio.Queue[AudioFrame | None] until the None sentinel
    (pushed by _pump_inbound on VADSpeechEnd) closes this turn's stream."""
    while True:
        frame = await queue.get()
        if frame is None:
            return
        yield frame


# ---------------------------------------------------------------------------
# CallOrchestrator — the full per-call real-time pipeline
# ---------------------------------------------------------------------------


class CallOrchestrator:
    """Owns every per-call object and runs the 3 concurrent pipeline tasks.

    Constructed fresh per WebSocket connection via `create()`.
    """

    def __init__(
        self,
        call_id: str,
        tenant_id: str,
        adapter: TwilioWebSocketAdapter,
        deps: SharedCallDependencies,
        vad_engine: VADEngine,
        context: CustomerContext | None = None,
        identity_verified: bool = False,
    ) -> None:
        import asyncio

        self._call_id = call_id
        self._tenant_id = tenant_id
        self._adapter = adapter
        self._deps = deps
        self._context = context
        self._identity_verified = identity_verified

        self._session = deps.audio_session_manager_service.create_session(call_id, tenant_id)
        self._vad = VADEndpointingService(
            vad_engine=vad_engine,
            endpoint_detector=EndpointDetector(),
            bargein_detector=BargeinDetector(),
            backchannel_discriminator=BackchannelDiscriminator(),
        )
        self._vad.start()
        self._playback = PlaybackScheduler()
        self._audio_output = AudioOutput()

        self._call_start_monotonic = time.monotonic()
        self._turn_ready = asyncio.Event()
        self._current_turn_queue: asyncio.Queue[AudioFrame | None] | None = None
        self._turn_active = False
        self._turn_index = 0
        self._out_seq = 0
        self._closing = False
        self._dialogue_manager: DialogueManager | None = None

    @classmethod
    async def create(
        cls,
        call_id: str,
        tenant_id: str,
        credentials: dict[str, str],
        deps: SharedCallDependencies,
        context: CustomerContext | None = None,
        identity_verified: bool = False,
    ) -> CallOrchestrator | None:
        """Admit the call (AR-2: authenticate before any allocation), then
        construct the orchestrator. Returns None if authentication fails —
        caller must close the WebSocket without proceeding."""
        adapter = TwilioWebSocketAdapter(
            account_sid=deps.account_sid,
            auth_token=deps.auth_token,
            tenant_id=tenant_id,
            call_id=call_id,
        )
        result = await deps.media_gateway_service.admit_adapter(
            call_id=call_id,
            tenant_id=tenant_id,
            adapter=adapter,
            credentials=credentials,
            adapter_type=ADAPTER_TYPE_TWILIO,
        )
        if not result.success:
            logger.warning("Call %s rejected: %s", call_id, result.reason)
            return None

        vad_model = deps.vad_model_factory() if deps.vad_model_factory is not None else _default_vad_model()
        vad_engine = VADEngine(model=vad_model)

        return cls(
            call_id=call_id,
            tenant_id=tenant_id,
            adapter=adapter,
            deps=deps,
            vad_engine=vad_engine,
            context=context,
            identity_verified=identity_verified,
        )

    def _call_time_ms(self) -> int:
        return int((time.monotonic() - self._call_start_monotonic) * 1000)

    def feed_message(self, msg: dict[str, Any] | None) -> None:
        """Public forwarding point for the WS server layer's inbound loop —
        avoids reaching into the adapter's internals from outside this class."""
        self._adapter.put_message(msg)

    # ------------------------------------------------------------------
    # Task A — inbound frame pump
    # ------------------------------------------------------------------

    async def _pump_inbound(self) -> None:
        async for raw_frame in self._adapter.receive_frame():
            pcm_frame = _mulaw_frame_to_pcm16le(raw_frame)
            for ready_frame in self._session.push_frame(pcm_frame):
                pre = self._deps.audio_preprocessor.process_frame(self._call_id, ready_frame)
                events = self._vad.process_frame(pre, self._call_id, self._tenant_id, self._call_time_ms())

                if self._turn_active and self._current_turn_queue is not None:
                    await self._current_turn_queue.put(pre)

                for event in events:
                    await self._handle_vad_event(event)

        # Carrier closed the stream — end any in-flight turn so Task B
        # doesn't block forever waiting on a sentinel that will never come.
        if self._turn_active and self._current_turn_queue is not None:
            await self._current_turn_queue.put(None)
        self._closing = True

    async def _handle_vad_event(self, event: object) -> None:
        import asyncio

        if isinstance(event, VADSpeechStart) and not self._turn_active:
            self._current_turn_queue = asyncio.Queue()
            self._turn_active = True
            self._turn_ready.set()
        elif isinstance(event, VADSpeechEnd) and self._turn_active:
            if self._current_turn_queue is not None:
                await self._current_turn_queue.put(None)
            self._turn_active = False
        elif isinstance(event, BargeinDetected):
            logger.info("Barge-in detected on call %s — flushing playback", self._call_id)
            await self._playback.flush()
            self._playback.clear_barge_in()
            self._session.on_barge_in()
            self._session.on_barge_in_end()

    # ------------------------------------------------------------------
    # Task B — turn loop: STT -> DialogueManager -> ConversationEngine -> TTS out
    # ------------------------------------------------------------------

    async def _run_turns(self) -> None:
        if self._dialogue_manager is None:
            from src.services.dialogue_manager.service import DialogueManager

            self._dialogue_manager = DialogueManager(call_id=self._call_id, tenant_id=self._tenant_id)

        while not self._closing:
            await self._turn_ready.wait()
            self._turn_ready.clear()
            await self._run_turns_one_iteration()

    async def _run_turns_one_iteration(self) -> None:
        """One turn's worth of STT -> DialogueManager -> ConversationEngine ->
        outbound audio. Split out from _run_turns() so orchestration logic is
        testable without driving the (otherwise infinite) polling loop."""
        if self._dialogue_manager is None:
            from src.services.dialogue_manager.service import DialogueManager

            self._dialogue_manager = DialogueManager(call_id=self._call_id, tenant_id=self._tenant_id)

        queue = self._current_turn_queue
        if queue is None:
            return

        word_stream: AsyncIterator[WordHypothesis] = self._deps.stt_service.transcribe_stream(
            _queue_to_frame_gen(queue), language=self._deps.language
        )
        turn = await self._dialogue_manager.ingest_stream(word_stream)

        if not turn.transcript.strip():
            return  # silence/noise-only turn — nothing to respond to

        clauses = await self._deps.conversation_engine.handle_turn(
            turn=turn,
            playback=self._playback,
            context=self._context,
            identity_verified=self._identity_verified,
        )
        await self._send_clauses(clauses)
        self._turn_index += 1

    async def _send_clauses(self, clauses: list[Any]) -> None:
        """Send this turn's clauses out and drain them from PlaybackScheduler.

        ConversationEngine's TrueStreamingPipeline already enqueued every one
        of these same clauses into self._playback as it synthesised them (for
        barge-in tracking — VAD's BargeinDetected handler calls
        self._playback.flush(); TrueStreamingPipeline.run()'s loop checks
        self._playback.barge_in_event mid-stream). Nothing previously dequeued
        them afterward: RI-3's bounded-queue guard (max_depth=512, ~43s of
        cumulative 85.33ms chunks) would eventually reject every turn's first
        enqueue() once total spoken audio across the whole call exceeded that
        bound, crashing any call longer than ~43s of cumulative AI speech —
        found via Path-A Phase 7's real multi-turn dry run, not caught by
        existing tests (none drove enough turns to reach the bound). Draining
        one dequeue_nowait() per clause sent keeps the queue's depth accurate
        to "still pending," matching what it was already enqueuing for.
        Non-blocking: a mocked/test-double ConversationEngine that returns
        clauses without ever calling enqueue() on this scheduler must not
        hang here waiting for entries that will never arrive.
        """
        self._vad.set_playback_active(True, playback_seq=self._turn_index)
        try:
            for clause in clauses:
                pcm_ulaw = self._audio_output.convert(clause, fmt="ulaw")
                self._out_seq += 1
                out_frame = _clause_to_mulaw_frame(pcm_ulaw, seq=self._out_seq, rtp_ts=self._out_seq * 160)
                await self._adapter.send_frame(out_frame)
                self._playback.dequeue_nowait()
        finally:
            self._vad.set_playback_active(False)

    # ------------------------------------------------------------------
    # Task C — outbound pump: adapter's queued Twilio JSON -> real WebSocket
    # ------------------------------------------------------------------

    async def _pump_outbound(self, websocket: WebSocket) -> None:
        import asyncio

        # Twilio Media Streams JSON is sent as WebSocket TEXT frames, not
        # binary (adapter.drain_outbound() returns the JSON already encoded
        # to bytes for queueing — decode back to str for send_text()).
        while not self._closing:
            msg = self._adapter.drain_outbound()
            if msg is not None:
                await websocket.send_text(msg.decode())
            else:
                await asyncio.sleep(0.01)
        # Drain any remaining queued messages before the connection closes.
        while (msg := self._adapter.drain_outbound()) is not None:
            await websocket.send_text(msg.decode())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _speak_greeting(self) -> None:
        """Path-A Phase 6g: speak the call-open identity-verification
        greeting before any customer turn, so DialogueResponseEngine's
        AWAIT_IDENTITY state has already asked its question by the time the
        customer's first reply reaches handle_turn(). No-op when
        ConversationEngine has no dialogue_response wired (build_greeting()
        returns None) — preserves pre-Phase-6g behavior for TwiML-<Say>-based
        greetings or callers that haven't adopted the scripted golden path.
        """
        greeting = self._deps.conversation_engine.build_greeting(self._context)
        if greeting is None:
            return
        clauses = await self._deps.conversation_engine.speak_scripted_text(greeting, self._playback)
        await self._send_clauses(clauses)

    async def run(self, websocket: WebSocket) -> None:
        import asyncio

        if self._deps.speak_greeting:
            await self._speak_greeting()

        tasks = [
            asyncio.create_task(self._pump_inbound()),
            asyncio.create_task(self._run_turns()),
            asyncio.create_task(self._pump_outbound(websocket)),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            self._closing = True
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._deps.media_gateway_service.release_adapter(self._call_id)
            self._deps.audio_session_manager_service.release_session(self._call_id)


def _default_vad_model() -> VADModelProtocol:
    """No SILERO_VAD_MODEL_PATH configured -> the documented testing/
    offline-environment fallback (src/services/vad_endpointing/vad_engine.py:
    "In production always use SileroVADModel", but EnergyVADModel is a real,
    dependency-free, deterministic energy-threshold VAD, not a mock — it
    degrades gracefully rather than failing closed when no ONNX model file
    has been provisioned on this node)."""
    from src.services.vad_endpointing.vad_engine import EnergyVADModel

    logger.warning(
        "No Silero VAD model configured — falling back to EnergyVADModel "
        "(V1 Ch6: 'NOT for production use'). Set SILERO_VAD_MODEL_PATH to "
        "use the production model."
    )
    return EnergyVADModel()


# ---------------------------------------------------------------------------
# Starlette app factory
# ---------------------------------------------------------------------------


def create_twilio_media_stream_app(deps: SharedCallDependencies) -> Starlette:
    """Build the Starlette app serving the Twilio Media Streams WebSocket.

    Unlike admin_portal/api_platform/hitl's create_*_api() factories, this
    app is meant to actually be bound by a live uvicorn process — it is the
    Phase 4 telephony transport this consolidation plan exists to build.

    Route: WS /twilio/media-stream
    """

    async def _endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        call_id = ""
        try:
            first_message = await websocket.receive_json()
        except Exception:
            await websocket.close(code=4000)
            return

        # Twilio always opens with {"event": "connected"} then {"event":
        # "start", "start": {"callSid": ..., ...}} — pull identifiers from
        # the start message per TwilioWebSocketAdapter._handle_start()'s
        # own parsing (twilio_websocket.py).
        start_msg = first_message
        if first_message.get("event") == "connected":
            try:
                start_msg = await websocket.receive_json()
            except Exception:
                await websocket.close(code=4000)
                return

        call_id = start_msg.get("start", {}).get("callSid", "") or start_msg.get("start", {}).get("streamSid", "")
        if not call_id:
            logger.error("Twilio WS connection with no callSid/streamSid in start message — closing")
            await websocket.close(code=4001)
            return

        # See SharedCallDependencies.public_ws_base_url's docstring: Twilio's
        # signature is computed over the public URL it was told to connect
        # to, which this process cannot observe directly when running behind
        # a tunnel/reverse-proxy — websocket.url would be the internal
        # address, and the signature would never validate against it.
        request_url = (
            f"{deps.public_ws_base_url}{websocket.url.path}" if deps.public_ws_base_url else str(websocket.url)
        )
        credentials = {
            "account_sid": deps.account_sid,
            "auth_token": deps.auth_token,
            "url": request_url,
            "params": "{}",
            "x_twilio_signature": websocket.headers.get("x-twilio-signature", ""),
            "expected_account_sid": deps.account_sid,
        }

        orchestrator = await CallOrchestrator.create(
            call_id=call_id,
            tenant_id=deps.tenant_id,
            credentials=credentials,
            deps=deps,
        )
        if orchestrator is None:
            await websocket.close(code=4003)
            return

        orchestrator.feed_message(start_msg)  # feed the already-consumed 'start' message

        async def _forward_inbound() -> None:
            try:
                while True:
                    msg = await websocket.receive_json()
                    orchestrator.feed_message(msg)
                    if msg.get("event") == "stop":
                        break
            except WebSocketDisconnect:
                pass
            finally:
                orchestrator.feed_message(None)

        import asyncio

        forward_task = asyncio.create_task(_forward_inbound())
        try:
            await orchestrator.run(websocket)
        finally:
            if not forward_task.done():
                forward_task.cancel()
            await asyncio.gather(forward_task, return_exceptions=True)

    return Starlette(routes=[WebSocketRoute("/twilio/media-stream", endpoint=_endpoint)])
