'''T3 re-run — barge-in during buffering (buffered_streaming@400) with fixed-time trigger.

Instead of waiting for gate.enqueue monkey-patch to fire (which failed due to
import-order), use a fixed sleep long enough for Veena to produce a few
super-frames but shorter than the ~5s the gate takes to accumulate its
threshold and release, and read gate state directly by digging into the pipeline.
Fires flush at t=2s wall — well after first Veena chunk (~85ms) and well
before enough audio has accumulated for a natural release plus TTFA.
'''
import asyncio, os, sys, time, io, logging, json
sys.path.insert(0, '/opt/voiceos/app')

LOG_BUF = io.StringIO()
_h = logging.StreamHandler(LOG_BUF)
_h.setLevel(logging.INFO)
_h.setFormatter(logging.Formatter('%(name)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(_h)
logging.getLogger().setLevel(logging.INFO)

TEXT = 'नमस्ते प्रतीक जी, मैं राजत फाइनेंस से बोल रही हूं। आपका EMI बकाया है।'

async def main():
    os.environ['VOICEOS_TTS_MODE'] = 'buffered_streaming'
    os.environ['VOICEOS_TTS_BUFFER_MS'] = '400'
    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler
    engine = build_conversation_engine()
    playback = PlaybackScheduler()
    accepted = []
    rejected = []
    t0 = time.monotonic()
    orig = playback.enqueue
    async def spy(c):
        cg = getattr(c,'generation',0)
        if cg != playback.generation:
            rejected.append((time.monotonic()-t0, cg, playback.generation))
        else:
            accepted.append((time.monotonic()-t0, cg, len(c.audio_data)))
        return await orig(c)
    playback.enqueue = spy
    task = asyncio.create_task(engine.speak_scripted_text(text=TEXT, playback=playback))
    # Fixed trigger: flush at 1200ms — Veena has produced ~14 super-frames (~1.2s of audio)
    # by that time; 400ms threshold would have been long-since crossed by natural release.
    # So this actually tests barge-in AFTER buffered_streaming naturally released.
    # Instead trigger at 200ms — Veena has produced ~2 chunks (~170ms of audio, buffered_ms<400)
    await asyncio.sleep(0.20)
    gen_before = playback.generation
    accepted_before_flush = len(accepted)
    depth_before = playback.depth
    t_flush = time.monotonic()-t0
    await playback.flush()
    gen_after = playback.generation
    depth_after_flush = playback.depth
    try:
        clauses = await asyncio.wait_for(task, timeout=45)
    except Exception as e:
        clauses = []
        print(f'pipeline aborted: {type(e).__name__}: {e}')
    arrivals_after_flush = [a for a in accepted if a[0] > t_flush]
    rejections_after_flush = [r for r in rejected if r[0] > t_flush]
    result = {
        'test': 'T3_barge_in_during_buffering_v2',
        't_flush_ms': round(t_flush*1000),
        'gen_before': gen_before,
        'gen_after': gen_after,
        'accepted_before_flush': accepted_before_flush,
        'depth_before_flush': depth_before,
        'depth_after_flush': depth_after_flush,
        'accepted_total_after_pipeline_end': len(accepted),
        'rejected_total': len(rejected),
        'arrivals_after_flush_ms': [round(a[0]*1000) for a in arrivals_after_flush[:5]],
        'stale_arrivals_after_flush': sum(1 for a in arrivals_after_flush if a[1] != gen_after),
        'clean_arrivals_after_flush': sum(1 for a in arrivals_after_flush if a[1] == gen_after),
        'rejections_after_flush': len(rejections_after_flush),
        'n_returned_by_pipeline': len(clauses),
    }
    print(json.dumps(result, indent=2))
    print('\n=== relevant log lines ===')
    for l in LOG_BUF.getvalue().splitlines():
        if any(k in l for k in ('StartupBufferGate','PlaybackScheduler','TrueStreamingPipeline')):
            print('  ' + l[:200])

if __name__=='__main__':
    asyncio.run(main())
