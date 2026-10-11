'''STEP 6 lean trace: first-clause latency, text-split proof, is_final, LLM leg.'''
import asyncio, os, sys, time, collections
sys.path.insert(0, '/opt/voiceos/app')
os.environ.setdefault('PYTHONPATH', '/opt/voiceos/app')
os.environ.pop('VOICEOS_TTS_MODE', None)
os.environ.pop('VOICEOS_TTS_BUFFER_MS', None)

async def main():
    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler

    engine = build_conversation_engine()
    print('adapters: llm=%s tts=%s pipeline=%s' % (
        type(engine._llm._adapter).__name__ if hasattr(engine._llm,'_adapter') else type(engine._llm).__name__,
        type(engine._tts._adapter).__name__ if hasattr(engine._tts,'_adapter') else type(engine._tts).__name__,
        type(engine._pipeline).__name__))

    # ---- TTS/pipeline leg ----
    print()
    print('=== 6.b speak_scripted_text ===')
    playback = PlaybackScheduler()
    text = 'नमस्ते प्रतीक जी, मैं राजत से बोल रही हूं। आपका EMI बकाया है।'

    # monkey-patch playback.enqueue to capture arrival timestamps
    arrivals = []
    orig_enq = playback.enqueue
    t0 = time.monotonic()
    def _spy(clause):
        arrivals.append((time.monotonic()-t0, len(clause.audio_data), getattr(clause,'generation',None), clause.text, clause.is_final))
        return orig_enq(clause)
    playback.enqueue = _spy

    t=time.monotonic()
    clauses = await engine.speak_scripted_text(text=text, playback=playback)
    wall = (time.monotonic()-t)*1000
    if arrivals:
        first = arrivals[0][0]*1000
    else:
        first = -1
    print(f'wall_ms={wall:.0f} first_arrival_ms={first:.0f} n_arrivals={len(arrivals)} n_returned={len(clauses)}')
    # Distinct clause texts (sentence-split proof)
    texts = collections.Counter(a[3] for a in arrivals)
    print(f'distinct_texts={len(texts)}')
    for txt, count in texts.items():
        first_idx = next(i for i,a in enumerate(arrivals) if a[3]==txt)
        first_ts = arrivals[first_idx][0]*1000
        print(f'  text[{first_idx}] first_ms={first_ts:.0f} n_chunks={count} -> {txt!r}')
    # is_final position
    final_idx = [i for i,a in enumerate(arrivals) if a[4]]
    print(f'is_final indices={final_idx}')
    # generation propagation
    gens = set(a[2] for a in arrivals)
    print(f'generation_values={gens} playback.gen={playback.generation}')
    total = sum(a[1] for a in arrivals)
    print(f'total_pcm_bytes={total} ~audio_ms={total/(24000*2)*1000:.0f}')

    # ---- LLM leg: prove multi-delta streaming through production vLLMAdapter ----
    print()
    print('=== 6.c vLLMAdapter.generate_stream (production adapter, real endpoint) ===')
    from src.libs.contracts.streaming import (
        ResponsePlan, DeliveryPlan, PromptContext, RiskAssessment, IntentClassification, ProsodyProfile,
    )
    from src.libs.contracts import streaming as S
    # Build a minimal ResponsePlan — inspect fields
    import dataclasses as dc, inspect
    # ResponsePlan may be pydantic
    print('ResponsePlan fields:', list(ResponsePlan.model_fields.keys()) if hasattr(ResponsePlan,'model_fields') else 'unknown')

if __name__=='__main__':
    asyncio.run(main())
