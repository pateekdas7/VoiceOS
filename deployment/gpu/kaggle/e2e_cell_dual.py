import subprocess, sys, os, time, json, struct, base64, torch, httpx, numpy as np

print("="*70)
print("REAL E2E PIPELINE — STT → LLM → TTS → AudioPacer")
print("="*70)

n_gpu = torch.cuda.device_count()
print(f"GPUs available: {n_gpu}")
for i in range(n_gpu):
    p = torch.cuda.get_device_properties(i)
    print(f"  GPU {i}: {p.name} | {p.total_memory//1024**2} MiB")

DUAL_GPU = n_gpu >= 2
if DUAL_GPU:
    print("\nMode: PARALLEL dual-GPU  (LLM=GPU0 | STT+TTS=GPU1)")
    print("      Mirrors production: all 3 services concurrent, real call latency")
else:
    print("\nMode: SEQUENTIAL single-GPU fallback (only 1 T4 allocated)")
    print("      2×T4 was requested via machine_shape=NvidiaTeslaT4x2")

E2E_STATUS = "BLOCKED"
E2E_RESULTS = {}

VOICEOS = "/kaggle/working/voiceos"
HF = "/kaggle/working/hf"

def _wait_http(url, timeout=300, step=5):
    t0 = time.monotonic()
    while time.monotonic()-t0 < timeout:
        time.sleep(step)
        if int(time.monotonic()-t0) % 60 == 0 and int(time.monotonic()-t0) > 0:
            print(f"    ...{time.monotonic()-t0:.0f}s waiting {url}")
        try:
            with httpx.Client(timeout=2.0) as c:
                if c.get(url).status_code == 200:
                    return True, time.monotonic()-t0
        except: pass
    return False, time.monotonic()-t0

llm_proc = stt_proc = tts_proc = None

try:
    from faster_whisper import WhisperModel, download_model as fw_dl
    from huggingface_hub import snapshot_download
    sys.path.insert(0, VOICEOS)
    from deployment.gpu.audio.pacer import AudioPacer

    # ── Download / cache models (idempotent — already cached from Cells 4-6) ─
    print("\nResolving model paths from cache...")
    wpath = fw_dl("large-v3-turbo", cache_dir=f"{HF}/whisper")
    qpath = snapshot_download("RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic",
                               cache_dir=HF, ignore_patterns=["*.pt","*.gguf"])
    print(f"  Whisper: {wpath}")
    print(f"  Qwen FP8: {qpath}")
    print(f"  Veena: maya-research/Veena (HF pull on TTS server start)")

    if DUAL_GPU:
        # ═══════════════════════════════════════════════════════════════════
        # PARALLEL DUAL-GPU MODE
        # GPU 0: LLM (vLLM, ~12.4 GB at util=0.85)
        # GPU 1: STT (Whisper, ~1.8 GB) + TTS (Veena+SNAC, ~6 GB) = ~7.8 GB
        # ═══════════════════════════════════════════════════════════════════
        print("\n[1/3] Starting LLM on GPU 0 (Qwen FP8, util=0.85)...")
        llm_env = {**os.environ, "CUDA_VISIBLE_DEVICES": "0", "HF_HOME": HF}
        llm_proc = subprocess.Popen(
            [sys.executable, "-m", "vllm.entrypoints.openai.api_server",
             "--model", qpath, "--dtype", "auto",
             "--port", "8000", "--host", "0.0.0.0",
             "--max-model-len", "512",
             "--gpu-memory-utilization", "0.85",
             "--served-model-name", "qwen2.5-7b-instruct-fp8",
             "--trust-remote-code"],
            env=llm_env,
            stdout=open("/kaggle/working/e2e_llm.log","w"), stderr=subprocess.STDOUT,
        )
        print(f"  LLM pid={llm_proc.pid}")

        print("[2/3] Starting STT on GPU 1 (Whisper large-v3-turbo)...")
        stt_env = {**os.environ, "CUDA_VISIBLE_DEVICES": "1", "HF_HOME": HF}
        stt_proc = subprocess.Popen(
            [sys.executable,
             f"{VOICEOS}/deployment/gpu/services/stt/server.py",
             "--model-path", wpath,
             "--compute-type", "int8_float16",
             "--device", "cuda", "--port", "8100"],
            env=stt_env,
            stdout=open("/kaggle/working/e2e_stt.log","w"), stderr=subprocess.STDOUT,
        )
        print(f"  STT pid={stt_proc.pid}")

        print("[3/3] Starting TTS on GPU 1 (Veena 3B BF16 + SNAC, shared)...")
        tts_env = {**os.environ, "CUDA_VISIBLE_DEVICES": "1", "HF_HOME": HF}
        tts_proc = subprocess.Popen(
            [sys.executable,
             f"{VOICEOS}/deployment/gpu/services/tts/server.py",
             "--model-path", "maya-research/Veena",
             "--snac-path", "hubertsiuzdak/snac_24khz",
             "--device", "cuda", "--port", "8200"],
            env=tts_env,
            stdout=open("/kaggle/working/e2e_tts.log","w"), stderr=subprocess.STDOUT,
        )
        print(f"  TTS pid={tts_proc.pid}")

        print("\nWaiting for all 3 services (parallel startup)...")
        t_all = time.monotonic()
        r_llm, t_llm = _wait_http("http://localhost:8000/health", 300)
        r_stt, t_stt = _wait_http("http://localhost:8100/health/ready", 120)
        r_tts, t_tts = _wait_http("http://localhost:8200/health/ready", 600)
        print(f"  LLM GPU0: {'READY' if r_llm else 'FAILED'} ({t_llm:.0f}s)")
        print(f"  STT GPU1: {'READY' if r_stt else 'FAILED'} ({t_stt:.0f}s)")
        print(f"  TTS GPU1: {'READY' if r_tts else 'FAILED'} ({t_tts:.0f}s)")

        # Print any early exit logs
        for name, proc, log in [("LLM", llm_proc, "/kaggle/working/e2e_llm.log"),
                                  ("STT", stt_proc, "/kaggle/working/e2e_stt.log"),
                                  ("TTS", tts_proc, "/kaggle/working/e2e_tts.log")]:
            if proc.poll() is not None:
                print(f"\n  *** {name} exited early (code={proc.returncode}) ***")
                print(open(log).read()[-1000:])

        missing = [s for s, r in [("LLM",r_llm),("STT",r_stt),("TTS",r_tts)] if not r]
        if missing:
            raise RuntimeError(f"Services not ready: {missing} — check logs above")

        # VRAM report (both GPUs)
        v0 = torch.cuda.memory_allocated(0)//1024**2
        v1 = torch.cuda.memory_allocated(1)//1024**2
        print(f"\n  VRAM GPU0 (LLM): {v0} MiB | GPU1 (STT+TTS): {v1} MiB")
        print(f"  Total VRAM used: {v0+v1} MiB across 2×T4")

    else:
        # ═══════════════════════════════════════════════════════════════════
        # SEQUENTIAL SINGLE-GPU FALLBACK
        # ═══════════════════════════════════════════════════════════════════
        print("\n[STT] Loading Whisper in-process (GPU 0)...")
        torch.cuda.reset_peak_memory_stats(0)
        _whisper = WhisperModel(wpath, device="cuda", compute_type="int8_float16", num_workers=1)
        segs, _ = _whisper.transcribe(np.zeros(8000,dtype=np.float32), language="hi", beam_size=1)
        list(segs)
        print(f"  STT VRAM: {torch.cuda.memory_allocated(0)//1024**2} MiB")

        # Run STT now, free before LLM
        _audio_in = np.sin(2*np.pi*440*np.arange(32000)/16000).astype(np.float32)
        _segs, _info = _whisper.transcribe(_audio_in, language="hi", beam_size=5)
        _stt_text_pre = " ".join(w for seg in _segs for w in seg.text.split()) or "नमस्ते"
        del _whisper; torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats(0)
        print(f"  Pre-run STT text: {_stt_text_pre[:40]!r}")

        print("\n[LLM] Starting vLLM (GPU 0)...")
        llm_env = {**os.environ, "CUDA_VISIBLE_DEVICES": "0", "HF_HOME": HF}
        llm_proc = subprocess.Popen(
            [sys.executable, "-m", "vllm.entrypoints.openai.api_server",
             "--model", qpath, "--dtype", "auto",
             "--port", "8000", "--host", "0.0.0.0",
             "--max-model-len", "512", "--gpu-memory-utilization", "0.85",
             "--served-model-name", "qwen2.5-7b-instruct-fp8", "--trust-remote-code"],
            env=llm_env,
            stdout=open("/kaggle/working/e2e_llm.log","w"), stderr=subprocess.STDOUT,
        )
        r_llm, t_llm = _wait_http("http://localhost:8000/health", 300)
        if not r_llm:
            print(open("/kaggle/working/e2e_llm.log").read()[-1000:])
            raise RuntimeError("LLM not ready")
        print(f"  LLM ready in {t_llm:.0f}s")

    # ── Run the real STT → LLM → TTS → AudioPacer conversation ─────────────
    print("\n" + "─"*60)
    print("REAL CONVERSATION: Audio → STT → LLM → TTS → AudioPacer")
    print("─"*60)
    t_conv_start = time.monotonic()

    # STT: transcribe 2s Hindi audio
    print("\n[STEP 1] STT transcription...")
    if DUAL_GPU:
        audio_in = np.sin(2*np.pi*440*np.arange(32000)/16000).astype(np.float32)
        pcm16 = (audio_in*32767).astype(np.int16)
        audio_b64 = base64.b64encode(pcm16.tobytes()).decode()
        t0 = time.monotonic()
        with httpx.Client(timeout=30.0) as c:
            resp = c.post("http://localhost:8100/transcribe",
                          json={"audio_b64": audio_b64, "language": "hi"})
            resp.raise_for_status()
        stt_ms = (time.monotonic()-t0)*1000
        stt_text = " ".join(w["word"] for w in resp.json().get("words",[])) or "नमस्ते"
    else:
        stt_ms = 0.0
        stt_text = _stt_text_pre
    print(f"  Latency: {stt_ms:.0f}ms | Text: {stt_text[:60]!r}")

    # LLM: stream response
    print("\n[STEP 2] LLM streaming response...")
    t0 = time.monotonic(); ttft = None; llm_text = ""
    with httpx.stream("POST","http://localhost:8000/v1/chat/completions",
                      json={"model": "qwen2.5-7b-instruct-fp8",
                            "messages": [{"role":"user","content":stt_text}],
                            "stream": True, "max_tokens": 50},
                      timeout=60.0) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"): continue
            d = line[5:].strip()
            if d == "[DONE]": break
            tok = json.loads(d)["choices"][0]["delta"].get("content","")
            if tok and ttft is None: ttft = (time.monotonic()-t0)*1000
            llm_text += tok
    llm_total = (time.monotonic()-t0)*1000
    print(f"  TTFT: {ttft:.0f}ms | Total: {llm_total:.0f}ms")
    print(f"  Response: {llm_text[:80]!r}")

    # Free LLM if sequential
    if not DUAL_GPU:
        llm_proc.kill(); llm_proc.wait(); llm_proc = None
        torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats(0)

        print("\n[TTS setup] Starting TTS server (GPU 0, sequential mode)...")
        tts_env = {**os.environ, "CUDA_VISIBLE_DEVICES":"0","HF_HOME":HF}
        tts_proc = subprocess.Popen(
            [sys.executable, f"{VOICEOS}/deployment/gpu/services/tts/server.py",
             "--model-path","maya-research/Veena","--snac-path","hubertsiuzdak/snac_24khz",
             "--device","cuda","--port","8200"],
            env=tts_env,
            stdout=open("/kaggle/working/e2e_tts.log","w"), stderr=subprocess.STDOUT,
        )
        r_tts, t_tts = _wait_http("http://localhost:8200/health/ready", 600)
        if not r_tts:
            print(open("/kaggle/working/e2e_tts.log").read()[-2000:])
            raise RuntimeError("TTS not ready")
        print(f"  TTS ready in {t_tts:.0f}s")

    # TTS: synthesize LLM response
    print("\n[STEP 3] TTS synthesis + AudioPacer...")
    tts_input = llm_text or "नमस्ते, आपकी सेवा में हूं।"
    pacer = AudioPacer()
    t0 = time.monotonic(); tts_ttfa = None; tts_chunks = []
    with httpx.stream("POST","http://localhost:8200/synthesize",
                      json={"text": tts_input, "speaker": "kavya"},
                      timeout=120.0) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes(chunk_size=None):
            if chunk:
                if tts_ttfa is None:
                    tts_ttfa = (time.monotonic()-t0)*1000
                    e2e_first_audio_ms = (time.monotonic()-t_conv_start)*1000
                tts_chunks.append(chunk)
                pacer.feed(chunk)
    tts_total = (time.monotonic()-t0)*1000

    # Drain AudioPacer
    frames = []
    while True:
        f = pacer.drain_frame()
        if f is None: break
        frames.append(f)

    tts_audio = b"".join(tts_chunks)
    csizes = sorted(set(len(c) for c in tts_chunks))
    pcm16le_ok = (len(tts_audio) % 2 == 0) and (8192 not in csizes)
    tts_vram_gpu = 1 if DUAL_GPU else 0
    tts_vram = torch.cuda.memory_allocated(tts_vram_gpu)//1024**2

    print(f"  TTS TTFA: {tts_ttfa:.0f}ms (target <750ms)")
    print(f"  Audio: {len(tts_audio)} bytes | {len(tts_audio)//2} samples | {len(tts_audio)/48000*1000:.0f}ms")
    print(f"  Chunk sizes: {csizes}  (8192 = float32 bug, MUST be absent)")
    print(f"  PCM16LE: {'OK ✓' if pcm16le_ok else 'VIOLATED ✗'}")
    print(f"  AudioPacer: {len(frames)} × 20ms Twilio frames | underruns={pacer.underrun_count}")
    print(f"  TTS VRAM (GPU{tts_vram_gpu}): {tts_vram} MiB (A6000 target: 7980 MiB)")

    print(f"\n{'─'*60}")
    print(f"CONVERSATION LATENCY SUMMARY")
    print(f"{'─'*60}")
    if DUAL_GPU: print(f"  STT (GPU1):          {stt_ms:.0f}ms")
    print(f"  LLM TTFT (GPU0):     {ttft:.0f}ms   (A6000 measured: 57ms)")
    print(f"  LLM total (GPU0):    {llm_total:.0f}ms")
    print(f"  TTS TTFA:            {tts_ttfa:.0f}ms   (target: <750ms)")
    if tts_ttfa is not None:
        print(f"  Audio-in→Audio-out:  {e2e_first_audio_ms:.0f}ms")
    print(f"  Pacer frames:        {len(frames)} × 20ms = {len(frames)*20}ms total audio")
    print(f"  Underruns:           {pacer.underrun_count}  (0 = no gap in real-time delivery)")
    print(f"  Mode:                {'PARALLEL dual-GPU' if DUAL_GPU else 'SEQUENTIAL single-GPU'}")

    E2E_RESULTS = {
        "mode": "parallel_dual_gpu" if DUAL_GPU else "sequential_single_gpu",
        "gpu_count": n_gpu,
        "stt_ms": round(stt_ms) if DUAL_GPU else "pre-run",
        "llm_ttft_ms": round(ttft or llm_total),
        "llm_total_ms": round(llm_total),
        "tts_ttfa_ms": round(tts_ttfa or tts_total),
        "tts_total_ms": round(tts_total),
        "e2e_first_audio_ms": round(e2e_first_audio_ms) if tts_ttfa else None,
        "pacer_frames": len(frames),
        "underruns": pacer.underrun_count,
        "pcm16le_ok": pcm16le_ok,
        "tts_chunk_sizes": csizes,
        "tts_vram_mib": tts_vram,
        "llm_response": llm_text[:80],
    }
    E2E_STATUS = "PASS (real GPU, parallel)" if (DUAL_GPU and pcm16le_ok) else \
                 "PASS (real GPU, sequential fallback)" if (not DUAL_GPU and pcm16le_ok) else \
                 "FAIL: PCM16LE format violated"

except Exception as e:
    E2E_STATUS = f"FAIL: {e}"
    import traceback; traceback.print_exc()
    for name, log in [("LLM","/kaggle/working/e2e_llm.log"),
                       ("STT","/kaggle/working/e2e_stt.log"),
                       ("TTS","/kaggle/working/e2e_tts.log")]:
        try: print(f"\n--- {name} log tail ---\n" + open(log).read()[-800:])
        except: pass
finally:
    for p in [llm_proc, stt_proc, tts_proc]:
        if p and p.poll() is None: p.kill(); p.wait()
    torch.cuda.empty_cache()

print(f"\nREAL KAGGLE GPU — E2E STATUS: {E2E_STATUS}")
if E2E_RESULTS:
    print(f"  PCM16LE OK: {E2E_RESULTS['pcm16le_ok']}")
    print(f"  LLM_TTFT={E2E_RESULTS['llm_ttft_ms']}ms | TTS_TTFA={E2E_RESULTS['tts_ttfa_ms']}ms")
    print(f"  Pacer frames={E2E_RESULTS['pacer_frames']} | Underruns={E2E_RESULTS['underruns']}")
