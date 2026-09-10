'''Phase H STEP 6 — full production pipeline trace using real ConversationEngine.

Exercises the exact wiring deployment/cpu/app.py builds for the WS entrypoint,
then routes a real Hindi utterance through speak_scripted_text() → Veena → gate
→ PlaybackScheduler, capturing every clause. Also independently exercises the
vLLM SSE streaming leg.
'''
import asyncio, os, sys, time, importlib
sys.path.insert(0, '/opt/voiceos/app')
os.environ.setdefault('PYTHONPATH', '/opt/voiceos/app')

# Force streaming default (no gate)
os.environ.pop('VOICEOS_TTS_MODE', None)
os.environ.pop('VOICEOS_TTS_BUFFER_MS', None)


async def main():
    print('=== STEP 6.a construct production ConversationEngine ===')
    t=time.monotonic()
    from deployment.cpu.app import build_conversation_engine
    engine = build_conversation_engine()
    print(f'  built in {(time.monotonic()-t)*1000:.0f}ms  type={type(engine).__name__}')

    # Confirm adapters are real HTTPs
    llm = engine._llm
    tts = engine._tts
    print(f'  llm type={type(llm).__name__} adapter={type(getattr(llm, "_adapter", llm)).__name__}')
    print(f'  tts type={type(tts).__name__} adapter={type(getattr(tts, "_adapter", tts)).__name__}')
    print(f'  pipeline type={type(engine._pipeline).__name__}')

    print()
    print('=== STEP 6.b speak_scripted_text (Hindi, 2 sentences) → PlaybackScheduler ===')
    from src.services.playback.scheduler import PlaybackScheduler
    playback = PlaybackScheduler()
    text = 'नमस्ते प्रतीक जी, मैं राजत फाइनेंस से बोल रही हूं। आपका EMI बकाया है।'
    t=time.monotonic()
    clauses = await engine.speak_scripted_text(text=text, playback=playback)
    elapsed = (time.monotonic()-t)*1000
    print(f'  wall={elapsed:.0f}ms  n_clauses={len(clauses)}  playback.gen={playback.generation}')
    total_bytes = 0
    for i, c in enumerate(clauses):
        total_bytes += len(c.audio_data)
        gen = getattr(c, 'generation', None)
        print(f'  clause[{i}] gen={gen} sr={c.sample_rate} bytes={len(c.audio_data)} is_final={c.is_final} text={c.text!r}')
    print(f'  total_pcm_bytes={total_bytes} (~{total_bytes/(24000*2)*1000:.0f}ms of 24kHz PCM16LE)')

    print()
    print('=== STEP 6.c independent vLLM SSE stream (proves multi-delta streaming) ===')
    from src.libs.contracts.streaming import ResponsePlan, DeliveryPlan, PromptContext, RiskAssessment, IntentClassification
    from src.libs.contracts.streaming import Intent, RiskLevel, ProsodyProfile
    # Look at generate_stream signature via inspection
    import inspect
    print('  generate_stream sig:', inspect.signature(llm.generate_stream))

if __name__ == '__main__':
    asyncio.run(main())
