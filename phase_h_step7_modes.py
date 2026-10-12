'''Phase H STEP 7 — three-mode live validation against real Kaggle T4x2.

For each of streaming / buffered_streaming@400 / blocking:
  1. Set env vars fresh.
  2. Build production ConversationEngine.
  3. Wrap PlaybackScheduler.enqueue + StartupBufferGate.enqueue+_release_buffered
     + VeenaAdapter.synthesize_stream to capture:
        - TTS synthesis call count
        - AudioClause arrival timeline (into gate and into playback)
        - Buffer depth history
  4. Run speak_scripted_text with the SAME Hindi text.
  5. Convert every clause via AudioOutput → 8kHz μ-law 160-byte 20ms frames.
  6. Simulate a real-time 20ms consumer starting at first-playback-ready.
     Compute underruns, longest gap, total playback duration.
'''
import asyncio, os, sys, time, statistics, json

sys.path.insert(0, '/opt/voiceos/app')

TEXT = 'नमस्ते प्रतीक जी, मैं राजत फाइनेंस से बोल रही हूं। आपका EMI बकाया है। कृपया आज ही भुगतान करें।'


def reset_env(mode: str | None, buffer_ms: int | None):
    os.environ.pop('VOICEOS_TTS_MODE', None)
    os.environ.pop('VOICEOS_TTS_BUFFER_MS', None)
    if mode is not None:
        os.environ['VOICEOS_TTS_MODE'] = mode
    if buffer_ms is not None:
        os.environ['VOICEOS_TTS_BUFFER_MS'] = str(buffer_ms)


async def run_one(mode_label: str, mode_env: str, buffer_env: int | None):
    reset_env(mode_env, buffer_env)
    # Fresh imports so build_gate_from_env re-reads env is not needed
    # (build_gate_from_env reads at each call), but clear cached engine
    for m in list(sys.modules):
        if m.startswith('src.services.tts.startup_buffer_gate'):
            del sys.modules[m]

    from deployment.cpu.app import build_conversation_engine
    from src.services.playback.scheduler import PlaybackScheduler
    from src.services.tts.startup_buffer_gate import StartupBufferGate, TTSMode, build_gate_from_env
    from src.services.playback.output import AudioOutput

    engine = build_conversation_engine()

    # -- Instrumentation --
    tts_call_count = {'n': 0, 'texts': []}
    orig_synth = engine._tts._adapter.synthesize_stream

    async def _wrap_synth(text_chunks, voice_config):
        tts_call_count['n'] += 1
        # Peek text without consuming (buffer it)
        buffered = []
        async def _replay():
            for c in buffered:
                yield c
        async for c in text_chunks:
            buffered.append(c)
        tts_call_count['texts'].append(''.join(buffered))
        return await orig_synth(_replay(), voice_config)

    engine._tts._adapter.synthesize_stream = _wrap_synth

    playback = PlaybackScheduler()
    arrivals_playback = []   # list of (t_rel, bytes_len, generation, text, is_final)
    gate_events = []         # list of (t_rel, event_kind, buffered_ms, buffer_depth)
    t0 = time.monotonic()

    orig_pb_enq = playback.enqueue
    async def _spy_pb_enq(clause):
        arrivals_playback.append((time.monotonic()-t0, len(clause.audio_data), getattr(clause,'generation',None), clause.text, clause.is_final))
        return await orig_pb_enq(clause)
    playback.enqueue = _spy_pb_enq

    # Monkey-patch StartupBufferGate to log
    orig_enq = StartupBufferGate.enqueue
    orig_release = StartupBufferGate._release_buffered
    orig_flush = StartupBufferGate.flush_final
    orig_discard = StartupBufferGate.discard

    async def _wrap_enq(self, clause):
        r = await orig_enq(self, clause)
        gate_events.append((time.monotonic()-t0, 'enq', self.buffered_ms, self.depth))
        return r
    async def _wrap_release(self):
        gate_events.append((time.monotonic()-t0, 'release_start', self.buffered_ms, self.depth))
        r = await orig_release(self)
        gate_events.append((time.monotonic()-t0, 'release_end', self.buffered_ms, self.depth))
        return r
    async def _wrap_flush(self):
        gate_events.append((time.monotonic()-t0, 'flush_final_start', self.buffered_ms, self.depth))
        r = await orig_flush(self)
        gate_events.append((time.monotonic()-t0, 'flush_final_end', self.buffered_ms, self.depth))
        return r
    def _wrap_discard(self):
        gate_events.append((time.monotonic()-t0, 'discard', self.buffered_ms, self.depth))
        return orig_discard(self)
    StartupBufferGate.enqueue = _wrap_enq
    StartupBufferGate._release_buffered = _wrap_release
    StartupBufferGate.flush_final = _wrap_flush
    StartupBufferGate.discard = _wrap_discard

    # Run
    t=time.monotonic()
    clauses = await engine.speak_scripted_text(text=TEXT, playback=playback)
    wall_ms = (time.monotonic()-t)*1000
    ttfa_ms = arrivals_playback[0][0]*1000 if arrivals_playback else -1

    # Convert to μ-law 20ms frames
    out = AudioOutput(source_sample_rate=24000, target_sample_rate=8000)
    ulaw_frames = []
    # For each PLAYBACK arrival, convert clause and split into 160-byte frames
    for (t_rel, blen, gen, txt, is_final) in arrivals_playback:
        pass
    # Actually convert from clauses list preserving arrival timing
    # Reconstruct arrival ts per clause obj (align by index into arrivals_playback)
    # We use clauses returned by pipeline as source of AudioClause objects (they equal what went to playback)
    # Note: for BLOCKING/BUFFERED, arrivals may come out of the gate — arrivals_playback timestamps are when they reach playback
    frame_arrival_times = []  # ms since t0 when each 20ms frame became available for playback
    frame_bytes = 0
    for i, c in enumerate(clauses):
        ts = arrivals_playback[i][0]*1000 if i < len(arrivals_playback) else wall_ms
        mu = out.convert(c, 'ulaw')
        frame_bytes += len(mu)
        # Split into 160-byte frames
        for k in range(0, len(mu), 160):
            chunk = mu[k:k+160]
            if len(chunk) < 160:
                # pad-silence a partial frame for the sim (analogous to production which sends what it has)
                chunk = chunk + b'\xff' * (160 - len(chunk))
            # Frame is available when the clause arrived
            frame_arrival_times.append(ts + (k // 160) * 20.0 * 0)  # all frames of this clause available at ts
    n_frames = len(frame_arrival_times)
    total_audio_ms = n_frames * 20.0
    # Simulate 20 ms real-time consumer starting at first frame arrival
    play_start = frame_arrival_times[0] if frame_arrival_times else 0.0
    underruns = 0
    gap_ms_list = []
    last_arrival_wall = play_start
    for i, arr in enumerate(frame_arrival_times):
        target_play_time = play_start + i * 20.0
        if arr > target_play_time:
            underruns += 1
            gap = arr - target_play_time
            gap_ms_list.append(gap)
    longest_gap = max(gap_ms_list) if gap_ms_list else 0.0

    # Peak buffer
    peak_buffered_ms = max((ev[2] for ev in gate_events), default=0.0)
    peak_depth = max((ev[3] for ev in gate_events), default=0)
    # Release timestamps
    release_ts = [ev[0]*1000 for ev in gate_events if ev[1]=='release_start']
    flush_ts = [ev[0]*1000 for ev in gate_events if ev[1]=='flush_final_start']
    discard_ts = [ev[0]*1000 for ev in gate_events if ev[1]=='discard']

    # Restore
    StartupBufferGate.enqueue = orig_enq
    StartupBufferGate._release_buffered = orig_release
    StartupBufferGate.flush_final = orig_flush
    StartupBufferGate.discard = orig_discard

    result = {
        'mode': mode_label,
        'wall_ms': round(wall_ms),
        'ttfa_playback_ms': round(ttfa_ms),
        'first_gate_enq_ms': round(gate_events[0][0]*1000) if gate_events else None,
        'first_release_ms': round(release_ts[0]) if release_ts else None,
        'flush_final_ms': round(flush_ts[0]) if flush_ts else None,
        'discard_events': len(discard_ts),
        'n_clauses': len(clauses),
        'n_arrivals_playback': len(arrivals_playback),
        'n_tts_calls': tts_call_count['n'],
        'tts_call_texts': tts_call_count['texts'],
        'total_audio_ms': round(total_audio_ms),
        'n_ulaw_frames_160b': n_frames,
        'peak_buffered_ms': round(peak_buffered_ms),
        'peak_buffer_depth': peak_depth,
        'underruns': underruns,
        'longest_gap_ms': round(longest_gap),
        'generation_values': sorted({a[2] for a in arrivals_playback}),
        'final_generation': playback.generation,
        'is_final_indices': [i for i,a in enumerate(arrivals_playback) if a[4]],
        'sentences_seen_by_tts': [t[:40] for t in tts_call_count['texts']],
        'rtf': round(wall_ms / max(total_audio_ms, 1), 2),
    }
    return result


async def main():
    results = []
    for label, mode_env, buffer_env in [
        ('streaming', 'streaming', None),
        ('buffered_400', 'buffered_streaming', 400),
        ('blocking', 'blocking', None),
    ]:
        print(f'\n======= running mode: {label} =======')
        r = await run_one(label, mode_env, buffer_env)
        results.append(r)
        for k,v in r.items():
            print(f'  {k}: {v}')
    print('\n=== SUMMARY ===')
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__=='__main__':
    asyncio.run(main())
