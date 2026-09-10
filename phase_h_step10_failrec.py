'''Phase H STEP 10 — Failure / Recovery validation on live CPU ↔ Kaggle T4x2.

Test-harness ONLY. No production code is modified.
All failures are injected via monkey-patch at adapter/httpx boundaries.
Kaggle services are NEVER killed.
'''
import asyncio, os, sys, time, json, io, logging, traceback, contextlib
sys.path.insert(0, '/opt/voiceos/app')

LOG_BUF = io.StringIO()
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(LOG_BUF)],
                    format='%(name)s %(levelname)s %(message)s', force=True)

import httpx

from src.services.tts.adapters.veena_adapter import VeenaAdapter
from src.services.llm_runtime.adapters.vllm_adapter import vLLMAdapter
from src.services.llm_runtime.prompt_contract import PromptContract
from src.services.stt.adapters.whisper_http_adapter import WhisperHTTPAdapter
from src.libs.contracts.streaming import VoiceConfig
from src.services.playback.scheduler import PlaybackScheduler
from src.services.playback.output import AudioOutput
from src.services.tts.startup_buffer_gate import StartupBufferGate, TTSMode
from src.services.conversation_engine.engine import _default_response_plan
from deployment.cpu.app import build_gpu_scheduler


def _make_llm():
    return vLLMAdapter(gpu_scheduler=build_gpu_scheduler(),
                       prompt_contract=PromptContract(),
                       base_url=os.environ['LLM_BASE_URL'])


SHORT_TEXT = 'नमस्ते।'


def _capture_logs():
    LOG_BUF.seek(0); LOG_BUF.truncate()

def _get_logs():
    return LOG_BUF.getvalue()


def _snap_scheduler(sched):
    return {
        'generation': sched.generation,
        'barge_in_event_set': sched.barge_in_event.is_set(),
        'queue_depth': sched.queue_depth() if hasattr(sched,'queue_depth') else None,
    }


async def _run_healthy_turn(label):
    '''Run a healthy short synthesis turn against live Veena, return summary.'''
    gpu = build_gpu_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=gpu, base_url=os.environ['TTS_BASE_URL'])
    sched = PlaybackScheduler()
    vc = VoiceConfig(rate_scale=1.0, language='hi')
    async def _one():
        yield SHORT_TEXT
    t0 = time.monotonic()
    clauses = []
    try:
        async for c in await adapter.synthesize_stream(_one(), vc):
            clauses.append(c)
            await sched.enqueue(c)
        return {
            'label': label,
            'ok': True,
            'n_clauses': len(clauses),
            'wall_ms': int((time.monotonic()-t0)*1000),
            'sched_after': _snap_scheduler(sched),
        }
    except Exception as e:
        return {'label': label, 'ok': False, 'error': repr(e), 'sched_after': _snap_scheduler(sched)}


# ============================================================
# F1 — LLM failure
# ============================================================
async def test_f1_llm_failure():
    print('\n--- F1: LLM unavailable ---')
    _capture_logs()
    result = {'test': 'F1_LLM_unavailable'}
    llm = _make_llm()
    plan = _default_response_plan()
    # Inject: _open_stream raises ConnectError
    async def _fail_open(client, url, payload):
        raise httpx.ConnectError('injected: LLM unreachable')
    orig = vLLMAdapter._open_stream
    vLLMAdapter._open_stream = staticmethod(_fail_open)
    t0 = time.monotonic()
    err = None
    delta_count = 0
    try:
        async for d in await llm.generate_stream(prompt='नमस्ते', response_plan=plan, max_tokens=16):
            delta_count += 1
    except Exception as e:
        err = e
    detect_ms = int((time.monotonic()-t0)*1000)
    result['detect_ms'] = detect_ms
    result['delta_count'] = delta_count
    result['exception_type'] = type(err).__name__ if err else None
    result['exception_msg'] = str(err)[:200] if err else None
    # Restore
    vLLMAdapter._open_stream = staticmethod(orig)
    # Recovery: real LLM call
    recovery = await _run_llm_recovery()
    result['recovery'] = recovery
    result['log_snippet'] = [l for l in _get_logs().splitlines() if 'llm' in l.lower() or 'vllm' in l.lower()][:5]
    print(f'  detect_ms={detect_ms} exception={result["exception_type"]} recovered={recovery.get("ok")}')
    return result


async def _run_llm_recovery():
    llm = _make_llm()
    plan = _default_response_plan()
    t0 = time.monotonic()
    deltas = 0
    err = None
    try:
        async for d in await llm.generate_stream(prompt='say hi', response_plan=plan, max_tokens=8):
            deltas += 1
            if deltas >= 3: break
    except Exception as e:
        err = e
    return {'ok': err is None, 'deltas': deltas, 'wall_ms': int((time.monotonic()-t0)*1000),
            'error': repr(err) if err else None}


# ============================================================
# F2 — STT failure
# ============================================================
async def test_f2_stt_failure():
    print('\n--- F2: STT unavailable ---')
    _capture_logs()
    result = {'test': 'F2_STT_unavailable'}
    stt = WhisperHTTPAdapter(gpu_scheduler=build_gpu_scheduler(), base_url=os.environ['STT_BASE_URL'])
    orig = WhisperHTTPAdapter._call_transcribe
    async def _fail_call(self, pcm_bytes, language):
        raise httpx.ConnectError('injected: STT unreachable')
    WhisperHTTPAdapter._call_transcribe = _fail_call
    t0 = time.monotonic()
    err = None
    try:
        _ = await stt._call_transcribe(b'\x00'*16000, 'hi')
    except Exception as e:
        err = e
    detect_ms = int((time.monotonic()-t0)*1000)
    result['detect_ms'] = detect_ms
    result['exception_type'] = type(err).__name__ if err else None
    result['exception_msg'] = str(err)[:200] if err else None
    WhisperHTTPAdapter._call_transcribe = orig
    # Recovery: real STT
    recovery = await _run_stt_recovery()
    result['recovery'] = recovery
    print(f'  detect_ms={detect_ms} exception={result["exception_type"]} recovered={recovery.get("ok")}')
    return result


async def _run_stt_recovery():
    stt = WhisperHTTPAdapter(gpu_scheduler=build_gpu_scheduler(), base_url=os.environ['STT_BASE_URL'])
    t0 = time.monotonic()
    err = None; segs = None
    try:
        segs = await stt._call_transcribe(b'\x00'*32000, 'hi')
    except Exception as e:
        err = e
    return {'ok': err is None, 'n_segments': (len(segs) if segs is not None else None),
            'wall_ms': int((time.monotonic()-t0)*1000), 'error': repr(err) if err else None}


# ============================================================
# F3 — TTS pre-synthesis failure
# ============================================================
async def test_f3_tts_pre_synth_failure():
    print('\n--- F3: TTS unavailable BEFORE synthesis ---')
    _capture_logs()
    result = {'test': 'F3_TTS_pre_synth_unavailable'}
    gpu = build_gpu_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=gpu, base_url=os.environ['TTS_BASE_URL'])
    sched = PlaybackScheduler()
    orig = VeenaAdapter._open_stream
    async def _fail_open(client, url, payload):
        raise httpx.ConnectError('injected: TTS unreachable')
    VeenaAdapter._open_stream = staticmethod(_fail_open)
    vc = VoiceConfig(rate_scale=1.0, language='hi')
    async def _one():
        yield SHORT_TEXT
    clauses = []
    t0 = time.monotonic()
    err = None
    try:
        async for c in await adapter.synthesize_stream(_one(), vc):
            clauses.append(c)
            await sched.enqueue(c)
    except Exception as e:
        err = e
    detect_ms = int((time.monotonic()-t0)*1000)
    result['detect_ms'] = detect_ms
    result['clauses_received'] = len(clauses)
    result['exception_type'] = type(err).__name__ if err else None
    result['exception_msg'] = str(err)[:200] if err else None
    result['sched_after_failure'] = _snap_scheduler(sched)
    VeenaAdapter._open_stream = staticmethod(orig)
    recovery = await _run_healthy_turn('F3_recovery')
    result['recovery'] = recovery
    print(f'  detect_ms={detect_ms} clauses={len(clauses)} exception={result["exception_type"]} recovered={recovery["ok"]}')
    return result


# ============================================================
# F5+F6 — TTS mid-stream failure (during synthesis)
# ============================================================
async def test_f6_tts_midstream_failure():
    print('\n--- F6: TTS mid-stream failure ---')
    _capture_logs()
    result = {'test': 'F6_TTS_midstream_failure'}
    gpu = build_gpu_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=gpu, base_url=os.environ['TTS_BASE_URL'])
    sched = PlaybackScheduler()

    # Wrap _open_stream so httpx response's aiter_bytes raises after first chunk
    orig_open = VeenaAdapter._open_stream
    async def _wrapped_open(client, url, payload):
        resp = await orig_open(client, url, payload)
        orig_aiter = resp.aiter_bytes
        async def _fault_aiter(chunk_size=None):
            got = 0
            async for chunk in orig_aiter(chunk_size or 4096):
                got += 1
                yield chunk
                if got >= 1:
                    raise httpx.RemoteProtocolError('injected: connection closed mid-stream')
        resp.aiter_bytes = _fault_aiter
        return resp
    VeenaAdapter._open_stream = staticmethod(_wrapped_open)

    vc = VoiceConfig(rate_scale=1.0, language='hi')
    async def _one():
        yield SHORT_TEXT
    clauses = []
    t0 = time.monotonic()
    err = None
    try:
        async for c in await adapter.synthesize_stream(_one(), vc):
            clauses.append(c)
            await sched.enqueue(c)
    except Exception as e:
        err = e
    detect_ms = int((time.monotonic()-t0)*1000)
    result['detect_ms'] = detect_ms
    result['clauses_received_before_failure'] = len(clauses)
    result['first_clause_sample_rate'] = clauses[0].sample_rate if clauses else None
    result['first_clause_bytes'] = len(clauses[0].audio_data) if clauses else 0
    result['exception_type'] = type(err).__name__ if err else None
    result['exception_msg'] = str(err)[:200] if err else None
    result['sched_after_failure'] = _snap_scheduler(sched)
    VeenaAdapter._open_stream = staticmethod(orig_open)
    recovery = await _run_healthy_turn('F6_recovery')
    result['recovery'] = recovery
    print(f'  detect_ms={detect_ms} clauses_before={len(clauses)} exception={result["exception_type"]} recovered={recovery["ok"]}')
    return result


# ============================================================
# F7 — network/tunnel interruption
# ============================================================
async def test_f7_network_interrupt():
    print('\n--- F7: Network interrupt during TTS ---')
    _capture_logs()
    result = {'test': 'F7_network_interrupt'}
    gpu = build_gpu_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=gpu, base_url=os.environ['TTS_BASE_URL'])
    sched = PlaybackScheduler()
    orig_open = VeenaAdapter._open_stream
    async def _timeout_open(client, url, payload):
        raise httpx.ReadTimeout('injected: read timeout (simulated tunnel drop)')
    VeenaAdapter._open_stream = staticmethod(_timeout_open)
    vc = VoiceConfig(rate_scale=1.0, language='hi')
    async def _one():
        yield SHORT_TEXT
    t0 = time.monotonic()
    err = None
    clauses = []
    try:
        async for c in await adapter.synthesize_stream(_one(), vc):
            clauses.append(c)
    except Exception as e:
        err = e
    detect_ms = int((time.monotonic()-t0)*1000)
    result['detect_ms'] = detect_ms
    result['exception_type'] = type(err).__name__ if err else None
    result['exception_msg'] = str(err)[:200] if err else None
    result['sched_blocked'] = _snap_scheduler(sched)
    VeenaAdapter._open_stream = staticmethod(orig_open)
    recovery = await _run_healthy_turn('F7_recovery')
    result['recovery'] = recovery
    print(f'  detect_ms={detect_ms} exception={result["exception_type"]} recovered={recovery["ok"]}')
    return result


# ============================================================
# F8 — failure during buffered-streaming
# ============================================================
async def test_f8_buffered_mode_failure():
    print('\n--- F8: Failure during buffered_streaming@400 ---')
    _capture_logs()
    result = {'test': 'F8_buffered_mode_failure'}
    gpu = build_gpu_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=gpu, base_url=os.environ['TTS_BASE_URL'])
    sched = PlaybackScheduler()
    gate = StartupBufferGate(playback=sched, mode=TTSMode.BUFFERED_STREAMING, threshold_ms=400,
                             bytes_per_sample=2)
    # Inject: raise after first chunk (like F6)
    orig_open = VeenaAdapter._open_stream
    async def _wrapped_open(client, url, payload):
        resp = await orig_open(client, url, payload)
        orig_aiter = resp.aiter_bytes
        async def _fault_aiter(chunk_size=None):
            got = 0
            async for chunk in orig_aiter(chunk_size or 4096):
                got += 1
                yield chunk
                if got >= 1:
                    raise httpx.RemoteProtocolError('injected: mid-stream drop during buffering')
        resp.aiter_bytes = _fault_aiter
        return resp
    VeenaAdapter._open_stream = staticmethod(_wrapped_open)

    vc = VoiceConfig(rate_scale=1.0, language='hi')
    async def _one():
        yield SHORT_TEXT
    buffered_clauses = []
    t0 = time.monotonic()
    err = None
    try:
        async for c in await adapter.synthesize_stream(_one(), vc):
            buffered_clauses.append(c)
            await gate.enqueue(c)
    except Exception as e:
        err = e
    detect_ms = int((time.monotonic()-t0)*1000)
    result['detect_ms'] = detect_ms
    result['clauses_buffered_before_failure'] = len(buffered_clauses)
    result['exception_type'] = type(err).__name__ if err else None
    result['exception_msg'] = str(err)[:200] if err else None
    result['gate_buffered_ms_after_failure'] = gate.buffered_ms
    result['gate_buffered_clauses_after_failure'] = gate.buffered_clauses
    result['gate_released_flag'] = gate.released
    depth_before_flush = sched.queue_depth() if hasattr(sched,'queue_depth') else None
    try:
        await gate.flush_final()
        flush_err = None
    except Exception as e:
        flush_err = repr(e)
    result['flush_final_error'] = flush_err
    result['sched_depth_after_flush_final'] = sched.queue_depth() if hasattr(sched,'queue_depth') else None
    result['sched_depth_before_flush_final'] = depth_before_flush
    VeenaAdapter._open_stream = staticmethod(orig_open)
    recovery = await _run_healthy_turn('F8_recovery')
    result['recovery'] = recovery
    print(f'  detect_ms={detect_ms} buffered_before={len(buffered_clauses)} exception={result["exception_type"]} recovered={recovery["ok"]}')
    return result


# ============================================================
# F9 — barge-in + failure + recovery
# ============================================================
async def test_f9_bargein_plus_failure():
    print('\n--- F9: Barge-in + failure + recovery ---')
    _capture_logs()
    result = {'test': 'F9_bargein_plus_failure_plus_recovery'}
    gpu = build_gpu_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=gpu, base_url=os.environ['TTS_BASE_URL'])
    sched = PlaybackScheduler()
    vc = VoiceConfig(rate_scale=1.0, language='hi')

    # (1) start real synthesis, gather 1 clause
    async def _one():
        yield SHORT_TEXT + ' आपका EMI बकाया है।'
    clauses_pre_barge = []
    stream = await adapter.synthesize_stream(_one(), vc)
    async for c in stream:
        clauses_pre_barge.append(c)
        await sched.enqueue(c)
        if len(clauses_pre_barge) >= 2:
            break
    gen_before = sched.generation
    # (2) trigger barge-in flush
    await sched.flush()
    gen_after_flush = sched.generation
    # Attempt to close stream cleanly
    try:
        await stream.aclose()
    except Exception:
        pass
    # (3) simulate failure on next attempt
    orig_open = VeenaAdapter._open_stream
    async def _fail(client, url, payload):
        raise httpx.ConnectError('injected: failure right after barge-in')
    VeenaAdapter._open_stream = staticmethod(_fail)
    err = None
    try:
        async for c in await adapter.synthesize_stream(_one(), vc):
            pass
    except Exception as e:
        err = e
    result['post_bargein_failure_exception'] = type(err).__name__ if err else None
    # (4) clear barge-in, restore, start new turn
    sched.clear_barge_in()
    VeenaAdapter._open_stream = staticmethod(orig_open)
    recovery_sched = PlaybackScheduler()
    # simulate new turn generation continuity — new_turn should still respect current gen
    async def _short():
        yield SHORT_TEXT
    new_clauses = []
    async for c in await adapter.synthesize_stream(_short(), vc):
        new_clauses.append(c)
        await recovery_sched.enqueue(c)
    result['clauses_pre_barge'] = len(clauses_pre_barge)
    result['gen_before_barge'] = gen_before
    result['gen_after_flush'] = gen_after_flush
    result['new_turn_clauses'] = len(new_clauses)
    # Try to enqueue a STALE (old-generation) clause into new scheduler → should be dropped
    stale = clauses_pre_barge[0].model_copy(update={'generation': gen_before})
    recovery_sched._generation = 1  # simulate advanced generation
    depth_before = recovery_sched.queue_depth() if hasattr(recovery_sched,'queue_depth') else None
    await recovery_sched.enqueue(stale)
    depth_after = recovery_sched.queue_depth() if hasattr(recovery_sched,'queue_depth') else None
    result['stale_enqueue_dropped'] = (depth_before == depth_after)
    result['recovery_scheduler_gen'] = recovery_sched.generation
    result['log_snippet'] = [l for l in _get_logs().splitlines() if 'stale' in l.lower() or 'barge' in l.lower()][:5]
    print(f'  gen: {gen_before}->{gen_after_flush}, post_barge_failure={result["post_bargein_failure_exception"]}, '
          f'new_turn_clauses={len(new_clauses)}, stale_dropped={result["stale_enqueue_dropped"]}')
    return result


# ============================================================
# F10 — rapid failure/recovery
# ============================================================
async def test_f10_rapid_cycle():
    print('\n--- F10: Rapid failure/recovery cycle ---')
    result = {'test': 'F10_rapid_cycle', 'iterations': []}
    for i in range(3):
        # fail
        gpu = build_gpu_scheduler()
        adapter = VeenaAdapter(gpu_scheduler=gpu, base_url=os.environ['TTS_BASE_URL'])
        orig_open = VeenaAdapter._open_stream
        async def _fail(client, url, payload):
            raise httpx.ConnectError(f'injected iter {i}')
        VeenaAdapter._open_stream = staticmethod(_fail)
        err = None
        try:
            async for c in await adapter.synthesize_stream((yield_one_gen()), VoiceConfig(rate_scale=1.0, language='hi')):
                pass
        except Exception as e:
            err = e
        VeenaAdapter._open_stream = staticmethod(orig_open)
        # recover
        rec = await _run_healthy_turn(f'F10_iter{i}')
        result['iterations'].append({
            'iter': i,
            'failed': err is not None,
            'exception': type(err).__name__ if err else None,
            'recovery_ok': rec.get('ok'),
            'recovery_clauses': rec.get('n_clauses'),
        })
    # asyncio task snapshot at end
    tasks = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()]
    result['orphan_tasks'] = len(tasks)
    result['orphan_task_names'] = [t.get_name() for t in tasks][:10]
    print(f'  iterations={len(result["iterations"])} orphan_tasks={result["orphan_tasks"]}')
    return result


async def yield_one_gen():
    yield SHORT_TEXT


# ============================================================
# F11 — new-turn recovery (ConversationEngine path)
# ============================================================
async def test_f11_new_turn_via_engine():
    print('\n--- F11: New-turn recovery via ConversationEngine ---')
    result = {'test': 'F11_new_turn_via_engine'}
    try:
        from deployment.cpu.app import build_conversation_engine
        engine = build_conversation_engine()
    except Exception as e:
        result['engine_build_error'] = repr(e)[:300]
        print(f'  engine build failed: {result["engine_build_error"]}')
        return result
    # After F1-F10 injections/restores, start a fresh scripted turn
    try:
        playback = PlaybackScheduler()
        t0 = time.monotonic()
        clauses = await engine.speak_scripted_text(SHORT_TEXT, playback=playback)
        result['wall_ms'] = int((time.monotonic()-t0)*1000)
        result['n_clauses'] = len(clauses)
        result['first_clause_bytes'] = len(clauses[0].audio_data) if clauses else 0
        result['is_final_at_end'] = clauses[-1].is_final if clauses else None
        result['playback_gen_after'] = playback.generation
        result['ok'] = len(clauses) > 0
    except Exception as e:
        result['ok'] = False
        result['error'] = repr(e)[:300]
    print(f'  ok={result.get("ok")} clauses={result.get("n_clauses")}')
    return result


# ============================================================
# STARTING STATE + HEALTH
# ============================================================
async def starting_state():
    import subprocess
    def _http_get(url, timeout=5):
        try:
            async def _g():
                async with httpx.AsyncClient(timeout=timeout) as c:
                    r = await c.get(url)
                    return r.status_code, r.text[:200]
            return asyncio.get_event_loop().run_until_complete(_g())
        except Exception as e:
            return -1, repr(e)
    async with httpx.AsyncClient(timeout=30) as c:
        llm_r = await c.get(os.environ['LLM_BASE_URL']+'/v1/models')
        stt_r = await c.get(os.environ['STT_BASE_URL']+'/health/ready')
        tts_r = await c.get(os.environ['TTS_BASE_URL']+'/health/ready')
    sched = PlaybackScheduler()
    return {
        'git_head': subprocess.getoutput('git -C /opt/voiceos/app rev-parse HEAD'),
        'working_tree_lines': int(subprocess.getoutput('git -C /opt/voiceos/app status --short | wc -l')),
        'hostname': subprocess.getoutput('hostname'),
        'llm_status': llm_r.status_code,
        'stt_status_json': stt_r.json() if stt_r.status_code==200 else stt_r.text[:200],
        'tts_status_json': tts_r.json() if tts_r.status_code==200 else tts_r.text[:200],
        'redis': subprocess.getoutput('redis-cli ping'),
        'tts_mode_env': os.environ.get('VOICEOS_TTS_MODE', '(unset)'),
        'tts_buffer_ms_env': os.environ.get('VOICEOS_TTS_BUFFER_MS', '(unset)'),
        'playback_scheduler_gen_init': sched.generation,
        'barge_in_event_init': sched.barge_in_event.is_set(),
    }


async def main():
    print('=== 10.1 STARTING STATE ===')
    ss = await starting_state()
    print(json.dumps(ss, indent=2, default=str))
    results = {'starting_state': ss, 'tests': {}}

    # Baseline healthy turn (to confirm live path works before any injections)
    baseline = await _run_healthy_turn('baseline_healthy')
    results['baseline_healthy'] = baseline
    print(f'\n[BASELINE] healthy turn: ok={baseline["ok"]} n_clauses={baseline.get("n_clauses")} wall_ms={baseline.get("wall_ms")}')

    results['tests']['F1'] = await test_f1_llm_failure()
    results['tests']['F2'] = await test_f2_stt_failure()
    results['tests']['F3'] = await test_f3_tts_pre_synth_failure()
    results['tests']['F6'] = await test_f6_tts_midstream_failure()
    results['tests']['F7'] = await test_f7_network_interrupt()
    results['tests']['F8'] = await test_f8_buffered_mode_failure()
    results['tests']['F9'] = await test_f9_bargein_plus_failure()
    results['tests']['F10'] = await test_f10_rapid_cycle()
    results['tests']['F11'] = await test_f11_new_turn_via_engine()

    # Final: task/queue snapshot
    tasks = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()]
    results['final_orphan_tasks'] = len(tasks)
    results['final_orphan_task_names'] = [t.get_name() for t in tasks][:20]

    print('\n=== FINAL SUMMARY JSON ===')
    print(json.dumps(results, indent=2, default=str))

asyncio.run(main())
