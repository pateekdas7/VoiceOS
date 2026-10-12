# =====================================================================
# VoiceOS T4 Veena TTS Benchmark — isolated from STT / VAD / LLM / CIL /
# Twilio / network / HindiScriptConverter / AudioPacer.
#
# Purpose: measure the CURRENT production Veena streaming implementation
# on Kaggle T4×2 to distinguish:
#   (a) GOOD STREAMING BUT SLOW  (chunks flow smoothly at slow real-time)
#   (b) BAD STREAMING / BLOCKING (long silence, then burst)
# by measuring TTFA + per-chunk cadence for 3 text lengths.
#
# Preserved verbatim from production:
#   - Veena BF16, SDPA, maya-research/Veena
#   - SNAC 24kHz, sliding-window decode over 21 tokens, decode every 7 tokens
#   - Generation params: do_sample=True, temperature=0.4, top_p=0.9, rep_pen=1.05
#   - Prompt shape: <spk_maitri> <text>, wrapped in Veena special tokens
#   - Speaker: maitri
#   - SpeakerLockProcessor with lock_tokens=21, boot_temp=0.05, base_temp=0.4
# =====================================================================
import sys, os, json, time, subprocess, threading, queue, types as _types
from threading import Thread

print("=" * 70)
print("VoiceOS T4 Veena Bench")
print("=" * 70)
print(f"Python: {sys.version.split()[0]}")
print(f"Time:   {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

# =====================================================================
# SECTION 0: GPU sanity
# =====================================================================
r = subprocess.run(
    "nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv",
    shell=True, capture_output=True, text=True,
)
print("\n--- nvidia-smi (pre-load) ---")
print(r.stdout)

# =====================================================================
# SECTION 1: Install exact Sprint-29 packages
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 1: Installing packages")
print("=" * 70)

def pip(pkg, timeout=600):
    t0 = time.monotonic()
    r = subprocess.run(
        f"{sys.executable} -m pip install --upgrade {pkg} -q 2>&1",
        shell=True, capture_output=True, text=True, timeout=timeout,
    )
    ok = r.returncode == 0
    print(f"  {'OK' if ok else 'FAIL'}: {pkg} ({time.monotonic()-t0:.0f}s)")
    if not ok:
        print(f"    {(r.stdout + r.stderr)[-300:]}")

for pkg in [
    "transformers==5.12.1",
    "tokenizers==0.22.2",
    "accelerate==1.7.0",
    "snac==1.0.0",
]:
    pip(pkg)

import torch  # noqa: E402
import numpy as np  # noqa: E402
from transformers import AutoTokenizer, AutoModelForCausalLM, LogitsProcessor, LogitsProcessorList  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from snac import SNAC  # noqa: E402

print(f"\nPyTorch:      {torch.__version__}")
print(f"CUDA:         {torch.version.cuda}")
print(f"GPU count:    {torch.cuda.device_count()}")
for i in range(torch.cuda.device_count()):
    p = torch.cuda.get_device_properties(i)
    print(f"  GPU {i}: {p.name} | {p.total_memory // 1024**2} MiB | sm_{p.major}{p.minor}")
print(f"CUDA_VISIBLE_DEVICES = {os.environ.get('CUDA_VISIBLE_DEVICES', '<unset>')}")

# =====================================================================
# SECTION 2: Load Veena + SNAC (production freeze — Sprint-29)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 2: Load Veena + SNAC")
print("=" * 70)

VEENA_REV = "8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f"
SNAC_REV  = "d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec"
HF_HOME   = "/kaggle/working/hf"
os.makedirs(HF_HOME, exist_ok=True)
os.environ["HF_HOME"] = HF_HOME

DEVICE = "cuda:0"  # bind Veena + SNAC to a single T4 (identify below)

print("Downloading Veena...")
t0 = time.monotonic()
veena_path = snapshot_download(
    "maya-research/Veena",
    revision=VEENA_REV,
    cache_dir=HF_HOME,
    ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
)
print(f"  Veena path: {veena_path} ({time.monotonic()-t0:.0f}s)")

_tokenizer = AutoTokenizer.from_pretrained(veena_path)
_model = AutoModelForCausalLM.from_pretrained(
    veena_path,
    torch_dtype=torch.bfloat16,
    attn_implementation="sdpa",
    device_map=DEVICE,
    low_cpu_mem_usage=True,
)
_model.eval()
print(f"  Veena loaded: {_model.__class__.__name__} on {DEVICE}")

print("\nDownloading SNAC...")
t0 = time.monotonic()
snac_dir = snapshot_download("hubertsiuzdak/snac_24khz", revision=SNAC_REV, cache_dir=HF_HOME)
snac_cfg_path = os.path.join(snac_dir, "config.json")
snac_wts_path = os.path.join(snac_dir, "pytorch_model.bin")
print(f"  SNAC path:  {snac_dir} ({time.monotonic()-t0:.0f}s)")

with open(snac_cfg_path) as f:
    snac_cfg_full = json.load(f)

SNAC_KEYS = {
    "sampling_rate", "encoder_dim", "encoder_rates", "latent_dim",
    "decoder_dim", "decoder_rates", "attn_window_size", "codebook_size",
    "codebook_dim", "vq_strides", "noise", "depthwise",
}
snac_cfg = {k: v for k, v in snac_cfg_full.items() if k in SNAC_KEYS}
snac_cfg["attn_window_size"] = None
_snac_model = SNAC(**snac_cfg).eval()

def _strip_attn(seq):
    return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != "LocalMHA"])

_snac_model.encoder.block = _strip_attn(_snac_model.encoder.block)
_snac_model.decoder.model = _strip_attn(_snac_model.decoder.model)
sd = torch.load(snac_wts_path, map_location="cpu", weights_only=False)
_snac_model.load_state_dict(sd)
_snac_model = _snac_model.to(DEVICE).eval()

def _snac_decode_compat(self, codes):
    z_q = None
    for i, quantizer in enumerate(self.quantizer.quantizers):
        z_q_i = quantizer.decode_code(codes[i])
        z_q_i = quantizer.out_proj(z_q_i)
        if quantizer.stride > 1:
            z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)
        z_q = z_q_i if z_q is None else z_q + z_q_i
    return self.decoder(z_q)

_snac_model.decode = _types.MethodType(_snac_decode_compat, _snac_model)

# smoke test
_test_codes = [
    torch.zeros(1, 3, dtype=torch.long, device=DEVICE),
    torch.zeros(1, 6, dtype=torch.long, device=DEVICE),
    torch.zeros(1, 12, dtype=torch.long, device=DEVICE),
]
with torch.no_grad():
    _tt = _snac_model.decode(_test_codes)
print(f"  SNAC smoke: shape={tuple(_tt.shape)}")

# Veena special-token IDs (verified against maya-research/Veena tokenizer)
_START_OF_HUMAN     = 128259
_END_OF_HUMAN       = 128260
_START_OF_AI        = 128261
_END_OF_AI          = 128262
_START_OF_SPEECH    = 128257
_END_OF_SPEECH      = 128258
_AUDIO_CODE_OFFSET  = 128266
_CODEBOOK_SIZE      = 4096
_TOKENS_PER_FRAME   = 7
_SLIDING_WINDOW     = 21
_SAMPLES_PER_FRAME  = 2048           # 85.33ms at 24kHz
_SAMPLE_RATE        = 24000
_CHUNK_BYTES        = _SAMPLES_PER_FRAME * 2  # 4096 bytes PCM16LE
_SNAC_MIN_TOKEN     = _AUDIO_CODE_OFFSET
_SNAC_MAX_TOKEN     = _AUDIO_CODE_OFFSET + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1
_SPK_MAITRI         = _tokenizer.convert_tokens_to_ids("<spk_maitri>")
print(f"  <spk_maitri> id = {_SPK_MAITRI}")

# =====================================================================
# SECTION 3: SpeakerLockProcessor  (identical to production Sprint-29 fix)
# =====================================================================
class SpeakerLockProcessor(LogitsProcessor):
    def __init__(self, prompt_len, lock_tokens=21, boot_temp=0.05, base_temp=0.4):
        self.prompt_len  = prompt_len
        self.lock_tokens = int(lock_tokens)
        self.scale       = float(base_temp) / max(float(boot_temp), 1e-6)

    def __call__(self, input_ids, scores):
        gen_pos = input_ids.shape[1] - self.prompt_len
        if gen_pos < self.lock_tokens:
            scores = scores * self.scale
        return scores

# =====================================================================
# SECTION 4: Custom token streamer (identical to production _SNACTokenStreamer)
# =====================================================================
class SNACTokenStreamer:
    def __init__(self):
        self._q = queue.SimpleQueue()

    def put(self, value):
        for tid in value.flatten().tolist():
            self._q.put(int(tid))

    def end(self):
        self._q.put(None)

    def __iter__(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            yield item

# =====================================================================
# SECTION 5: Sliding-window SNAC decode (identical to production _snac_decode_window)
# =====================================================================
def snac_decode_window(window):
    num_frames = _SLIDING_WINDOW // _TOKENS_PER_FRAME  # 3
    c0, c1, c2 = [], [], []
    for i in range(num_frames):
        b = i * _TOKENS_PER_FRAME
        c0.append(window[b])
        c1.append(window[b + 1])
        c2.append(window[b + 2])
        c2.append(window[b + 3])
        c1.append(window[b + 4])
        c2.append(window[b + 5])
        c2.append(window[b + 6])
    def _clamp(v):
        return [min(max(x, 0), _CODEBOOK_SIZE - 1) for x in v]
    codes = [
        torch.tensor(_clamp(c0), dtype=torch.long, device=DEVICE).unsqueeze(0),
        torch.tensor(_clamp(c1), dtype=torch.long, device=DEVICE).unsqueeze(0),
        torch.tensor(_clamp(c2), dtype=torch.long, device=DEVICE).unsqueeze(0),
    ]
    with torch.no_grad():
        audio_hat = _snac_model.decode(codes)
    middle = audio_hat.squeeze()[_SAMPLES_PER_FRAME:_SAMPLES_PER_FRAME * 2]
    pcm16 = (middle.cpu().float().clamp(-1.0, 1.0).numpy() * 32767).astype("int16")
    return pcm16.tobytes()

# =====================================================================
# SECTION 6: Streaming synthesis (mirrors production _stream_synthesis_sync)
# =====================================================================
def stream_synthesis(text, speaker="maitri", lock_tokens=21, boot_temp=0.05):
    """Yields (chunk_bytes, t_now_monotonic) tuples."""
    prompt = f"<spk_{speaker}> {text}"
    ids    = _tokenizer.encode(prompt, add_special_tokens=False)
    full   = [_START_OF_HUMAN, *ids, _END_OF_HUMAN, _START_OF_AI, _START_OF_SPEECH]
    input_ids = torch.tensor([full], device=DEVICE)
    max_new   = min(int(len(text) * 1.3) * _TOKENS_PER_FRAME + 21, 700)

    streamer = SNACTokenStreamer()
    lp = LogitsProcessorList([
        SpeakerLockProcessor(
            prompt_len=input_ids.shape[1],
            lock_tokens=lock_tokens,
            boot_temp=boot_temp,
            base_temp=0.4,
        )
    ])
    gen_err = []

    def _generate():
        try:
            with torch.no_grad():
                _model.generate(
                    input_ids,
                    max_new_tokens=max_new,
                    do_sample=True,
                    temperature=0.4,
                    top_p=0.9,
                    repetition_penalty=1.05,
                    pad_token_id=_tokenizer.pad_token_id or _END_OF_SPEECH,
                    eos_token_id=[_END_OF_SPEECH, _END_OF_AI],
                    logits_processor=lp,
                    streamer=streamer,
                )
        except Exception as exc:
            gen_err.append(exc)
        finally:
            streamer.end()

    th = Thread(target=_generate, daemon=True)
    th.start()

    audio_buffer = []
    audio_count  = 0
    try:
        for tid in streamer:
            if not (_SNAC_MIN_TOKEN <= tid <= _SNAC_MAX_TOKEN):
                continue
            pos = audio_count % _TOKENS_PER_FRAME
            cbv = tid - _AUDIO_CODE_OFFSET - pos * _CODEBOOK_SIZE
            audio_buffer.append(max(0, min(cbv, _CODEBOOK_SIZE - 1)))
            audio_count += 1
            if audio_count % _TOKENS_PER_FRAME == 0 and audio_count >= _SLIDING_WINDOW:
                chunk = snac_decode_window(audio_buffer[-_SLIDING_WINDOW:])
                yield chunk, time.monotonic()
    finally:
        th.join()
    if gen_err:
        raise RuntimeError(f"Generation failed: {gen_err[0]}") from gen_err[0]

# =====================================================================
# SECTION 7: GPU utilization sampler (background thread)
# =====================================================================
class GPUSampler(threading.Thread):
    def __init__(self, interval=0.25):
        super().__init__(daemon=True)
        self.interval = interval
        self.samples  = []  # list of (t_rel, [(idx, util, mem_used_mib), ...])
        self._stop_event = threading.Event()
        self._t0 = None

    def run(self):
        self._t0 = time.monotonic()
        while not self._stop_event.is_set():
            r = subprocess.run(
                "nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader,nounits",
                shell=True, capture_output=True, text=True,
            )
            rows = []
            for line in r.stdout.strip().splitlines():
                parts = [x.strip() for x in line.split(",")]
                if len(parts) == 3:
                    try:
                        rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
                    except ValueError:
                        pass
            self.samples.append((time.monotonic() - self._t0, rows))
            if self._stop_event.wait(self.interval):
                break

    def stop(self):
        self._stop_event.set()
        self.join(timeout=2.0)

    def summary(self):
        # per-GPU util avg/max, per-GPU mem avg/max
        if not self.samples:
            return {}
        agg = {}
        for _, rows in self.samples:
            for idx, util, mem in rows:
                a = agg.setdefault(idx, {"util": [], "mem": []})
                a["util"].append(util)
                a["mem"].append(mem)
        out = {}
        for idx, v in agg.items():
            out[idx] = {
                "util_avg": round(sum(v["util"]) / len(v["util"]), 1),
                "util_max": max(v["util"]),
                "mem_avg_mib": round(sum(v["mem"]) / len(v["mem"]), 1),
                "mem_max_mib": max(v["mem"]),
                "n_samples": len(v["util"]),
            }
        return out

# =====================================================================
# SECTION 8: Test corpus
# =====================================================================
CORPUS = [
    ("Short",  "Namaste, main Maitri hoon."),
    ("Medium", "Namaste Prateek ji, main Maitri hun Rajat Finance se. Aapke account mein pachaas hazaar rupay ka outstanding balance hai."),
    ("Long",   (
        "Main samajh sakti hun ki yeh mushkil waqt hai. "
        "Lekin hum aapke saath kaam karne ke liye taiyaar hain. "
        "Aap ek partial payment bhi kar sakte hain — jo bhi aap ke liye sambhav ho. "
        "Kya aap mujhe bata sakte hain ki aapke liye kya comfortable hoga? "
        "Hum ek flexible repayment plan bana sakte hain jo aapki situation mein fit ho. "
        "Kripya mujhe seedha bataiye."
    )),
]

# =====================================================================
# SECTION 9: Warm-up + benchmark loop
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 9: Warm-up")
print("=" * 70)
_ = list(stream_synthesis("Namaste.", speaker="maitri"))
print("  warm-up complete")

print("\n" + "=" * 70)
print("SECTION 10: Benchmark")
print("=" * 70)

results = []
for label, text in CORPUS:
    print(f"\n--- TEST: {label} ({len(text)} chars) ---")
    print(f"    text: {text[:80]}{'...' if len(text) > 80 else ''}")

    # Reset caches so cold cache TTFA is measured per call
    torch.cuda.empty_cache()
    mem_before = torch.cuda.memory_allocated(DEVICE) // 1024**2

    sampler = GPUSampler(interval=0.25)
    sampler.start()

    t0 = time.monotonic()
    t_first = None
    chunk_ts = []
    chunk_sizes = []
    total_bytes = 0

    for chunk, ts in stream_synthesis(text, speaker="maitri", lock_tokens=21, boot_temp=0.05):
        if t_first is None:
            t_first = ts
        chunk_ts.append(ts - t0)
        chunk_sizes.append(len(chunk))
        total_bytes += len(chunk)

    t_end = time.monotonic()
    sampler.stop()

    mem_after = torch.cuda.memory_allocated(DEVICE) // 1024**2
    audio_sec = total_bytes / 2 / _SAMPLE_RATE
    gen_sec   = t_end - t0
    ttfa_ms   = (t_first - t0) * 1000 if t_first else -1
    stream_span_sec = (chunk_ts[-1] - chunk_ts[0]) if len(chunk_ts) >= 2 else 0.0
    rtf = audio_sec / gen_sec if gen_sec > 0 else 0.0

    gpu = sampler.summary()

    inter_ms = []
    for i in range(1, len(chunk_ts)):
        inter_ms.append((chunk_ts[i] - chunk_ts[i - 1]) * 1000)
    inter_ms_min = min(inter_ms) if inter_ms else 0
    inter_ms_max = max(inter_ms) if inter_ms else 0
    inter_ms_avg = sum(inter_ms) / len(inter_ms) if inter_ms else 0

    entry = {
        "label": label,
        "chars": len(text),
        "chunks": len(chunk_sizes),
        "chunk_size_min": min(chunk_sizes) if chunk_sizes else 0,
        "chunk_size_max": max(chunk_sizes) if chunk_sizes else 0,
        "chunk_size_avg": round(sum(chunk_sizes) / len(chunk_sizes), 1) if chunk_sizes else 0,
        "total_bytes": total_bytes,
        "audio_sec": round(audio_sec, 3),
        "ttfa_ms": round(ttfa_ms, 1),
        "gen_sec": round(gen_sec, 3),
        "stream_span_sec": round(stream_span_sec, 3),
        "rtf": round(rtf, 4),
        "inter_chunk_ms_min": round(inter_ms_min, 1),
        "inter_chunk_ms_max": round(inter_ms_max, 1),
        "inter_chunk_ms_avg": round(inter_ms_avg, 1),
        "gpu_mem_before_mib": mem_before,
        "gpu_mem_after_mib":  mem_after,
        "gpu_utilization":    gpu,
        "chunk_timestamps_sec": [round(x, 4) for x in chunk_ts],
    }
    results.append(entry)

    print(f"  chunks:         {entry['chunks']}")
    print(f"  chunk size:     min={entry['chunk_size_min']} max={entry['chunk_size_max']} avg={entry['chunk_size_avg']}")
    print(f"  audio_sec:      {entry['audio_sec']}")
    print(f"  TTFA:           {entry['ttfa_ms']} ms")
    print(f"  total gen:      {entry['gen_sec']} s")
    print(f"  streaming span: {entry['stream_span_sec']} s (first chunk -> last chunk)")
    print(f"  RTF:            {entry['rtf']}  (audio_sec / gen_sec — >1 means faster than realtime)")
    print(f"  inter-chunk ms: min={entry['inter_chunk_ms_min']} max={entry['inter_chunk_ms_max']} avg={entry['inter_chunk_ms_avg']}")
    print(f"  GPU mem (MiB):  before={mem_before} after={mem_after}")
    print(f"  GPU util:       {gpu}")

# =====================================================================
# SECTION 11: Summary table + persist JSON
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 11: SUMMARY")
print("=" * 70)
hdr = f"{'Test':<7} | {'Chars':>5} | {'Audio(s)':>8} | {'TTFA(ms)':>9} | {'Total(s)':>8} | {'Chunks':>6} | {'RTF':>6} | {'GPU util avg':>13}"
print(hdr)
print("-" * len(hdr))
for e in results:
    gpu0 = e["gpu_utilization"].get(0, {})
    util_str = f"{gpu0.get('util_avg', 0)}/{gpu0.get('util_max', 0)}%"
    print(f"{e['label']:<7} | {e['chars']:>5} | {e['audio_sec']:>8} | {e['ttfa_ms']:>9} | {e['gen_sec']:>8} | {e['chunks']:>6} | {e['rtf']:>6} | {util_str:>13}")

out_path = "/kaggle/working/t4_veena_bench_results.json"
with open(out_path, "w") as f:
    json.dump({
        "meta": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu_count": torch.cuda.device_count(),
            "gpu_names": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "device_used": DEVICE,
            "veena_rev": VEENA_REV,
            "snac_rev": SNAC_REV,
            "lock_tokens": 21,
            "boot_temp": 0.05,
            "speaker": "maitri",
            "utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        },
        "results": results,
    }, f, indent=2)
print(f"\nWrote {out_path} ({os.path.getsize(out_path)} bytes)")

# Final nvidia-smi
r = subprocess.run("nvidia-smi", shell=True, capture_output=True, text=True)
print("\n--- nvidia-smi (post-run) ---")
print(r.stdout[:900])
print("\nDone.")
