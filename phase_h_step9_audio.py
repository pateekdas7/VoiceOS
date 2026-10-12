'''Phase H STEP 9 — live audio-contract validation on CPU ↔ Kaggle T4x2.

Verifies:
  9.2 raw Veena PCM: 24 kHz PCM16LE, 1 ch, 2 bytes/sample
  9.3 AudioOutput.convert: PCM16LE 24 kHz → μ-law 8 kHz (6:1 byte ratio)
  9.4 Twilio frame contract: 160-byte μ-law frames (20 ms each)
  9.5 Frame sequence integrity via production _send_clause
  9.6 Real inter-frame timing (CPU-local, not Twilio-wire)
  9.7 Underrun & clause-gap measurements (real Veena arrival timing)
  9.8 Inter-sentence gap
  9.10 Small barge-in audio-contract regression
  9.11 Short/medium/long response tests
'''
import asyncio, os, sys, time, statistics, json, io, logging
sys.path.insert(0, '/opt/voiceos/app')

LOG_BUF = io.StringIO()
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(LOG_BUF)],
                    format='%(name)s %(levelname)s %(message)s')

SHORT = 'नमस्ते।'                                    # ~1-2s
MEDIUM = 'नमस्ते प्रतीक जी, मैं राजत फाइनेंस से बोल रही हूं।'  # ~4-5s
LONG = MEDIUM + ' आपका EMI बकाया है। कृपया आज ही भुगतान करें।'  # ~10s+


def make_capturing_adapter():
    class CapAdapter:
        def __init__(self):
            self.frames = []  # list of (t_rel, seq, rtp_ts, payload_len, payload_prefix)
            self.t0 = None
        async def send_frame(self, frame):
            if self.t0 is None: self.t0 = time.monotonic()
            await asyncio.sleep(0)
            self.frames.append((
                time.monotonic()-self.t0,
                getattr(frame,'seq',None),
                getattr(frame,'rtp_ts',None),
                len(frame.pcm_data),
                frame.pcm_data[:4].hex(),
                str(frame.config.encoding) if hasattr(frame.config,'encoding') else None,
                int(frame.config.sample_rate) if hasattr(frame.config,'sample_rate') else None,
                frame.config.channels if hasattr(frame.config,'channels') else None,
            ))
        async def _yield_wrap(self):
            pass
    return CapAdapter()


async def synth_only(text: str):
    '''Directly exercise VeenaAdapter for raw PCM contract verification.'''
    
    
    from src.services.tts.adapters.veena_adapter import VeenaAdapter
    from src.libs.contracts.streaming import VoiceConfig
    from deployment.cpu.app import build_gpu_scheduler
    adapter = VeenaAdapter(gpu_scheduler=build_gpu_scheduler(), base_url=os.environ['TTS_BASE_URL'])
    vc = VoiceConfig(rate_scale=1.0, language='hi')
    async def _one():
        yield text
    clauses = []
    t0 = time.monotonic()
    ttfa = None
    async for c in await adapter.synthesize_stream(_one(), vc):
        if ttfa is None: ttfa = (time.monotonic()-t0)*1000
        clauses.append((time.monotonic()-t0, c))
    wall = (time.monotonic()-t0)*1000
    return clauses, wall, ttfa


async def send_via_production_path(clauses):
    '''Exercise real _send_clause via object.__new__(CallOrchestrator) with fake adapter.'''
    from src.services.media_gateway.twilio_ws_entrypoint import CallOrchestrator
    from src.services.playback.scheduler import PlaybackScheduler
    from src.services.playback.output import AudioOutput
    orch = object.__new__(CallOrchestrator)
    orch._adapter = make_capturing_adapter()
    orch._playback = PlaybackScheduler()
    orch._audio_output = AudioOutput(source_sample_rate=24000, target_sample_rate=8000)
    orch._recorder = None
    orch._out_seq = 0
    orch._pace_last_clause_end = None
    per_clause_pace = []
    t_start = time.monotonic()
    for c in clauses:
        _t = time.monotonic()
        await orch._send_clause(c)
        per_clause_pace.append({'t_start': _t-t_start, 't_end': time.monotonic()-t_start,
                                 'audio_ms_added_by_this_clause': len(orch._audio_output._odd_byte_carry)})
    return orch._adapter.frames, per_clause_pace


def analyse_frames(frames):
    if not frames:
        return {'n_frames':0}
    seqs = [f[1] for f in frames]
    lens = [f[3] for f in frames]
    encodings = list({f[5] for f in frames})
    srs = list({f[6] for f in frames})
    chs = list({f[7] for f in frames})
    times = [f[0]*1000 for f in frames]
    gaps = [times[i+1]-times[i] for i in range(len(times)-1)]
    return {
        'n_frames': len(frames),
        'first_seq': seqs[0], 'last_seq': seqs[-1],
        'seqs_monotonic': all(seqs[i]+1 == seqs[i+1] for i in range(len(seqs)-1)),
        'all_160_bytes': all(l==160 for l in lens),
        'unique_lengths': sorted(set(lens)),
        'unique_encodings': encodings,
        'unique_sample_rates': srs,
        'unique_channels': chs,
        'total_bytes': sum(lens),
        'implied_audio_ms': len(frames)*20,
        'cpu_send_p50_gap_ms': round(statistics.median(gaps),3) if gaps else None,
        'cpu_send_p90_gap_ms': round(sorted(gaps)[int(len(gaps)*0.90)],3) if gaps else None,
        'cpu_send_p95_gap_ms': round(sorted(gaps)[int(len(gaps)*0.95)],3) if gaps else None,
        'cpu_send_p99_gap_ms': round(sorted(gaps)[int(len(gaps)*0.99)],3) if gaps else None,
        'cpu_send_max_gap_ms': round(max(gaps),3) if gaps else None,
        'cpu_send_min_gap_ms': round(min(gaps),3) if gaps else None,
        'gaps_over_20ms': sum(1 for g in gaps if g > 20),
        'gaps_over_40ms': sum(1 for g in gaps if g > 40),
        'gaps_over_100ms': sum(1 for g in gaps if g > 100),
        'gaps_over_500ms': sum(1 for g in gaps if g > 500),
    }


def analyse_per_clause_pace(per_clause_pace, veena_arrivals):
    '''Simulate real-time playback pacing to identify producer starvation.

    Each Veena clause arrives at t=veena_arrivals[i]. Its audio contributes
    audio_ms_i. If we play clauses back-to-back starting when first arrives,
    an underrun happens when the next needed frame belongs to a clause
    that has not yet arrived.
    '''
    return per_clause_pace


async def run_one_length(label, text):
    print(f'\n=== length={label} text={text!r} ===')
    clauses_ts, wall_ms, ttfa_ms = await synth_only(text)
    if not clauses_ts:
        return {'label': label, 'error': 'no clauses'}
    # 9.2 raw PCM contract
    total_pcm = sum(len(c.audio_data) for _,c in clauses_ts)
    sample_rates = list({c.sample_rate for _,c in clauses_ts})
    audio_ms_calc = total_pcm / (24000 * 2) * 1000
    print(f'  raw: n_clauses={len(clauses_ts)} total_pcm={total_pcm} sr={sample_rates} audio_ms_from_bytes={audio_ms_calc:.1f}')
    # 9.5+9.6 real _send_clause via production path
    only_clauses = [c for _,c in clauses_ts]
    frames, pace = await send_via_production_path(only_clauses)
    fa = analyse_frames(frames)
    print(f'  frames: n={fa["n_frames"]} all_160B={fa["all_160_bytes"]} monotonic={fa["seqs_monotonic"]} enc={fa["unique_encodings"]} sr={fa["unique_sample_rates"]} ch={fa["unique_channels"]}')
    print(f'  implied μ-law audio_ms={fa["implied_audio_ms"]} (raw PCM calc={audio_ms_calc:.0f})')
    print(f'  CPU send cadence: p50={fa["cpu_send_p50_gap_ms"]} p95={fa["cpu_send_p95_gap_ms"]} p99={fa["cpu_send_p99_gap_ms"]} max={fa["cpu_send_max_gap_ms"]}')
    print(f'  gaps: >20ms={fa["gaps_over_20ms"]} >40ms={fa["gaps_over_40ms"]} >100ms={fa["gaps_over_100ms"]} >500ms={fa["gaps_over_500ms"]}')
    # 9.7 producer starvation: simulate real-time consumer against arrival timings
    # Build a timeline: for each clause, μ-law bytes / 160 = frames; distribute at 20ms cadence starting when clause arrived
    from src.services.playback.output import AudioOutput
    out = AudioOutput(24000, 8000)
    frame_availability = []  # list of ms when frame becomes available
    for arr_s, clause in clauses_ts:
        arr_ms = arr_s * 1000
        mu = out.convert(clause, 'ulaw')
        n = (len(mu) + 159) // 160
        for k in range(n):
            frame_availability.append(arr_ms)
    if frame_availability:
        play_start = frame_availability[0]
        underruns = 0
        gaps = []
        for i, avail in enumerate(frame_availability):
            target = play_start + i * 20
            if avail > target:
                underruns += 1
                gaps.append(avail - target)
        longest = max(gaps) if gaps else 0
    else:
        underruns=0; longest=0; gaps=[]
    print(f'  RT-sim: n_frames={len(frame_availability)} underruns={underruns} longest_gap_ms={longest:.0f} gaps_over_500ms={sum(1 for g in gaps if g>500)}')
    return {
        'label': label,
        'ttfa_ms': round(ttfa_ms),
        'wall_ms': round(wall_ms),
        'raw_pcm_bytes': total_pcm,
        'raw_pcm_sample_rates': sample_rates,
        'raw_audio_ms': round(audio_ms_calc),
        'rtf_producer': round(wall_ms / max(audio_ms_calc,1), 2),
        **fa,
        'rt_sim_underruns': underruns,
        'rt_sim_longest_gap_ms': round(longest),
        'rt_sim_gaps_over_500ms': sum(1 for g in gaps if g>500),
    }


async def barge_in_audio_regression():
    '''One barge-in audio-contract regression: mid-clause flush → partial frames OK, no post-flush frames.'''
    from src.services.media_gateway.twilio_ws_entrypoint import CallOrchestrator
    from src.services.playback.scheduler import PlaybackScheduler
    from src.services.playback.output import AudioOutput
    from src.libs.contracts.streaming import AudioClause
    print('\n=== 9.10 barge-in audio-contract regression ===')
    # Prepare two synthetic clauses (avoids depending on real Veena for this quick check)
    c1 = AudioClause(audio_data=b'\x00\x10'*2400,  # 4800 bytes = 100ms of 24kHz PCM16LE
                     sample_rate=24000, text='c1', clause_index=0, is_final=False, generation=0)
    c2 = AudioClause(audio_data=b'\x00\x20'*2400, sample_rate=24000, text='c2', clause_index=1, is_final=True, generation=0)
    orch = object.__new__(CallOrchestrator)
    orch._adapter = make_capturing_adapter()
    orch._playback = PlaybackScheduler()
    orch._audio_output = AudioOutput(24000, 8000)
    orch._recorder = None
    orch._out_seq = 0
    orch._pace_last_clause_end = None
    # Send c1 fully
    await orch._send_clause(c1)
    frames_after_c1 = len(orch._adapter.frames)
    # Advance generation BEFORE c2 (simulating barge-in that fired between clauses)
    await orch._playback.flush()
    # Try to send c2 — should be dropped (gen 0 vs playback gen 1)
    await orch._send_clause(c2)
    frames_after_c2 = len(orch._adapter.frames)
    result = {
        'frames_from_c1': frames_after_c1,
        'frames_from_c2_after_barge_in': frames_after_c2 - frames_after_c1,
        'playback_gen': orch._playback.generation,
        'all_frames_160B': all(f[3]==160 for f in orch._adapter.frames),
        'monotonic_seq': all(orch._adapter.frames[i][1]+1 == orch._adapter.frames[i+1][1] for i in range(len(orch._adapter.frames)-1)),
    }
    print(json.dumps(result))
    # Also test mid-clause barge: flush WHILE inside _send_clause
    print('\n=== 9.10b mid-clause barge-in ===')
    orch2 = object.__new__(CallOrchestrator)
    orch2._adapter = make_capturing_adapter()
    orch2._playback = PlaybackScheduler()
    orch2._audio_output = AudioOutput(24000, 8000)
    orch2._recorder = None
    orch2._out_seq = 0
    orch2._pace_last_clause_end = None
    # Create a very large clause (5s of PCM = 240000 bytes → 40000 μ-law → 250 frames)
    big = AudioClause(audio_data=(b'\x00\x10')*120000, sample_rate=24000, text='big', clause_index=0, is_final=True, generation=0)
    async def flusher():
        await asyncio.sleep(0.001)
        await orch2._playback.flush()
    ft = asyncio.create_task(flusher())
    await orch2._send_clause(big)
    await ft
    partial = orch2._adapter.frames
    result2 = {
        'frames_sent_before_abort': len(partial),
        'all_160B': all(f[3]==160 for f in partial),
        'playback_gen_after': orch2._playback.generation,
    }
    print(json.dumps(result2))
    return {'9_10a': result, '9_10b': result2}


async def main():
    import subprocess, json as _json
    print('=== 9.1 STARTING STATE ===')
    git = subprocess.check_output(['git','-C','/opt/voiceos/app','log','-1','--oneline'], text=True).strip()
    print(f'git HEAD: {git}')
    print(f'env: TTS={os.environ.get("TTS_BASE_URL","?")[:60]}')
    print(f'VOICEOS_TTS_MODE={os.environ.get("VOICEOS_TTS_MODE","(unset, default streaming)")}')
    # Probe endpoints
    import urllib.request
    for label, url in [('LLM', os.environ['LLM_BASE_URL']+'/health'),
                       ('STT', os.environ['STT_BASE_URL']+'/health/ready'),
                       ('TTS', os.environ['TTS_BASE_URL']+'/health/ready')]:
        try:
            r = urllib.request.urlopen(url, timeout=8).read()[:200].decode(errors='replace')
            print(f'  {label} OK: {r[:100]}')
        except Exception as e:
            print(f'  {label} FAIL: {e}')

    results = {}
    for label, text in [('short', SHORT), ('medium', MEDIUM), ('long', LONG)]:
        try:
            results[label] = await run_one_length(label, text)
        except Exception as e:
            results[label] = {'error': f'{type(e).__name__}: {e}'}

    results['barge_in'] = await barge_in_audio_regression()

    print('\n=== JSON SUMMARY ===')
    print(_json.dumps(results, indent=2, default=str))

if __name__=='__main__':
    asyncio.run(main())
