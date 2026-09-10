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
import re
import time
import uuid
from datetime import datetime
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute
from starlette.responses import Response
from starlette.requests import Request as _StarReq
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.context import CustomerContext
from src.libs.contracts.events.audio_events import BargeinDetected, VADSpeechEnd, VADSpeechStart
from src.libs.contracts.primitives import TenantId
from src.libs.contracts.streaming import WordHypothesis
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment
from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.media_gateway.adapters.twilio_websocket import TwilioWebSocketAdapter
from src.services.media_gateway.admission import AdmissionRegistry
from src.services.media_gateway.auth import validate_twilio_signature
from src.services.media_gateway.call_recorder import CallRecorder
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
    from src.services.crm.service import CustomerService
    from src.services.dialogue_manager.service import DialogueManager
    from src.services.stt.service import STTService
    from src.services.tts.greeting_cache import GreetingCache

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
    customer_service: CustomerService | None = None
    """Authoritative CRM lookup used by /voice to resolve customer_id from
    the caller's phone number (ANI for inbound calls, DNIS/To for
    outbound-api Direction) BEFORE minting TwiML. Same
    ``CustomerService`` instance that already lives inside
    ``CustomerContextAssembler`` — shared so both paths see the same
    authoritative source. When ``None`` (unit-test fixtures, or a
    deployment without CRM), /voice skips the lookup and emits TwiML
    with no ``customer_id`` Parameter — WSS then sees an empty value and
    the dialogue safely renders ``clarify_no_record`` instead of
    fabricating a balance. Never trusts a caller-provided value: the
    ``customer_id`` embedded in TwiML comes only from THIS lookup, so
    what WSS reads from ``customParameters`` is authoritative-by-origin
    even though customParameters as a channel is caller-controlled."""
    admission_registry: AdmissionRegistry = field(default_factory=AdmissionRegistry)
    """Two-stage admission for Twilio Media Streams. HTTP /voice validates
    X-Twilio-Signature and mints a single-use token bound to (CallSid,
    AccountSid, tenant_id) which is embedded in the TwiML <Stream>
    <Parameter/>. WSS /twilio/media-stream verifies + consumes the token
    from start.customParameters before any session resource is allocated.
    Twilio never sends X-Twilio-Signature on the WebSocket upgrade — the
    previous design attempted HMAC on WSS anyway, which unconditionally
    failed against real Twilio traffic while unit tests fabricated the
    header and silently passed. Default factory issues a fresh in-memory
    registry per process; production shares one instance across every
    call (all methods are async-safe under an internal asyncio.Lock)."""
    vad_model_factory: type[VADModelProtocol] | Any = None  # see build_vad_model() in deployment/cpu/app.py
    language: str = "hi"
    speak_greeting: bool = True
    """Path-A Phase 6g: speak ConversationEngine.build_greeting() once at
    call start, before the customer's first turn. True by default; a caller
    that already spoke a greeting through some other channel (e.g. TwiML
    <Say> before <Connect><Stream>) can set this False to avoid a duplicate."""
    greeting_timeout_s: float = 60.0
    """Upper bound on how long the call-open greeting (which calls out to
    the GPU TTS service) may take before CallOrchestrator.run() gives up on
    it and proceeds to the turn-processing pipeline anyway. Found via a
    live Call-002 trial: _speak_greeting() previously ran outside run()'s
    try/finally with no timeout at all -- when the GPU node was
    unreachable, the greeting call hung indefinitely, the turn-processing
    tasks (which start only after the greeting returns) never even began,
    the customer heard nothing and got no response to anything they said,
    and cleanup (recorder.close(), adapter/session release) never ran
    either, since the hang never reached run()'s finally block."""
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
    consent_gate: Any = None
    """Optional :class:`CustomerConsentPort` consulted immediately before the
    WebSocket is accepted and the greeting starts. Defense-in-depth against
    a consent revocation that landed *after* the scheduler cleared the call
    but *before* it actually connected — and the only consent check on the
    call path for inbound calls, which never went through the scheduler at
    all. ``None`` (default) uses a :class:`NullCustomerConsent` never-blocks
    stub so dev/test paths don't need a Postgres-backed adapter wired up.
    A revoked lookup here closes the WebSocket with 4003 before the customer
    ever hears audio, emits an ``consent_revoked`` audit event via the
    logger, and increments ``CALLS_BLOCKED_CONSENT_REVOKED``."""

    recording_dir: str = ""
    """When non-empty, every call gets a CallRecorder writing its transcript
    (JSONL of timestamped STT/dialogue/TTS/barge-in events) and raw
    customer/Kavya audio (two mono WAV files) to this directory —
    Sprint-029 Call-002's production acceptance instrumentation. Empty
    string (the default) disables recording entirely (no CallRecorder is
    constructed), preserving prior behavior for every existing test and
    deployment that hasn't opted in."""
    greeting_cache: "GreetingCache | None" = None
    """Optional pre-synthesised u-law greeting cache. When set and a hit
    is found for the rendered greeting text, _speak_greeting() splices the
    cached 20 ms frames straight into the outbound WebSocket at wire-cadence
    (bypassing GPU TTS entirely, cutting greeting TTFA from ~19s to ~0ms).
    On miss, or when None, the pre-existing live-TTS path runs unchanged.
    Warm-up is scheduled off the Starlette on_startup hook so process boot
    is never blocked on GPU TTS."""


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
        recorder: CallRecorder | None = None,
    ) -> None:
        import asyncio

        self._call_id = call_id
        self._tenant_id = tenant_id
        self._adapter = adapter
        self._deps = deps
        self._context = context
        self._identity_verified = identity_verified
        self._recorder = recorder

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
        # Stable-suffix orchestrator: signalled when VADSpeechEnd fires for
        # the current turn (queue sentinel already pushed). The orchestrator
        # uses this as its "safety-net" trigger: if it has not yet
        # speculatively fired an LLM task by end-of-speech, it fires now on
        # the finalized stable_prefix; if it has fired but the fired prefix
        # is stale relative to the final prefix, it cancels and refires.
        self._vad_speech_ended = asyncio.Event()
        # Debounce window before a stable prefix is considered stable enough
        # to speculatively fire an LLM turn. 250 ms matches the spec (long
        # enough to let LocalAgreement-2 stabilise across the next chunk,
        # short enough that TTFT budget doesn't collapse).
        self._stable_debounce_s: float = 0.250
        # Minimum stable word count before speculative fire is allowed. Below
        # this we simply keep accumulating — a 1- or 2-word prefix is almost
        # never enough context to plan a useful reply and firing on it just
        # wastes GPU cycles that would be cancelled milliseconds later.
        self._stable_min_words: int = 3
        self._out_seq = 0
        self._closing = False
        self._dialogue_manager: DialogueManager | None = None
        # Stream-aware μ-law framing carry: any <160-byte tail left over
        # after emitting complete 160-byte Twilio frames from a clause is
        # kept here and prefixed onto the next clause's μ-law bytes, so
        # every frame Twilio ever sees is exactly 160 bytes (20 ms @ 8 kHz
        # μ-law). Cleared on barge-in / generation advance / WS disconnect.
        self._send_ulaw_carry: bytes = b""

        # Repeat-intent watchdog state (populated during greeting playback).
        # Holds the most recent preprocessed (PCM16LE @ 16 kHz) inbound frames
        # so the watchdog can hand ~1.5 s of audio to the STT adapter every
        # ~500 ms and match the caller's transcript against a repeat-intent
        # keyword regex. Frames are appended in _pump_inbound; the watchdog
        # is spawned alongside _speak_greeting() in run().
        # 16000 Hz * 2 bytes/sample * 1.5 s ~= 48000 bytes; a per-20-ms-frame
        # deque of maxlen 80 comfortably covers the 1.5 s window.
        self._greeting_audio_buffer: deque[AudioFrame] = deque(maxlen=80)
        self._greeting_watchdog_active: bool = False

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

        recorder = CallRecorder(call_id, deps.recording_dir) if deps.recording_dir else None

        return cls(
            call_id=call_id,
            tenant_id=tenant_id,
            adapter=adapter,
            deps=deps,
            vad_engine=vad_engine,
            context=context,
            identity_verified=identity_verified,
            recorder=recorder,
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

                if self._recorder is not None:
                    self._recorder.add_inbound_audio(pre.pcm_data, int(pre.config.sample_rate))

                # Feed the repeat-intent watchdog's ring buffer while it is
                # active (i.e. during greeting playback). The watchdog polls
                # this buffer every ~500 ms; frames outside the greeting
                # window are neither buffered nor transcribed.
                if self._greeting_watchdog_active:
                    self._greeting_audio_buffer.append(pre)

                if self._turn_active and self._current_turn_queue is not None:
                    await self._current_turn_queue.put(pre)

                for event in events:
                    await self._handle_vad_event(event)

        # Carrier closed the stream — end any in-flight turn so Task B
        # doesn't block forever waiting on a sentinel that will never come.
        if self._turn_active and self._current_turn_queue is not None:
            await self._current_turn_queue.put(None)
        self._closing = True
        # Wake _run_turns: it blocks on `await self._turn_ready.wait()`, which
        # is NOT cancelled by _closing flipping to True, so without this it
        # waits forever for a turn that can never arrive (the carrier stream
        # has ended). run()'s asyncio.wait(FIRST_EXCEPTION) then never
        # returns, and release_adapter()/release_session() never run — a leak
        # of one adapter + audio session per completed call. Only observable
        # when a call ends *cleanly*: a call that ended by raising (e.g. the
        # STT bug fixed alongside this) tripped FIRST_EXCEPTION and unwound
        # normally, which is why it went unnoticed until a clean hangup.
        self._turn_ready.set()

    async def _handle_vad_event(self, event: object) -> None:
        import asyncio

        if isinstance(event, VADSpeechStart) and not self._turn_active:
            if self._greeting_watchdog_active:
                logger.info(
                    "VADSpeechStart on call %s during greeting — discarding (greeting protection active)",
                    self._call_id,
                )
                return
            self._current_turn_queue = asyncio.Queue()
            self._turn_active = True
            # New turn opening: clear the end-of-speech signal from the
            # previous turn so the stable-suffix orchestrator does not
            # observe a stale VADSpeechEnd carried over from turn N-1.
            self._vad_speech_ended.clear()
            self._turn_ready.set()
        elif isinstance(event, VADSpeechEnd) and self._turn_active:
            if self._current_turn_queue is not None:
                await self._current_turn_queue.put(None)
            self._turn_active = False
            # Signal the stable-suffix orchestrator: this turn's audio
            # stream is now closed. If it hasn't fired an LLM task yet it
            # will fire the safety-net now on whatever stable_prefix it
            # has; if it has fired but the fired prefix diverges from the
            # final prefix it will cancel-and-refire.
            self._vad_speech_ended.set()
        elif isinstance(event, BargeinDetected):
            logger.info("Barge-in detected on call %s — flushing playback", self._call_id)
            if self._recorder is not None:
                self._recorder.event("barge_in", turn_index=self._turn_index, call_time_ms=self._call_time_ms())
            await self._playback.flush()
            self._playback.clear_barge_in()
            # Drop any μ-law tail carried forward from the pre-barge-in
            # generation so the next turn's first frame is aligned to a
            # clean 160-byte boundary rather than being prefixed by stale
            # audio the caller has already been interrupted over.
            self._send_ulaw_carry = b""
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
            if self._closing and not self._turn_active and self._current_turn_queue is None:
                break  # woken purely to observe the close, not by a real turn
            await self._run_turns_one_iteration()

    async def _run_turns_one_iteration(self) -> None:
        """Stable-suffix orchestrator: consumes STT WordHypotheses as they
        stream in during speech, tracks the growing stable prefix, and
        speculatively fires an LLM/TTS turn once the prefix has been
        stable for _stable_debounce_s seconds (default 250 ms) with at
        least _stable_min_words words (default 3). If the stable prefix
        later grows materially the current LLM task is cancelled (via
        its asyncio.Event) and a new task is fired on the extended prefix.

        Every cancel-and-refire bumps playback.generation (via
        preempt_current_clause + flush) so the last-mile _send_clause
        gate guarantees no TTS clause synthesised for a superseded
        prefix ever reaches Twilio.

        Design constants:
          * debounce = 250 ms; min_words = 3.
          * materially-extends: >=2 new words OR >30% growth on fired.
          * Safety-net at VADSpeechEnd: refire on any single-word extension.
          * Empty-prefix fallback: short-circuit to no-reply.
        """
        if self._dialogue_manager is None:
            from src.services.dialogue_manager.service import DialogueManager

            self._dialogue_manager = DialogueManager(call_id=self._call_id, tenant_id=self._tenant_id)

        queue = self._current_turn_queue
        if queue is None:
            return

        import asyncio

        stt_start = time.monotonic()
        word_stream: AsyncIterator[WordHypothesis] = await self._deps.stt_service.transcribe_stream(
            _queue_to_frame_gen(queue), language=self._deps.language
        )

        stable_prefix: list[str] = []
        last_growth_ts: float = time.monotonic()
        fired_prefix: list[str] | None = None
        llm_task: asyncio.Task[list[Any]] | None = None
        llm_cancel_event: asyncio.Event | None = None
        stream_exhausted = False

        def _materially_extends(fired: list[str], current: list[str]) -> bool:
            new_words = len(current) - len(fired)
            if new_words <= 0:
                return False
            if new_words >= 2:
                return True
            base = max(len(fired), 1)
            return (new_words / base) > 0.30

        def _fire_llm(prefix_words: list[str]) -> tuple[asyncio.Task[list[Any]], asyncio.Event]:
            try:
                self._playback.preempt_current_clause()
            except Exception:
                logger.exception("preempt_current_clause failed")
            asyncio.ensure_future(self._playback.flush())
            self._playback.clear_barge_in()

            transcript = " ".join(prefix_words)
            segments = tuple(
                UtteranceSegment(text=w, start_ms=0, end_ms=0, confidence=1.0)
                for w in prefix_words
            )
            synth_turn = TurnInput(
                turn_id=str(uuid.uuid4()),
                call_id=self._call_id,
                tenant_id=self._tenant_id,
                role=TurnRole.CUSTOMER,
                transcript=transcript,
                segments=segments,
                created_at=datetime.utcnow(),
                correlation_id="",
                trace_id="",
                turn_index=self._turn_index,
            )
            cev = asyncio.Event()
            task = asyncio.create_task(
                self._deps.conversation_engine.handle_turn(
                    turn=synth_turn,
                    playback=self._playback,
                    context=self._context,
                    identity_verified=self._identity_verified,
                    cancel_event=cev,
                )
            )
            return task, cev

        async def _cancel_and_wait(task: asyncio.Task[list[Any]], cev: asyncio.Event) -> None:
            cev.set()
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                if not task.cancelled():
                    logger.debug("cancelled LLM task raised", exc_info=True)

        aiter = word_stream.__aiter__()

        async def _next_hypothesis() -> WordHypothesis | None:
            try:
                return await aiter.__anext__()
            except StopAsyncIteration:
                return None

        pending_next: asyncio.Task[WordHypothesis | None] | None = None

        try:
            while not stream_exhausted:
                if pending_next is None:
                    pending_next = asyncio.create_task(_next_hypothesis())

                now = time.monotonic()
                elapsed_since_growth = now - last_growth_ts
                debounce_remaining = max(0.0, self._stable_debounce_s - elapsed_since_growth)

                waiters: list[asyncio.Future[Any]] = [pending_next]
                if llm_task is not None:
                    waiters.append(llm_task)

                should_debounce = (
                    fired_prefix is None
                    and len(stable_prefix) >= self._stable_min_words
                )
                timeout = debounce_remaining if should_debounce else None

                done, _pending = await asyncio.wait(
                    waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )

                if pending_next in done:
                    hyp = pending_next.result()
                    pending_next = None
                    if hyp is None:
                        stream_exhausted = True
                    elif hyp.is_final and hyp.word:
                        stable_prefix.append(hyp.word)
                        last_growth_ts = time.monotonic()
                        if fired_prefix is not None and _materially_extends(fired_prefix, stable_prefix):
                            assert llm_task is not None and llm_cancel_event is not None
                            logger.info(
                                "stable-suffix: material extension after fire (fired=%d, now=%d) - cancel-and-refire",
                                len(fired_prefix), len(stable_prefix),
                            )
                            await _cancel_and_wait(llm_task, llm_cancel_event)
                            llm_task, llm_cancel_event = _fire_llm(list(stable_prefix))
                            fired_prefix = list(stable_prefix)
                elif llm_task is not None and llm_task in done:
                    pass
                else:
                    if fired_prefix is None and len(stable_prefix) >= self._stable_min_words:
                        logger.info(
                            "stable-suffix: debounce elapsed - speculative fire on %d-word prefix",
                            len(stable_prefix),
                        )
                        llm_task, llm_cancel_event = _fire_llm(list(stable_prefix))
                        fired_prefix = list(stable_prefix)
        finally:
            if pending_next is not None and not pending_next.done():
                pending_next.cancel()
                try:
                    await pending_next
                except (asyncio.CancelledError, Exception):
                    pass

        await self._vad_speech_ended.wait()

        stt_latency_ms = int((time.monotonic() - stt_start) * 1000)

        if not stable_prefix:
            if llm_task is not None and llm_cancel_event is not None:
                await _cancel_and_wait(llm_task, llm_cancel_event)
            if self._recorder is not None:
                self._recorder.event(
                    "stt_empty_turn", turn_index=self._turn_index, latency_ms=stt_latency_ms,
                    call_time_ms=self._call_time_ms(),
                )
            return

        if fired_prefix is None:
            logger.info(
                "stable-suffix: safety-net fire at VADSpeechEnd on %d-word prefix",
                len(stable_prefix),
            )
            llm_task, llm_cancel_event = _fire_llm(list(stable_prefix))
            fired_prefix = list(stable_prefix)
        elif len(stable_prefix) > len(fired_prefix):
            logger.info(
                "stable-suffix: VADSpeechEnd - final prefix (%d) extends fired (%d), refire",
                len(stable_prefix), len(fired_prefix),
            )
            assert llm_task is not None and llm_cancel_event is not None
            await _cancel_and_wait(llm_task, llm_cancel_event)
            llm_task, llm_cancel_event = _fire_llm(list(stable_prefix))
            fired_prefix = list(stable_prefix)

        assert llm_task is not None

        if self._recorder is not None:
            self._recorder.event(
                "stt_final",
                turn_index=self._turn_index,
                transcript=" ".join(stable_prefix),
                latency_ms=stt_latency_ms,
                call_time_ms=self._call_time_ms(),
            )

        dialogue_start = time.monotonic()
        try:
            clauses = await llm_task
        except asyncio.CancelledError:
            logger.warning("stable-suffix: final LLM task cancelled - no clauses to send")
            return
        dialogue_latency_ms = int((time.monotonic() - dialogue_start) * 1000)

        if self._recorder is not None:
            self._recorder.event(
                "kavya_reply",
                turn_index=self._turn_index,
                text=" ".join(c.text for c in clauses if getattr(c, "text", "")),
                dialogue_and_tts_latency_ms=dialogue_latency_ms,
                clause_count=len(clauses),
                call_time_ms=self._call_time_ms(),
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
                await self._send_clause(clause)
                self._playback.dequeue_nowait()
        finally:
            self._vad.set_playback_active(False)

    async def _send_clause(self, clause: Any) -> None:
        """Convert one AudioClause to μ-law and send as 20 ms Twilio frames.

        Twilio Media Streams expects a steady stream of ~20 ms (160-byte μ-law
        @ 8 kHz) frames per WebSocket message. Sending a whole clause (often
        hundreds of ms) as one oversized frame caused audible stutter and,
        for long clauses, exceeded Twilio's per-message payload limit — the
        callee heard "na-ma---ste s-ir" (syllables torn apart) rather than
        smooth speech. Splitting into 160-byte chunks feeds Twilio's carrier
        at its native cadence.

        Does NOT touch the PlaybackScheduler queue -- callers own that,
        because the two callers differ: _send_clauses() receives clauses
        directly from ConversationEngine's return value and drains one
        queue entry per clause afterward, while _speak_greeting() takes its
        clauses *out* of the queue itself as they stream in.

        Phase E — the last-mile generation check. Snapshot the clause's
        stamped generation once; between every 20 ms Twilio frame compare
        it against the scheduler's live generation. As soon as the
        scheduler has advanced (barge-in fired between frames), stop
        sending immediately — no further stale audio reaches Twilio for
        this clause. This is the terminal fail-closed boundary before
        the carrier: even if the scheduler and gate both let a stale
        clause slip through (which they shouldn't), this per-frame check
        still catches the mid-clause barge-in that flushed after we
        already popped the clause.
        """
        # Phase E — drop the whole clause immediately if it is already
        # stale by the time we reach here (defensive; the scheduler's
        # enqueue would already have dropped it).
        clause_generation = getattr(clause, "generation", 0)
        if clause_generation != self._playback.generation:
            logger.warning(
                "twilio_ws: dropping stale clause idx=%d "
                "(clause_gen=%d, playback_gen=%d) — never sent to Twilio",
                getattr(clause, "clause_index", -1),
                clause_generation,
                self._playback.generation,
            )
            return
        if self._recorder is not None:
            self._recorder.add_outbound_audio(clause.audio_data, clause.sample_rate)
        import time as _t, logging as _lg
        _t0 = _t.monotonic()
        _last_end = getattr(self, "_pace_last_clause_end", None)
        _gap_ms = int((_t0 - _last_end) * 1000) if _last_end else 0
        pcm_ulaw = self._audio_output.convert(clause, fmt="ulaw")
        # 20 ms of 8 kHz μ-law = 160 bytes per Twilio outbound frame.
        # Stream-aware framing: prepend any <160-byte tail left over from
        # the previous clause so every emitted payload is exactly 160
        # bytes. Anything under 160 bytes at the end of THIS clause is
        # held in self._send_ulaw_carry for the next clause. Twilio
        # silently drops non-160-byte μ-law@8kHz frames, so per-clause
        # chunking (the previous behaviour) leaked one malformed frame
        # per clause whenever len(pcm_ulaw) % 160 != 0.
        chunk_size = 160
        buf = self._send_ulaw_carry + pcm_ulaw
        n_full = len(buf) // chunk_size
        n_frames = 0
        aborted_mid_clause = False
        for i in range(n_full):
            # Phase E — between every frame verify the clause is still
            # current. Once the generation has advanced, every remaining
            # frame in this clause belongs to a discarded turn and must
            # not reach Twilio. The carry from the pre-barge-in generation
            # is dropped too (it belongs to audio the caller has already
            # been interrupted over).
            if self._playback.generation != clause_generation:
                aborted_mid_clause = True
                self._send_ulaw_carry = b""
                logger.info(
                    "twilio_ws: mid-clause barge-in — sent %d of %d frames "
                    "for clause idx=%d, generation advanced %d→%d",
                    n_frames,
                    n_full,
                    getattr(clause, "clause_index", -1),
                    clause_generation,
                    self._playback.generation,
                )
                break
            chunk = buf[i * chunk_size : (i + 1) * chunk_size]
            self._out_seq += 1
            out_frame = _clause_to_mulaw_frame(chunk, seq=self._out_seq, rtp_ts=self._out_seq * 160)
            await self._adapter.send_frame(out_frame)
            n_frames += 1
        else:
            # Clean loop exit (no barge-in): stash the <160-byte remainder
            # for the next clause. On the last clause of a call, the
            # remainder is dropped by the run() finally-block; it
            # represents <20 ms of audio (0..7.96 ms typical) which is
            # imperceptible relative to Twilio's own 20 ms cadence.
            self._send_ulaw_carry = buf[n_full * chunk_size :]
        _wall = int((_t.monotonic() - _t0) * 1000)
        _audio_ms = n_frames * 20
        _rtf = (_wall / _audio_ms) if _audio_ms else 0.0
        self._pace_last_clause_end = _t.monotonic()
        _lg.getLogger("voiceos.twilio_ws").info(
            "PACE_DIAG clause frames=%d audio_ms=%d wall_ms=%d rtf=%.2f gap_since_last_ms=%d aborted=%d",
            n_frames, _audio_ms, _wall, _rtf, _gap_ms, int(aborted_mid_clause),
        )

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
                self._outbound_frame_count = getattr(self, "_outbound_frame_count", 0) + 1
                if self._outbound_frame_count in (1, 10, 50, 200, 1000):
                    import logging as _lg
                    _lg.getLogger("voiceos.twilio_ws").info("CALL_DIAG: outbound_frame_count=%d bytes_sample=%d", self._outbound_frame_count, len(msg))
            else:
                await asyncio.sleep(0.01)
        # Drain any remaining queued messages before the connection closes.
        while (msg := self._adapter.drain_outbound()) is not None:
            await websocket.send_text(msg.decode())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Repeat-intent watchdog (runs alongside greeting playback)
    # ------------------------------------------------------------------

    # Case-insensitive whole-word match on common Hindi/Hinglish/English
    # "repeat / what did you say?" cues. If the caller utters any of these
    # while the greeting is still playing, the watchdog releases the
    # PlaybackScheduler's greeting-protection window early so the AI can
    # respond immediately instead of waiting for the full greeting to end.
    _REPEAT_INTENT_RE = re.compile(
        r"\b(repeat|dobara|kya bola|samajh nahi|kya kaha|what|pardon|excuse me|phir se)\b",
        re.IGNORECASE,
    )

    async def _repeat_intent_watchdog(self) -> None:
        """Runs during greeting playback. Buffers inbound user audio, runs a
        lightweight STT poll every ~500ms, and if the transcript matches a
        repeat-intent keyword, calls self._playback.clear_protection() to end
        greeting protection early."""
        import asyncio

        self._greeting_watchdog_active = True
        # Drop any stale frames from a previous call/greeting.
        self._greeting_audio_buffer.clear()
        try:
            while not self._closing:
                await asyncio.sleep(0.5)
                # Snapshot the ring buffer; empty => nothing to transcribe yet.
                frames = list(self._greeting_audio_buffer)
                if not frames:
                    continue

                async def _frame_gen(_frames: list[AudioFrame]) -> AsyncIterator[AudioFrame]:
                    for f in _frames:
                        yield f

                try:
                    word_stream: AsyncIterator[WordHypothesis] = (
                        await self._deps.stt_service.transcribe_stream(
                            _frame_gen(frames), language=self._deps.language
                        )
                    )
                    transcript_parts: list[str] = []
                    async for word in word_stream:
                        transcript_parts.append(word.word)
                    transcript = " ".join(transcript_parts).strip()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug(
                        "Call %s: repeat-intent watchdog STT poll failed",
                        self._call_id,
                        exc_info=True,
                    )
                    continue

                if not transcript:
                    continue

                if self._REPEAT_INTENT_RE.search(transcript):
                    logger.info(
                        "greeting repeat-intent detected: '%s' \u2014 releasing protection",
                        transcript,
                    )
                    try:
                        self._playback.clear_protection()
                    except Exception:
                        logger.exception(
                            "Call %s: clear_protection() raised", self._call_id
                        )
                    return
        except asyncio.CancelledError:
            return
        finally:
            self._greeting_watchdog_active = False
            self._greeting_audio_buffer.clear()

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
        if self._recorder is not None:
            self._recorder.event("greeting", text=greeting, call_time_ms=self._call_time_ms())
        # Greeting-cache fast path: if we have pre-synthesised u-law bytes
        # for this exact greeting text, splice them straight into the
        # outbound Twilio stream at wire cadence (20 ms per 160-byte frame).
        # This bypasses GPU TTS entirely and cuts greeting TTFA from ~19s
        # to ~5 ms. Barge-in still works because we tick playback.generation
        # through the same protected mechanism used by the live path.
        if self._deps.greeting_cache is not None:
            frames = self._deps.greeting_cache.get_frames(greeting)
            if frames:
                import asyncio as _asyncio_gc, time as _time_gc
                logger.info(
                    "twilio_ws: greeting-cache HIT (%d frames, ~%d ms) - splicing directly",
                    len(frames), len(frames) * 20,
                )
                greeting_generation = self._playback.generation
                self._playback.set_protected(greeting_generation)
                self._vad.set_playback_active(True, playback_seq=self._turn_index)
                _t_start_gc = _time_gc.monotonic()
                _first_frame_at = None
                try:
                    for i, frame_bytes in enumerate(frames):
                        # Barge-in / generation-advance check between frames.
                        if self._playback.generation != greeting_generation:
                            logger.info(
                                "twilio_ws: greeting-cache barge-in at frame %d/%d",
                                i, len(frames),
                            )
                            break
                        self._out_seq += 1
                        out_frame = _clause_to_mulaw_frame(
                            frame_bytes, seq=self._out_seq, rtp_ts=self._out_seq * 160,
                        )
                        await self._adapter.send_frame(out_frame)
                        if _first_frame_at is None:
                            _first_frame_at = _time_gc.monotonic()
                            logger.info(
                                "PACE_DIAG greeting_cache_ttfa_ms=%d",
                                int((_first_frame_at - _t_start_gc) * 1000),
                            )
                        # Pace at 20 ms/frame so we do not flood Twilio.
                        await _asyncio_gc.sleep(0.020)
                finally:
                    self._vad.set_playback_active(False)
                    self._playback.clear_protection()
                _wall_gc = int((_time_gc.monotonic() - _t_start_gc) * 1000)
                logger.info(
                    "PACE_DIAG greeting_cache_done wall_ms=%d out_seq=%d",
                    _wall_gc, self._out_seq,
                )
                return
            else:
                logger.info("twilio_ws: greeting-cache MISS - falling back to live TTS")

        # Stream the greeting out as TrueStreamingPipeline synthesises it,
        # rather than awaiting the whole list first. Measured on the real GPU:
        # the greeting takes ~19s to synthesise in full (131 clauses / ~15s of
        # audio), so buffering it meant ~19s of dead air before the customer
        # heard anything -- long enough that a real person hangs up, and long
        # enough that it tripped the greeting timeout entirely. The pipeline
        # already enqueues each clause into self._playback as it produces it,
        # so draining that queue concurrently gets first audio out in
        # roughly first-clause latency instead of full-utterance latency.
        import asyncio, time as _time

        self._pace_greeting_start = _time.monotonic()
        self._pace_greeting_first_frame_at = None
        # Phase E — the greeting also has a scope generation. If a barge-in
        # fires during greeting we must both stop sending AND cancel the
        # synthesis task so it doesn't keep producing (now-stale) clauses
        # that the scheduler would reject anyway but that would waste GPU
        # cycles.
        greeting_generation = self._playback.generation
        # Uninterruptible first greeting: protect this generation from
        # VAD-triggered barge-in until greeting_done fires (cleared in
        # the finally-block below).
        self._playback.set_protected(greeting_generation)
        synth_task = asyncio.create_task(
            self._deps.conversation_engine.speak_scripted_text(
                greeting, self._playback, tts_mode="streaming"
            )
        )
        self._vad.set_playback_active(True, playback_seq=self._turn_index)
        try:
            while not synth_task.done() or self._playback.depth > 0:
                # Phase E — observe barge-in / generation advance and
                # cancel the greeting synthesis immediately. Without
                # this, synth_task could produce dozens more stale
                # clauses before the async cascade unwinds.
                if self._playback.generation != greeting_generation:
                    if not synth_task.done():
                        logger.info(
                            "twilio_ws: greeting barge-in — cancelling "
                            "synth_task (gen advanced %d→%d)",
                            greeting_generation, self._playback.generation,
                        )
                        synth_task.cancel()
                    break
                clause = self._playback.dequeue_nowait()
                if clause is None:
                    await asyncio.sleep(0.005)
                    continue
                if getattr(self, "_pace_greeting_first_frame_at", None) is None:
                    self._pace_greeting_first_frame_at = _time.monotonic()
                    import logging as _lg2
                    _lg2.getLogger("voiceos.twilio_ws").info(
                        "PACE_DIAG greeting_ttfa_ms=%d",
                        int((self._pace_greeting_first_frame_at - self._pace_greeting_start) * 1000),
                    )
                await self._send_clause(clause)
        finally:
            self._vad.set_playback_active(False)
            import logging as _lg3
            _wall = int((_time.monotonic() - self._pace_greeting_start) * 1000)
            _lg3.getLogger("voiceos.twilio_ws").info(
                "PACE_DIAG greeting_done wall_ms=%d ttfa_ms=%d out_seq=%d",
                _wall,
                int((self._pace_greeting_first_frame_at - self._pace_greeting_start) * 1000) if getattr(self, "_pace_greeting_first_frame_at", None) else -1,
                self._out_seq,
            )
            # Uninterruptible greeting complete: restore normal barge-in
            # behavior. Inside finally so protection is cleared even on exception.
            self._playback.clear_protection()
        # Phase E — if we cancelled synth_task, awaiting it will re-raise
        # CancelledError; swallow that specifically (the cancel was
        # intentional, not a failure).
        try:
            await synth_task  # surface any synthesis exception
        except asyncio.CancelledError:
            pass

    async def run(self, websocket: WebSocket) -> None:
        import asyncio

        tasks: list[asyncio.Task[None]] = []
        try:
            if self._recorder is not None:
                self._recorder.event("call_start", tenant_id=self._tenant_id)

            tasks = [
                asyncio.create_task(self._pump_inbound()),
                asyncio.create_task(self._run_turns()),
                asyncio.create_task(self._pump_outbound(websocket)),
            ]

            async def _speak_greeting_task() -> None:
                from src.services.media_gateway.metrics import GREETING_OUTCOMES

                watchdog_task = asyncio.create_task(self._repeat_intent_watchdog())
                try:
                    try:
                        await asyncio.wait_for(self._speak_greeting(), timeout=self._deps.greeting_timeout_s)
                        GREETING_OUTCOMES.labels(outcome="ok").inc()
                    except TimeoutError:
                        GREETING_OUTCOMES.labels(outcome="timeout").inc()
                        logger.error(
                            "Call %s: greeting timed out after %.1fs (GPU TTS unreachable/slow?) — "
                            "proceeding to turn processing without it",
                            self._call_id,
                            self._deps.greeting_timeout_s,
                        )
                        if self._recorder is not None:
                            self._recorder.event(
                                "greeting_timeout", timeout_s=self._deps.greeting_timeout_s,
                                call_time_ms=self._call_time_ms(),
                            )
                    except Exception:
                        GREETING_OUTCOMES.labels(outcome="error").inc()
                        logger.exception("Call %s: greeting failed — proceeding to turn processing without it", self._call_id)
                        if self._recorder is not None:
                            self._recorder.event("greeting_error", call_time_ms=self._call_time_ms())
                finally:
                    watchdog_task.cancel()
                    try:
                        await watchdog_task
                    except (asyncio.CancelledError, Exception):
                        pass

            if self._deps.speak_greeting:
                tasks.append(asyncio.create_task(_speak_greeting_task()))
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            self._closing = True
            # Drop any μ-law tail carried between clauses so it does not
            # linger on the (dying) instance. Under Twilio's stream
            # contract a sub-160-byte tail cannot be emitted as a valid
            # frame — dropping is the correct terminal behaviour and
            # represents <20 ms of trailing audio.
            self._send_ulaw_carry = b""
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._deps.media_gateway_service.release_adapter(self._call_id)
            self._deps.audio_session_manager_service.release_session(self._call_id)
            if self._recorder is not None:
                self._recorder.event(
                    "call_end", total_turns=self._turn_index, call_time_ms=self._call_time_ms()
                )
                self._recorder.close()


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

        start = start_msg.get("start", {}) if isinstance(start_msg, dict) else {}
        msg_call_sid = start.get("callSid", "") or ""
        msg_stream_sid = start.get("streamSid", "") or ""
        msg_account_sid = start.get("accountSid", "") or ""
        custom_params = start.get("customParameters", {}) or {}
        # Twilio Media Streams: <Parameter name="admission_token" value="…"/>
        # inside <Stream> is forwarded to WSS in start.customParameters. Case
        # is preserved by Twilio; the /voice handler always emits the
        # lower-case key so lookup is deterministic.
        admission_token = custom_params.get("admission_token", "") or ""

        call_id = msg_call_sid or msg_stream_sid
        if not call_id:
            logger.error("Twilio WS start message missing both callSid and streamSid — closing 4001")
            await websocket.close(code=4001)
            return

        # AR-2 (auth-before-allocation): admission verification runs BEFORE
        # any per-call resource is allocated (no orchestrator, no session
        # gate admit, no VAD engine, no ConversationEngine.start_call).
        # A failed admission increments the rejection metric via
        # session_gate.reject() and closes the WebSocket immediately.
        expected_account_sid = deps.account_sid
        # Fail-closed on missing accountSid in the start message: a valid
        # Twilio connection always includes it; passing "" to the registry
        # never matches any ticket (issue() rejects empty account_sid) so
        # the account-bound isolation invariant holds.
        supplied_account_sid = msg_account_sid
        if not admission_token:
            reason = "missing_admission_token"
            logger.warning(
                "WSS admission rejected: %s call_sid=%s stream_sid=%s",
                reason, msg_call_sid, msg_stream_sid,
            )
            deps.media_gateway_service.gate.reject(
                call_id=call_id, reason=reason, adapter_type=ADAPTER_TYPE_TWILIO,
            )
            await websocket.close(code=4401)
            return

        ok, reason, ticket = await deps.admission_registry.verify_and_consume(
            token=admission_token,
            call_sid=msg_call_sid,
            account_sid=supplied_account_sid,
        )
        if not ok:
            logger.warning(
                "WSS admission rejected: %s call_sid=%s account_sid=%s",
                reason, msg_call_sid, supplied_account_sid,
            )
            deps.media_gateway_service.gate.reject(
                call_id=call_id, reason=reason, adapter_type=ADAPTER_TYPE_TWILIO,
            )
            await websocket.close(code=4401)
            return

        # Defense-in-depth: the ticket's account_sid was checked by
        # verify_and_consume, but we also verify against the process's
        # configured expected account_sid — a token minted under this
        # process cannot be honored against a different expected account.
        if ticket is not None and ticket.account_sid != expected_account_sid:
            logger.warning(
                "WSS admission rejected: admission_token_account_expected_mismatch "
                "expected=%s ticket=%s",
                expected_account_sid, ticket.account_sid,
            )
            deps.media_gateway_service.gate.reject(
                call_id=call_id,
                reason="admission_token_account_expected_mismatch",
                adapter_type=ADAPTER_TYPE_TWILIO,
            )
            await websocket.close(code=4401)
            return

        # Admission verified. Construct credentials that the adapter's
        # authenticate() will accept via the admission-verified branch —
        # `admission_verified="true"` is a private in-process signal set
        # only by this handler AFTER the registry consume succeeded, and
        # AR-2 is preserved (adapter.authenticate still runs inside
        # admit_adapter, and gate.admit still runs before adapter.connect).
        credentials = {
            "account_sid": expected_account_sid,
            "auth_token": deps.auth_token,
            "expected_account_sid": expected_account_sid,
            "admission_verified": "true",
            "admission_call_sid": msg_call_sid,
        }

        # Assemble the real, authoritative CustomerContext from the
        # customer_id embedded in <Stream><Parameter/>. Two production
        # sources of that parameter:
        #   * Outbound trials (scripts/place_call002.py) attach it directly
        #     to the TwiML before Twilio dials.
        #   * Inbound/outbound-api calls resolve it via the /voice handler
        #     above, which does ANI→CustomerService.find_by_phone() before
        #     minting TwiML (Gate 3D P1). The value WSS reads here is thus
        #     authoritative-by-origin even though customParameters as a
        #     channel is caller-controlled.
        # When no customer_id is present (CRM miss, wrong number, /voice
        # skipped the lookup), context stays None and DialogueResponseEngine
        # falls through to clarify_no_record rather than fabricating a
        # balance (Fix D — see engines/dialogue_response/engine.py
        # _resolve_outstanding_minor).
        customer_id = start_msg.get("start", {}).get("customParameters", {}).get("customer_id", "")

        # Consent-revocation defense-in-depth (V4 Ch2 — RBI FPC / DPDP).
        # Runs *before* start_call() and CallOrchestrator.create() so a
        # revoked customer never triggers CRM lookups, session allocation,
        # or a single frame of Kavya audio. Skipped when customer_id is
        # empty (the scheduler path already enforces DND per (tenant,
        # customer); this gate only kicks in when we know who we're
        # talking to). Any exception here is swallowed with a log rather
        # than fail-closed because a broken consent adapter must not take
        # every call down — the phone-scoped DND at dial time and the
        # scheduler's DNDStatusPort both remain in force.
        consent_gate = deps.consent_gate
        if consent_gate is None:
            from src.services.media_gateway.consent_gate import NullCustomerConsent
            consent_gate = NullCustomerConsent()
        if customer_id:
            try:
                if consent_gate.is_revoked(deps.tenant_id, customer_id):
                    logger.warning(
                        "consent_revoked call_id=%s tenant=%s customer=%s — closing call before greeting",
                        call_id, deps.tenant_id, customer_id,
                    )
                    from src.services.media_gateway.metrics import record_call_blocked_consent_revoked
                    record_call_blocked_consent_revoked(deps.tenant_id)
                    await websocket.close(code=4003)
                    return
            except Exception:
                logger.exception(
                    "consent_gate lookup failed for tenant=%s customer=%s — proceeding (fail-open, phone-DND and schedule-DND remain in force)",
                    deps.tenant_id, customer_id,
                )

        context = None
        if customer_id:
            try:
                context = deps.conversation_engine.start_call(
                    tenant_id=TenantId(deps.tenant_id), customer_id=customer_id, call_id=call_id
                )
            except Exception:
                logger.exception("start_call() failed for customer_id=%s call_id=%s — proceeding without context", customer_id, call_id)
        else:
            # No customer_id on the WSS start event — either the outbound
            # placement did not attach a <Parameter name="customer_id"/> to
            # <Stream>, or /voice's ANI→CustomerService lookup found no
            # matching customer for this phone number. Downstream will
            # render the clarify_no_record template rather than fabricate
            # a balance (see engines/dialogue_response/engine.py
            # _resolve_outstanding_minor).
            logger.warning(
                "twilio_ws: no customer_id on WSS start.customParameters (call_id=%s) — "
                "CustomerContext will be None and dialogue will use clarify_no_record template",
                call_id,
            )

        orchestrator = await CallOrchestrator.create(
            call_id=call_id,
            tenant_id=deps.tenant_id,
            credentials=credentials,
            deps=deps,
            context=context,
        )
        if orchestrator is None:
            await websocket.close(code=4003)
            return

        orchestrator.feed_message(start_msg)  # feed the already-consumed 'start' message
        # CALL_DIAG fix: populate stream_sid synchronously so outbound greeting
        # frames (enqueued before _pump_inbound processes the queued start msg)
        # carry the correct streamSid — Twilio silently drops frames with empty
        # streamSid, causing 30s of dead-air greeting → 1006 disconnect.
        _sid = start_msg.get('start', {}).get('streamSid', '')
        if _sid:
            orchestrator._adapter._stream_sid = _sid

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
            # Phase 3: persist post-call state (RelationshipMemory, PostCallSummary).
            # Best-effort — never re-raise; the WS is already closing at this point.
            try:
                deps.conversation_engine.end_call(
                    call_id,
                    outcome="completed",
                    customer_id=customer_id,
                )
            except Exception:
                logger.exception(
                    "end_call() failed in finally block for call_id=%s — continuing WS teardown",
                    call_id,
                )

    def _public_http_base_url() -> str:
        """Public HTTPS base URL Twilio POSTs /voice to — derived from the
        wss:// PUBLIC_WS_BASE_URL used for the Media Stream upgrade so
        signature validation runs against the exact URL Twilio signed."""
        base = deps.public_ws_base_url or ""
        if base.startswith("wss://"):
            return "https://" + base[len("wss://"):]
        if base.startswith("ws://"):
            return "http://" + base[len("ws://"):]
        return base

    async def _voice(request):
        """HTTP webhook handler.

        Twilio POSTs form-encoded call metadata (CallSid, AccountSid, From,
        To, …) with an X-Twilio-Signature header covering the URL and
        alphabetically-sorted POST param key/value pairs. This handler:

          1. Reads the form body and X-Twilio-Signature header.
          2. Reconstructs the public URL Twilio actually signed against
             (behind a tunnel/reverse-proxy the local request URL differs
             from what Twilio signed — see public_ws_base_url docstring).
          3. Validates the HMAC-SHA1 signature via validate_twilio_signature.
          4. Enforces AccountSid == deps.account_sid (cross-account defense).
          5. Mints a single-use admission token bound to (CallSid,
             AccountSid, tenant_id) via AdmissionRegistry.
          6. Emits TwiML with <Stream><Parameter name="admission_token"
             value="…"/> so Twilio forwards it back on the WSS upgrade.

        Any failure returns a 403 with no TwiML — Twilio will fail the
        call cleanly rather than opening a WebSocket that gets rejected
        by admission verification (which would be the same outcome, but
        wastes a WS handshake and pollutes the WSS rejection metrics).
        """
        # Parse Twilio's application/x-www-form-urlencoded body with stdlib
        # so this handler carries no runtime dependency on python-multipart
        # (which starlette.Request.form() would pull in even for URL-encoded
        # bodies in some starlette versions). Multipart bodies are not
        # supported here — Twilio only sends URL-encoded form data.
        try:
            raw_body = await request.body()
        except Exception:
            logger.warning("/voice rejected: body read error")
            return Response("Bad Request", status_code=400, media_type="text/plain")

        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type != "application/x-www-form-urlencoded":
            logger.warning("/voice rejected: unsupported content-type %r", content_type)
            return Response("Unsupported Media Type", status_code=415, media_type="text/plain")

        from urllib.parse import parse_qsl as _parse_qsl
        try:
            params = dict(_parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True))
        except Exception:
            logger.warning("/voice rejected: form parse error")
            return Response("Bad Request", status_code=400, media_type="text/plain")

        signature = request.headers.get("x-twilio-signature", "")
        if not signature:
            logger.warning("/voice rejected: missing X-Twilio-Signature header")
            return Response("Forbidden", status_code=403, media_type="text/plain")

        public_base = _public_http_base_url()
        if public_base:
            signed_url = f"{public_base.rstrip('/')}{request.url.path}"
        else:
            signed_url = str(request.url)

        if not validate_twilio_signature(deps.auth_token, signed_url, params, signature):
            logger.warning(
                "/voice rejected: invalid X-Twilio-Signature url=%s call_sid=%s",
                signed_url, params.get("CallSid", ""),
            )
            return Response("Forbidden", status_code=403, media_type="text/plain")

        call_sid = params.get("CallSid", "") or ""
        account_sid = params.get("AccountSid", "") or ""
        if not call_sid or not account_sid:
            logger.warning(
                "/voice rejected: missing CallSid or AccountSid in POST body (have=%s)",
                sorted(params.keys()),
            )
            return Response("Bad Request", status_code=400, media_type="text/plain")

        if account_sid != deps.account_sid:
            logger.warning(
                "/voice rejected: AccountSid mismatch expected=%s got=%s",
                deps.account_sid, account_sid,
            )
            return Response("Forbidden", status_code=403, media_type="text/plain")

        # ANI → CustomerService lookup (RI-5: authoritative-by-origin).
        # We resolve customer_id from the Twilio-supplied phone number
        # BEFORE minting TwiML. The result is embedded as a <Parameter/>
        # so WSS receives a customer_id whose sole provenance is our own
        # CRM lookup — customParameters as a channel is caller-controlled,
        # but this specific value is not caller-provided.
        #   * Direction=inbound        → customer is the caller (From)
        #   * Direction=outbound-api   → customer is the callee (To)
        # When lookup fails (no CRM match, no CustomerService configured,
        # or empty phone), we omit the parameter entirely. WSS will then
        # fall through to clarify_no_record instead of fabricating any
        # balance (Fix D).
        resolved_customer_id: str = ""
        direction = (params.get("Direction", "") or "").lower()
        from_number = params.get("From", "") or ""
        to_number = params.get("To", "") or ""
        caller_phone = to_number if "outbound" in direction else from_number
        if deps.customer_service is not None and caller_phone:
            try:
                customer = deps.customer_service.find_by_phone(
                    TenantId(deps.tenant_id), caller_phone,
                )
            except Exception as exc:
                logger.warning(
                    "/voice CRM lookup failed call_sid=%s phone=%s err=%s",
                    call_sid, caller_phone, exc,
                )
                customer = None
            if customer is not None:
                resolved_customer_id = str(customer.customer_id)
                logger.info(
                    "/voice resolved customer call_sid=%s direction=%s phone=%s customer_id=%s",
                    call_sid, direction, caller_phone, resolved_customer_id,
                )
            else:
                logger.info(
                    "/voice no CRM match call_sid=%s direction=%s phone=%s "
                    "(WSS will render clarify_no_record)",
                    call_sid, direction, caller_phone,
                )
        else:
            logger.info(
                "/voice skipping CRM lookup call_sid=%s customer_service=%s phone=%r",
                call_sid, deps.customer_service is not None, caller_phone,
            )

        ticket = await deps.admission_registry.issue(
            call_sid=call_sid,
            account_sid=account_sid,
            tenant_id=deps.tenant_id,
        )

        host = request.headers.get("host") or request.url.hostname
        wss = f"wss://{host}/twilio/media-stream"
        # secrets.token_urlsafe uses only URL-safe base64 [-_A-Za-z0-9] so
        # no XML-attribute escaping is required for value. Escape anyway
        # so a future token-format change cannot silently break TwiML.
        import xml.sax.saxutils as _xmlutils
        token_attr = _xmlutils.quoteattr(ticket.token)
        wss_attr = _xmlutils.quoteattr(wss)
        customer_param = ""
        if resolved_customer_id:
            customer_attr = _xmlutils.quoteattr(resolved_customer_id)
            customer_param = f"<Parameter name=\"customer_id\" value={customer_attr}/>"
        xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            f"<Response><Connect><Stream url={wss_attr}>"
            f"<Parameter name=\"admission_token\" value={token_attr}/>"
            f"{customer_param}"
            "</Stream></Connect></Response>"
        )
        return Response(xml, media_type="application/xml")

    async def _health(request):
        return Response("ok", media_type="text/plain")

    # V3 Ch12 §12.6/§12.9 — /health/live is a process-alive check with no
    # dependency I/O; /health/ready adds every GPU adapter (STT/LLM/TTS) so
    # k8s/systemd/LB stops routing calls when the GPU node is unreachable.
    # The naive /health above is preserved for backward compatibility with
    # existing Twilio configurations and older monitors.
    import os as _os
    from starlette.responses import JSONResponse as _JSONResponse

    from src.libs.health.probe import LivenessProbe, ReadinessProbe
    from src.libs.health.protocol import HealthStatus
    from src.services.media_gateway.health_checks import build_gpu_health_checks

    _liveness = LivenessProbe()
    _gpu_host = _os.environ.get("GPU_HOST", "")
    _readiness = ReadinessProbe(
        _liveness,
        dependencies=build_gpu_health_checks(_gpu_host) if _gpu_host else (),
    )

    async def _health_live(_request):
        status = await _liveness.check()
        return _JSONResponse(
            {"status": status.value},
            status_code=200 if status == HealthStatus.HEALTHY else 503,
        )

    async def _health_ready(_request):
        status = await _readiness.check()
        return _JSONResponse(
            {"status": status.value},
            status_code=200 if status == HealthStatus.HEALTHY else 503,
        )

    return Starlette(routes=[
        WebSocketRoute("/twilio/media-stream", endpoint=_endpoint),
        Route("/voice", endpoint=_voice, methods=["POST","GET"]),
        Route("/health", endpoint=_health, methods=["GET"]),
        Route("/health/live", endpoint=_health_live, methods=["GET"]),
        Route("/health/ready", endpoint=_health_ready, methods=["GET"]),
    ])
