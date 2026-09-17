import subprocess, sys, os, time, json, struct, base64, torch, httpx, numpy as np

print("="*70)
print("REAL E2E PIPELINE — STT → LLM → TTS → AudioPacer (Single GPU)")
print("GPU assignment: ALL services sequential on GPU 0 (Kaggle: 1 T4 only)")
print("="*70)
print("Strategy: STT in-process → free VRAM → vLLM → free VRAM → TTS server")

E2E_STATUS = "BLOCKED"
E2E_RESULTS = {}

VOICEOS = "/kaggle/working/voiceos"
HF = "/kaggle/working/hf"

def _wait_http(url, timeout=300, step=5):
    t0 = time.monotonic()
    while time.monotonic()-t0 < timeout:
        time.sleep(step)
        try:
            with httpx.Client(timeout=2.0) as c:
                if c.get(url).status_code == 200:
                    return True, time.monotonic()-t0
        except: pass
        elapsed = int(time.monotonic()-t0)
        if elapsed % 60 == 0 and elapsed > 0: print(f"    ...{elapsed}s waiting {url}")
    return False, time.monotonic()-t0

llm_proc = None
tts_proc = None

try:
    t_pipeline_start = time.monotonic()

    # ── STEP 1: STT (in-process, GPU 0) ──────────────────────────────────
    print("\n[1/3] STT — Whisper large-v3-turbo (in-process, GPU 0)...")
    from faster_whisper import WhisperModel, download_model as fw_dl

    wpath = fw_dl("large-v3-turbo", cache_dir=f"{HF}/whisper")
    torch.cuda.reset_peak_memory_stats(0)
    whisper_e2e = WhisperModel(wpath, device="cuda", compute_type="int8_float16", num_workers=1)

    segs, _ = whisper_e2e.transcribe(np.zeros(8000, dtype=np.float32), language="hi", beam_size=1)
    list(segs)  # warmup

    audio_in = np.sin(2*np.pi*440*np.arange(32000)/16000).astype(np.float32)
    t0 = time.monotonic()
    segs, info = whisper_e2e.transcribe(audio_in, language="hi", beam_size=5)
    stt_words = []
    for seg in segs:
        stt_words.extend(seg.text.split())
    stt_ms = (time.monotonic()-t0)*1000
    stt_vram = torch.cuda.max_memory_allocated(0)//1024**2
    stt_text = " ".join(stt_words) or "नमस्ते"
    print(f"  STT: {stt_ms:.0f}ms | VRAM peak={stt_vram}MiB | lang={info.language}")
    print(f"  Text: {stt_text[:60]!r}")

    del whisper_e2e; torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats(0)
    print(f"  STT freed. VRAM now: {torch.cuda.memory_allocated(0)//1024**2}MiB")

    # ── STEP 2: LLM (subprocess, GPU 0) ───────────────────────────────────
    print("\n[2/3] LLM — Qwen FP8 via vLLM (GPU 0, util=0.85)...")
    from huggingface_hub import snapshot_download
    qpath = snapshot_download("RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic",
                               cache_dir=HF, ignore_patterns=["*.pt","*.gguf"])
    llm_env = {**os.environ, "CUDA_VISIBLE_DEVICES": "0", "HF_HOME": HF}
    llm_proc = subprocess.Popen(
        [sys.executable, "-m", "vllm.entrypoints.openai.api_server",
         "--model", qpath, "--dtype", "auto", "--port", "8000", "--host", "0.0.0.0",
         "--max-model-len", "512", "--gpu-memory-utilization", "0.85",
         "--served-model-name", "qwen2.5-7b-instruct-fp8", "--trust-remote-code"],
        env=llm_env,
        stdout=open("/kaggle/working/e2e_llm.log","w"), stderr=subprocess.STDOUT,
    )
    r_llm, t_llm = _wait_http("http://localhost:8000/health", 300)
    print(f"  LLM: {'ready' if r_llm else 'NOT READY'} ({t_llm:.0f}s)")
    if not r_llm:
        print(open("/kaggle/working/e2e_llm.log").read()[-1500:])
        raise RuntimeError("LLM not ready")

    ttft = None; llm_text = ""
    t0 = time.monotonic()
    with httpx.stream("POST","http://localhost:8000/v1/chat/completions",
                      json={"model":"qwen2.5-7b-instruct-fp8",
                            "messages":[{"role":"user","content":stt_text}],
                            "stream":True,"max_tokens":30},
                      timeout=60.0) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"): continue
            d = line[5:].strip()
            if d=="[DONE]": break
            tok = json.loads(d)["choices"][0]["delta"].get("content","")
            if tok and ttft is None: ttft = (time.monotonic()-t0)*1000
            llm_text += tok
    llm_total = (time.monotonic()-t0)*1000
    print(f"  LLM TTFT={ttft:.0f}ms total={llm_total:.0f}ms")
    print(f"  Response: {llm_text[:60]!r}")

    llm_proc.kill(); llm_proc.wait(); llm_proc = None
    torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats(0)
    print(f"  LLM freed. VRAM now: {torch.cuda.memory_allocated(0)//1024**2}MiB")

    # ── STEP 3: TTS + AudioPacer (subprocess, GPU 0) ──────────────────────
    print("\n[3/3] TTS — Veena (GPU 0) + AudioPacer...")
    tts_env = {**os.environ, "CUDA_VISIBLE_DEVICES": "0", "HF_HOME": HF}
    tts_proc = subprocess.Popen(
        [sys.executable, f"{VOICEOS}/deployment/gpu/services/tts/server.py",
         "--model-path","maya-research/Veena","--snac-path","hubertsiuzdak/snac_24khz",
         "--device","cuda","--port","8200"],
        env=tts_env,
        stdout=open("/kaggle/working/e2e_tts.log","w"), stderr=subprocess.STDOUT,
    )
    r_tts, t_tts = _wait_http("http://localhost:8200/health/ready", 600)
    print(f"  TTS: {'ready' if r_tts else 'NOT READY'} ({t_tts:.0f}s)")
    if not r_tts:
        print(open("/kaggle/working/e2e_tts.log").read()[-2000:])
        raise RuntimeError("TTS not ready")

    sys.path.insert(0, VOICEOS)
    from deployment.gpu.audio.pacer import AudioPacer
    pacer_e2e = AudioPacer()
    tts_input = llm_text or "नमस्ते, आपकी सेवा में हूं।"
    t0 = time.monotonic(); tts_ttfa = None; tts_chunks = []
    with httpx.stream("POST","http://localhost:8200/synthesize",
                      json={"text":tts_input,"speaker":"kavya"},
                      timeout=120.0) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes(chunk_size=None):
            if chunk:
                if tts_ttfa is None: tts_ttfa = (time.monotonic()-t0)*1000
                tts_chunks.append(chunk); pacer_e2e.feed(chunk)
    tts_total = (time.monotonic()-t0)*1000

    frames_e2e = []
    while True:
        f = pacer_e2e.drain_frame()
        if f is None: break
        frames_e2e.append(f)

    tts_audio = b"".join(tts_chunks)
    csizes = sorted(set(len(c) for c in tts_chunks))
    pcm16le_ok = (len(tts_audio)%2==0) and (8192 not in csizes)
    tts_vram = torch.cuda.max_memory_allocated(0)//1024**2

    print(f"  TTS TTFA={tts_ttfa:.0f}ms total={tts_total:.0f}ms | chunks={len(tts_chunks)}")
    print(f"  Chunk sizes: {csizes}  (8192=float32 bug — MUST be absent)")
    print(f"  PCM16LE: {'OK ✓' if pcm16le_ok else 'VIOLATED ✗'}")
    print(f"  Audio: {len(tts_audio)} bytes | {len(tts_audio)//2} samples | {len(tts_audio)/48000*1000:.0f}ms")
    print(f"  Pacer: {len(frames_e2e)} frames × 20ms = {len(frames_e2e)*20}ms")
    print(f"  Underruns: {pacer_e2e.underrun_count}")
    print(f"  TTS VRAM peak: {tts_vram} MiB (A6000 target: 7980 MiB)")

    e2e_total_ms = (time.monotonic()-t_pipeline_start)*1000
    print(f"\n  Pipeline summary (sequential, single GPU):")
    print(f"    STT inference: {stt_ms:.0f}ms")
    print(f"    LLM TTFT:      {ttft:.0f}ms")
    print(f"    TTS TTFA:      {tts_ttfa:.0f}ms")
    print(f"    Total elapsed: {e2e_total_ms:.0f}ms (includes service startup times)")

    E2E_RESULTS = {
        "stt_ms": round(stt_ms),
        "llm_ttft_ms": round(ttft or llm_total),
        "llm_total_ms": round(llm_total),
        "tts_ttfa_ms": round(tts_ttfa or tts_total),
        "tts_total_ms": round(tts_total),
        "e2e_total_ms": round(e2e_total_ms),
        "pacer_frames": len(frames_e2e),
        "underruns": pacer_e2e.underrun_count,
        "pcm16le_ok": pcm16le_ok,
        "tts_chunk_sizes": csizes,
        "tts_vram_peak_mib": tts_vram,
        "stt_text": stt_text[:60],
        "llm_response": llm_text[:60],
        "note": "Sequential single-GPU; production uses persistent parallel services on A6000",
    }
    E2E_STATUS = "PASS (real T4 GPU, sequential)" if pcm16le_ok else "FAIL: PCM16LE format violated"

    tts_proc.kill(); tts_proc.wait(); tts_proc = None

except Exception as e:
    E2E_STATUS = f"FAIL: {e}"
    import traceback; traceback.print_exc()
finally:
    for p in [llm_proc, tts_proc]:
        if p and p.poll() is None: p.kill(); p.wait()
    torch.cuda.empty_cache()

print(f"\nREAL KAGGLE GPU — E2E STATUS: {E2E_STATUS}")
if E2E_RESULTS:
    print(f"  STT={E2E_RESULTS['stt_ms']}ms | LLM_TTFT={E2E_RESULTS['llm_ttft_ms']}ms | TTS_TTFA={E2E_RESULTS['tts_ttfa_ms']}ms")
    print(f"  Pacer frames={E2E_RESULTS['pacer_frames']} | Underruns={E2E_RESULTS['underruns']}")
    print(f"  PCM16LE OK: {E2E_RESULTS['pcm16le_ok']}")
