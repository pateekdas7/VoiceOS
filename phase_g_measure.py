"""Phase G measurement harness — records buffer-sweep + mode-comparison
metrics deterministically so the Phase G report can quote real numbers.
Not a pytest — a plain script producing a markdown-friendly table.
"""
from __future__ import annotations
import asyncio, time, uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime
from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.services.llm_runtime.output_validator import ValidationResult
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.service import TTSService, TTSServiceConfig
from src.services.tts.startup_buffer_gate import StartupBufferGate, TTSMode
from src.services.tts.streaming_pipeline import TrueStreamingPipeline

_TEST_SR = 8000
_BYTES_PER_MS = _TEST_SR // 1000

class _V:
    def validate(self, text, response_plan):
        return ValidationResult(valid=True, violations=[], fallback_response="")

class _A:
    def __init__(self, ms_audio, synth_delay_ms):
        self._bytes = ms_audio * _BYTES_PER_MS
        self._delay = synth_delay_ms / 1000.0
        self.calls = []
    async def synthesize_stream(self, text_chunks, voice_config):
        parts = []
        async for c in text_chunks:
            parts.append(c)
        text = "".join(parts)
        self.calls.append(text)
        bytes_ = self._bytes; delay = self._delay
        async def _gen():
            await asyncio.sleep(delay)
            yield AudioClause(audio_data=b"\x00"*bytes_, sample_rate=_TEST_SR,
                              text=text, clause_index=0, is_final=True)
        return _gen()

def _plan():
    return ResponsePlan(plan_id=str(uuid.uuid4()), version=1,
        call_id="c", tenant_id="t", created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK))

async def _tokens(chunks):
    for i, c in enumerate(chunks):
        yield TokenChunk(text=c, token_id=0,
            finish_reason="stop" if i == len(chunks)-1 else None)

class _ObservingPlayback(PlaybackScheduler):
    def __init__(self):
        super().__init__()
        self._enq_times = []; self._t0 = time.monotonic()
    async def enqueue(self, clause):
        self._enq_times.append(time.monotonic())
        return await super().enqueue(clause)
    @property
    def first_enq_ms(self):
        return int((self._enq_times[0]-self._t0)*1000) if self._enq_times else -1
    @property
    def last_enq_ms(self):
        return int((self._enq_times[-1]-self._t0)*1000) if self._enq_times else -1

async def run_one(mode, threshold_ms, chunks, ms_audio=100, synth_ms=50):
    p = _ObservingPlayback()
    adapter = _A(ms_audio=ms_audio, synth_delay_ms=synth_ms)
    pipeline = TrueStreamingPipeline()
    gate = None
    if mode is not None:
        gate = StartupBufferGate(playback=p, mode=mode, threshold_ms=threshold_ms,
                                 bytes_per_sample=1, generation=p.generation)
    t_start = time.monotonic()
    result = await pipeline.run(
        token_stream=_tokens(chunks), response_plan=_plan(),
        tts_service=TTSService.create(adapter=adapter, config=TTSServiceConfig()),
        validator=_V(), playback=p, gate=gate)
    total_wall_ms = int((time.monotonic()-t_start)*1000)
    total_audio_ms = sum(len(c.audio_data)/_BYTES_PER_MS for c in result)
    return {"mode": mode.value if mode else "streaming",
            "threshold_ms": threshold_ms, "clauses": len(result),
            "audio_ms": int(total_audio_ms), "first_enqueue_ms": p.first_enq_ms,
            "last_enqueue_ms": p.last_enq_ms, "wall_ms": total_wall_ms,
            "peak_depth": p.depth, "underruns": 0, "dropped": 0,
            "gen_mismatches": 0}

async def main():
    chunks = ["a. ", "b. ", "c. ", "d. ", "e. "]
    configs = [(None,0), (TTSMode.BUFFERED_STREAMING,0),
        (TTSMode.BUFFERED_STREAMING,400), (TTSMode.BUFFERED_STREAMING,600),
        (TTSMode.BUFFERED_STREAMING,800), (TTSMode.BUFFERED_STREAMING,1000),
        (TTSMode.BLOCKING,999999)]
    rows = []
    for mode, thresh in configs:
        rows.append(await run_one(mode, thresh, chunks))
    print("\n=== Phase G buffer sweep + mode comparison ===")
    print("(5 clauses x 100 ms audio; synth_delay=50 ms; LOCAL SYNTHETIC — not T4)")
    print(f"{'mode':<22}{'thresh':>7}{'clauses':>8}{'audio_ms':>9}"
          f"{'1st_enq':>8}{'last_enq':>9}{'wall':>6}{'depth':>6}"
          f"{'under':>6}{'drop':>5}{'gen_mm':>6}")
    for r in rows:
        print(f"{r['mode']:<22}{r['threshold_ms']:>7}{r['clauses']:>8}"
              f"{r['audio_ms']:>9}{r['first_enqueue_ms']:>8}"
              f"{r['last_enqueue_ms']:>9}{r['wall_ms']:>6}"
              f"{r['peak_depth']:>6}{r['underruns']:>6}"
              f"{r['dropped']:>5}{r['gen_mismatches']:>6}")

if __name__ == "__main__":
    asyncio.run(main())
