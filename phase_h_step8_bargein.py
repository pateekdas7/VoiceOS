'''Phase H STEP 8 — controlled barge-in validation on live Kaggle T4x2.

Runs 7 barge-in scenarios against real production ConversationEngine →
TrueStreamingPipeline → Veena → StartupBufferGate → PlaybackScheduler,
plus a synthetic race test for post-clear_barge_in stale delivery.

Every test wraps PlaybackScheduler.enqueue to count accepted vs. rejected
(stale) clauses via the existing generation gate.
'''
import asyncio, os, sys, time, contextlib, io, logging
sys.path.insert(0, '/opt/voiceos/app')

# Capture stderr warnings from scheduler/gate/pipeline
LOG_BUF = io.StringIO()
_h = logging.StreamHandler(LOG_BUF)
_h.setLevel(logging.WARNING)
_h.setFormatter(logging.Formatter('%(name)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(_h)
logging.getLogger().setLevel(logging.WARNING)


TEXT_2S = 'नमस्ते प्रतीक जी, मैं राजत फाइनेंस से बोल रही हूं। आपका EMI बकाया है।'
TEXT_3S = TEXT_2S + ' कृपया आज ही भुगतान करें।'


def reset_env(mode=None, buffer_ms=None):
    os.environ.pop('VOICEOS_TTS_MODE', None)
    os.environ.pop('VOICEOS_TTS_BUFFER_MS', None)
    if mode is not None: os.environ['VOICEOS_TTS_MODE'] = mode
    if buffer_ms is not None: os.environ['VOICEOS_TTS_BUFFER_MS'] = str(buffer_ms)
    for m in list(sys.modules):
        if m.startswith('src.services.tts.startup_buffer_gate'):
            del sys.modules[m]


def make_scheduler_with_spy(PlaybackScheduler):
    playback = PlaybackScheduler()
    counters = {'accepted': 0, 'rejected_stale': 0, 'arrivals': [], 'rejections': []}
    orig = playback.enqueue
    t0 = time.monotonic()
    async def spy(clause):
        cg = getattr(clause, 'generation', 0)
        if cg != playback.generation:
            counters['rejected_stale'] += 1
            counters['rejections'].append((time.monotonic()-t0, clause.clause_index, cg, playback.generation))
        else:
            counters['accepted'] += 1
            counters['arrivals'].append((time.monotonic()-t0, clause.clause_index, cg, len(clause.audio_data), clause.is_final))
        return await orig(clause)
    playback.enqueue = spy
    counters['t0'] = t0
    return playback, counters


async def test_1_bargein_before_first_audio():
    reset_env('streaming')
    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler
    engine = build_conversation_engine()
    playback, cnt = make_scheduler_with_spy(PlaybackScheduler)

    gen_before = playback.generation
    task = asyncio.create_task(engine.speak_scripted_text(text=TEXT_2S, playback=playback))
    await asyncio.sleep(0.10)  # 100ms — well before first Veena audio (TTFA ~5.7s)
    t_flush = time.monotonic() - cnt['t0']
    flushed = await playback.flush()
    gen_after = playback.generation
    try:
        clauses = await asyncio.wait_for(task, timeout=60)
    except (asyncio.TimeoutError, Exception) as e:
        clauses = []
        print(f'  pipeline task ended: {type(e).__name__}: {e}')
    # After pipeline ends, check that no stale clause slipped through beyond flush timestamp
    stale_after_flush = [a for a in cnt['arrivals'] if a[0] > t_flush and a[2] != gen_after]
    return {
        'test': 'T1_before_first_audio',
        'gen_before': gen_before,
        'gen_after_flush': gen_after,
        'flush_ts_ms': round(t_flush*1000),
        'flushed_queue_len': len(flushed),
        'accepted': cnt['accepted'],
        'rejected_stale': cnt['rejected_stale'],
        'n_returned': len(clauses),
        'stale_arrivals_after_flush': len(stale_after_flush),
        'barge_in_event_set': playback.barge_in_event.is_set(),
    }


async def test_2_bargein_during_tts_generation():
    reset_env('streaming')
    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler
    engine = build_conversation_engine()
    playback, cnt = make_scheduler_with_spy(PlaybackScheduler)

    task = asyncio.create_task(engine.speak_scripted_text(text=TEXT_2S, playback=playback))
    # Wait until first audio arrives, then wait some more so Veena is mid-stream
    while cnt['accepted'] == 0:
        await asyncio.sleep(0.05)
        if time.monotonic() - cnt['t0'] > 30: break
    accepted_at_trigger = cnt['accepted']
    t_first = cnt['arrivals'][0][0] if cnt['arrivals'] else 0
    # Let 20 more clauses through, then flush
    while cnt['accepted'] < accepted_at_trigger + 20:
        await asyncio.sleep(0.02)
        if time.monotonic() - cnt['t0'] > 60: break
    t_flush = time.monotonic() - cnt['t0']
    gen_before = playback.generation
    await playback.flush()
    gen_after = playback.generation
    try:
        clauses = await asyncio.wait_for(task, timeout=60)
    except Exception as e:
        clauses = []
        print(f'  pipeline task ended: {type(e).__name__}: {e}')
    # Count arrivals after flush timestamp
    arrivals_after_flush = [a for a in cnt['arrivals'] if a[0] > t_flush]
    stale_accepted_after_flush = [a for a in arrivals_after_flush if a[2] != gen_after]
    return {
        'test': 'T2_during_tts_generation',
        'first_audio_ms': round(t_first*1000),
        'accepted_before_barge': accepted_at_trigger,
        'flush_ts_ms': round(t_flush*1000),
        'gen_before': gen_before,
        'gen_after': gen_after,
        'accepted_total': cnt['accepted'],
        'rejected_stale_total': cnt['rejected_stale'],
        'arrivals_after_flush': len(arrivals_after_flush),
        'stale_accepted_after_flush': len(stale_accepted_after_flush),
        'n_returned': len(clauses),
    }


async def test_3_bargein_during_buffering():
    reset_env('buffered_streaming', 400)
    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler
    from src.services.tts.startup_buffer_gate import StartupBufferGate
    engine = build_conversation_engine()
    playback, cnt = make_scheduler_with_spy(PlaybackScheduler)

    # Instrument gate.discard/enqueue via class-level patch — set BEFORE run
    gate_events = []
    orig_enq = StartupBufferGate.enqueue
    orig_discard = StartupBufferGate.discard
    async def wrap_enq(self, clause):
        r = await orig_enq(self, clause)
        gate_events.append((time.monotonic()-cnt['t0'], 'enq', self.buffered_ms, self.depth))
        return r
    def wrap_disc(self):
        gate_events.append((time.monotonic()-cnt['t0'], 'discard_call', self.buffered_ms, self.depth))
        return orig_discard(self)
    StartupBufferGate.enqueue = wrap_enq
    StartupBufferGate.discard = wrap_disc

    try:
        task = asyncio.create_task(engine.speak_scripted_text(text=TEXT_2S, playback=playback))
        # Wait for gate to accumulate 2-3 clauses (well before 400ms threshold — Veena super-frames ~85ms)
        # Actually 400ms triggers after ~5 chunks — we want to hit BEFORE release
        while len(gate_events) < 3:
            await asyncio.sleep(0.02)
            if time.monotonic() - cnt['t0'] > 30: break
        t_flush = time.monotonic() - cnt['t0']
        gen_before = playback.generation
        # capture buffered_ms right before flush
        buffered_at_flush = gate_events[-1][2] if gate_events else 0
        buffered_depth_at_flush = gate_events[-1][3] if gate_events else 0
        await playback.flush()
        gen_after = playback.generation
        try:
            clauses = await asyncio.wait_for(task, timeout=60)
        except Exception as e:
            clauses = []
            print(f'  pipeline task ended: {type(e).__name__}: {e}')
        return {
            'test': 'T3_during_buffering',
            'gate_events_before_flush': sum(1 for e in gate_events if e[0] < t_flush and e[1]=='enq'),
            'buffered_ms_at_flush': round(buffered_at_flush),
            'buffered_depth_at_flush': buffered_depth_at_flush,
            'gen_before': gen_before,
            'gen_after': gen_after,
            'discard_calls': sum(1 for e in gate_events if e[1]=='discard_call'),
            'pb_accepted': cnt['accepted'],
            'pb_rejected_stale': cnt['rejected_stale'],
            'pb_arrivals_after_flush': sum(1 for a in cnt['arrivals'] if a[0] > t_flush),
        }
    finally:
        StartupBufferGate.enqueue = orig_enq
        StartupBufferGate.discard = orig_discard


async def test_4_bargein_during_playback_frames():
    # Simulate Twilio 20ms frame send loop over already-arrived audio
    reset_env('streaming')
    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler
    from src.services.playback.output import AudioOutput
    engine = build_conversation_engine()
    playback, cnt = make_scheduler_with_spy(PlaybackScheduler)

    # First run synth to completion so we have clauses to 'send'
    clauses = await engine.speak_scripted_text(text=TEXT_2S, playback=playback)
    if not clauses:
        return {'test': 'T4_during_playback_frames', 'error': 'no clauses'}
    out = AudioOutput(source_sample_rate=24000, target_sample_rate=8000)
    frames_sent = 0
    frames_after_barge = 0
    barge_triggered_at_frame = None
    gen_at_barge = None
    gen_after_barge = None
    t_send_start = time.monotonic()
    # Send frames at 20ms cadence; barge-in after ~10 frames (200ms of playback)
    for i, c in enumerate(clauses):
        # Per-clause generation check (mirrors twilio_ws_entrypoint _send_clause pre-loop)
        if getattr(c, 'generation', 0) != playback.generation:
            continue
        mu = out.convert(c, 'ulaw')
        for k in range(0, len(mu), 160):
            frame = mu[k:k+160]
            if len(frame) < 160: frame = frame + b'\xff'*(160-len(frame))
            # Per-frame generation check
            if getattr(c, 'generation', 0) != playback.generation:
                frames_after_barge += 1
                continue
            frames_sent += 1
            if frames_sent == 10 and barge_triggered_at_frame is None:
                barge_triggered_at_frame = frames_sent
                gen_at_barge = playback.generation
                await playback.flush()
                gen_after_barge = playback.generation
            await asyncio.sleep(0.020)
        # After each clause, if we already broke, exit
        if playback.generation != getattr(c, 'generation', 0) and barge_triggered_at_frame:
            break
    return {
        'test': 'T4_during_playback_frames',
        'total_clauses': len(clauses),
        'frames_sent_before_barge': barge_triggered_at_frame,
        'gen_at_barge': gen_at_barge,
        'gen_after_barge': gen_after_barge,
        'frames_dropped_after_barge_stale': frames_after_barge,
    }


async def test_5_bargein_during_second_sentence():
    reset_env('streaming')
    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler
    engine = build_conversation_engine()
    playback, cnt = make_scheduler_with_spy(PlaybackScheduler)
    # Distinct-text tracker (each sentence has its own text field)
    task = asyncio.create_task(engine.speak_scripted_text(text=TEXT_3S, playback=playback))
    # Wait for at least 2 distinct clause texts (means sentence 2 has arrived)
    def n_texts():
        return len({a[1] for a in cnt['arrivals']})
    # But arrivals track clause_index which increments across sentences; better to track distinct starts
    # We wait a fixed time: after first audio, wait 10s (sentence 1 finishes then sentence 2 begins)
    while cnt['accepted'] == 0:
        await asyncio.sleep(0.05)
        if time.monotonic() - cnt['t0'] > 30: break
    # Let sentence 1 fully arrive, then start of sentence 2
    await asyncio.sleep(12.0)  # sentence 1 ~10-15s on T4
    t_flush = time.monotonic() - cnt['t0']
    accepted_before = cnt['accepted']
    gen_before = playback.generation
    await playback.flush()
    gen_after = playback.generation
    try:
        clauses = await asyncio.wait_for(task, timeout=60)
    except Exception as e:
        clauses = []
    stale_after = sum(1 for a in cnt['arrivals'] if a[0] > t_flush and a[2] != gen_after)
    return {
        'test': 'T5_during_sentence_2',
        'accepted_before_barge': accepted_before,
        'flush_ts_ms': round(t_flush*1000),
        'gen_before': gen_before,
        'gen_after': gen_after,
        'accepted_total': cnt['accepted'],
        'rejected_stale': cnt['rejected_stale'],
        'stale_accepted_after_flush': stale_after,
    }


async def test_6_post_clear_bargein_race():
    '''Synthetic race: stamp a stale AudioClause and try to enqueue AFTER clear_barge_in().'''
    reset_env('streaming')
    from src.services.playback.scheduler import PlaybackScheduler
    from src.libs.contracts.streaming import AudioClause
    pb = PlaybackScheduler()
    gen_0 = pb.generation
    # Enqueue a valid clause first
    c_valid = AudioClause(audio_data=b'\x00'*1600, sample_rate=24000, text='ok', clause_index=0, is_final=False, generation=gen_0)
    await pb.enqueue(c_valid)
    depth_after_valid = pb.depth
    # Flush → gen becomes 1
    flushed = await pb.flush()
    gen_1 = pb.generation
    ev_set_after_flush = pb.barge_in_event.is_set()
    # Simulate next turn: clear_barge_in
    pb.clear_barge_in()
    ev_set_after_clear = pb.barge_in_event.is_set()
    # Now attempt to enqueue a STALE clause stamped with old gen
    c_stale = AudioClause(audio_data=b'\x00'*1600, sample_rate=24000, text='stale', clause_index=1, is_final=True, generation=gen_0)
    await pb.enqueue(c_stale)
    depth_after_stale = pb.depth
    # A new valid clause of current gen should still succeed
    c_new = AudioClause(audio_data=b'\x00'*1600, sample_rate=24000, text='new', clause_index=0, is_final=True, generation=gen_1)
    await pb.enqueue(c_new)
    depth_after_new = pb.depth
    return {
        'test': 'T6_post_clear_race',
        'gen_0': gen_0, 'gen_1': gen_1,
        'ev_set_after_flush': ev_set_after_flush,
        'ev_set_after_clear': ev_set_after_clear,
        'depth_after_valid_enqueue': depth_after_valid,
        'depth_after_flush': 0,
        'depth_after_stale_enqueue': depth_after_stale,
        'depth_after_new_enqueue': depth_after_new,
        'flushed_clauses': len(flushed),
    }


async def test_7_rapid_successive():
    '''Rapid flushes with in-flight stale enqueues.'''
    reset_env('streaming')
    from src.services.playback.scheduler import PlaybackScheduler
    from src.libs.contracts.streaming import AudioClause
    pb = PlaybackScheduler()
    gens = [pb.generation]
    # Enqueue and flush 3 times rapidly
    for i in range(3):
        c = AudioClause(audio_data=b'\x00'*800, sample_rate=24000, text=f'g{i}', clause_index=0, is_final=False, generation=pb.generation)
        await pb.enqueue(c)
        await pb.flush()
        gens.append(pb.generation)
    pb.clear_barge_in()
    # Now try to enqueue clauses stamped with each stale gen
    accepted_gens = []
    rejected_gens = []
    for g in gens[:-1]:
        c = AudioClause(audio_data=b'\x00'*800, sample_rate=24000, text=f'stale_g{g}', clause_index=0, is_final=False, generation=g)
        depth_before = pb.depth
        await pb.enqueue(c)
        depth_after = pb.depth
        if depth_after > depth_before:
            accepted_gens.append(g)
        else:
            rejected_gens.append(g)
    # And enqueue a current-gen clause
    c_now = AudioClause(audio_data=b'\x00'*800, sample_rate=24000, text='now', clause_index=0, is_final=True, generation=pb.generation)
    depth_before = pb.depth
    await pb.enqueue(c_now)
    depth_after = pb.depth
    return {
        'test': 'T7_rapid_successive',
        'generations_reached': gens,
        'final_generation': pb.generation,
        'monotonic': all(gens[i]+1 == gens[i+1] for i in range(len(gens)-1)),
        'stale_accepted': accepted_gens,
        'stale_rejected': rejected_gens,
        'current_enqueue_succeeded': depth_after > depth_before,
    }


async def main():
    print('=== STARTING STATE ===')
    print('CPU: root@101.53.141.141')
    import subprocess
    git = subprocess.check_output(['git','-C','/opt/voiceos/app','log','-1','--oneline'], text=True).strip()
    dirty = subprocess.check_output(['git','-C','/opt/voiceos/app','status','--porcelain'], text=True).strip().count('\n') + 1
    print(f'git HEAD: {git}')
    print(f'working tree dirty lines: {dirty}')

    results = []
    for fn in [test_1_bargein_before_first_audio,
               test_2_bargein_during_tts_generation,
               test_3_bargein_during_buffering,
               test_4_bargein_during_playback_frames,
               test_5_bargein_during_second_sentence,
               test_6_post_clear_bargein_race,
               test_7_rapid_successive]:
        print(f'\n--- {fn.__name__} ---')
        try:
            r = await fn()
        except Exception as e:
            r = {'test': fn.__name__, 'error': f'{type(e).__name__}: {e}'}
        results.append(r)
        for k,v in r.items():
            print(f'  {k}: {v}')

    print('\n=== WARNING LOG (rejection messages) ===')
    for line in LOG_BUF.getvalue().splitlines():
        if 'PlaybackScheduler' in line or 'StartupBufferGate' in line or 'TrueStreamingPipeline' in line:
            print('  ' + line)

    import json
    print('\n=== JSON SUMMARY ===')
    print(json.dumps(results, indent=2, default=str))

if __name__=='__main__':
    asyncio.run(main())
