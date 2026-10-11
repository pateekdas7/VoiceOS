# =====================================================================
# MAITRI VOICE SWITCH FIX KERNEL v4
# =====================================================================
# v3 forensic proved: male drift is in the GENERATION LAYER (24kHz)
#   P01 (13 words): 3/30 drift  (10%)
#   P02 (39 words): 22/30 drift (73%)
#   P03 (67 words): ~?/30 drift (high)
#
# Root cause (from Sprint-29 validation):
#   Veena stochastically samples from a "male-adjacent audio-code cluster"
#   during the first few generated tokens, which then locks the voice to male.
#   Sprint-29 fixed Kavya with SpeakerLockProcessor but NEVER applied it to Maitri.
#
# This kernel (v4):
#   Applies SpeakerLockProcessor to Maitri and sweeps lock_tokens = [7, 21, 35, 49]
#   on P07-P12 (same texts as P01-P06) to find the minimum lock that eliminates drift.
#
# v3 baseline (for comparison):
#   P01=3/30, P02=22/30, P03=TBD, P04=TBD, P05=TBD, P06=TBD
#
# AUDIT + FIX VALIDATION. No production modifications.
# =====================================================================
import sys, os, json, time, struct, wave, hashlib

print("=" * 70)
print("MAITRI VOICE SWITCH FIX KERNEL v4")
print("=" * 70)
print(f"Python: {sys.version}")
print(f"Time:   {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

# =====================================================================
# SECTION 0: Environment + GPU check
# =====================================================================
import subprocess
import torch
import numpy as np

r = subprocess.run("nvidia-smi", shell=True, capture_output=True, text=True)
print("\n--- nvidia-smi ---")
print(r.stdout[:800])

print(f"\nPyTorch:   {torch.__version__}")
print(f"CUDA:      {torch.version.cuda}")
print(f"GPU count: {torch.cuda.device_count()}")
GPU_NAME = "unknown"
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    GPU_NAME = p.name
    print(f"GPU 0:     {GPU_NAME} | {p.total_memory//1024**2} MiB | sm_{p.major}{p.minor}")

# =====================================================================
# SECTION 1: Install exact Sprint-29 packages
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 1: Installing Sprint-29 exact packages")
print("=" * 70)

def pip(pkg, timeout=600):
    t0 = time.monotonic()
    r = subprocess.run(
        f"{sys.executable} -m pip install --upgrade {pkg} -q 2>&1",
        shell=True, capture_output=True, text=True, timeout=timeout
    )
    ok = r.returncode == 0
    print(f"  {'OK' if ok else 'FAIL'}: {pkg} ({time.monotonic()-t0:.0f}s)")
    if not ok:
        print(f"    {(r.stdout+r.stderr)[-300:]}")
    return ok

for pkg in [
    "transformers==5.12.1",
    "tokenizers==0.22.2",
    "accelerate==1.7.0",
    "snac==1.0.0",
]:
    pip(pkg)

try:
    import scipy.signal
    print(f"  scipy.signal: OK")
except Exception as e:
    print(f"  scipy.signal: WARN ({e})")

# =====================================================================
# SECTION 2: Load Veena + SNAC (EXACT production method)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 2: Loading Veena + SNAC (Sprint-29 production freeze)")
print("=" * 70)

VEENA_REV = "8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f"
SNAC_REV  = "d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec"
HF_HOME   = "/kaggle/working/hf"
os.makedirs(HF_HOME, exist_ok=True)
os.environ["HF_HOME"] = HF_HOME

snac_site = subprocess.run(
    f"{sys.executable} -c 'import snac; import os; print(os.path.dirname(os.path.dirname(snac.__file__)))'",
    shell=True, capture_output=True, text=True
).stdout.strip()
if snac_site and snac_site not in sys.path:
    sys.path.insert(0, snac_site)
    print(f"  sys.path prepend: {snac_site}")

from transformers import AutoTokenizer, AutoModelForCausalLM
from huggingface_hub import snapshot_download
from snac import SNAC
import types as _types

print("\nDownloading Veena...")
t0 = time.monotonic()
veena_path = snapshot_download(
    "maya-research/Veena",
    revision=VEENA_REV,
    cache_dir=HF_HOME,
    ignore_patterns=["*.msgpack","*.h5","flax_model*","tf_model*"],
)
print(f"  Veena path: {veena_path} ({time.monotonic()-t0:.0f}s)")

tokenizer = AutoTokenizer.from_pretrained(veena_path)
model = AutoModelForCausalLM.from_pretrained(
    veena_path,
    torch_dtype=torch.bfloat16,
    attn_implementation="sdpa",
    device_map="cuda:0",
    low_cpu_mem_usage=True,
)
model.eval()
print(f"  Model: {model.__class__.__name__} | params: {sum(p.numel() for p in model.parameters())//1e6:.0f}M")
DEVICE = "cuda:0"

print("\nDownloading SNAC...")
t0 = time.monotonic()
snac_dir = snapshot_download(
    "hubertsiuzdak/snac_24khz",
    revision=SNAC_REV,
    cache_dir=HF_HOME,
)
snac_cfg_path = os.path.join(snac_dir, "config.json")
snac_wts_path = os.path.join(snac_dir, "pytorch_model.bin")
print(f"  SNAC dir: {snac_dir} ({time.monotonic()-t0:.0f}s)")

with open(snac_cfg_path) as f:
    snac_cfg_full = json.load(f)

SNAC_CONSTRUCTOR_KEYS = {
    "sampling_rate","encoder_dim","encoder_rates","latent_dim",
    "decoder_dim","decoder_rates","attn_window_size","codebook_size",
    "codebook_dim","vq_strides","noise","depthwise",
}
snac_cfg = {k: v for k, v in snac_cfg_full.items() if k in SNAC_CONSTRUCTOR_KEYS}
snac_cfg["attn_window_size"] = None

snac_model = SNAC(**snac_cfg).eval()

def _strip_attn(seq):
    return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != "LocalMHA"])

snac_model.encoder.block = _strip_attn(snac_model.encoder.block)
snac_model.decoder.model = _strip_attn(snac_model.decoder.model)

sd = torch.load(snac_wts_path, map_location="cpu", weights_only=False)
snac_model.load_state_dict(sd)
snac_model = snac_model.to(DEVICE).eval()
print(f"  SNAC loaded OK")

def _snac_decode_compat(self, codes):
    z_q = None
    for i, quantizer in enumerate(self.quantizer.quantizers):
        z_q_i = quantizer.decode_code(codes[i])
        z_q_i = quantizer.out_proj(z_q_i)
        if quantizer.stride > 1:
            z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)
        z_q = z_q_i if z_q is None else z_q + z_q_i
    return self.decoder(z_q)

snac_model.decode = _types.MethodType(_snac_decode_compat, snac_model)

_test_codes = [
    torch.zeros(1, 3, dtype=torch.long, device=DEVICE),
    torch.zeros(1, 6, dtype=torch.long, device=DEVICE),
    torch.zeros(1, 12, dtype=torch.long, device=DEVICE),
]
with torch.no_grad():
    _test_out = snac_model.decode(_test_codes)
print(f"  SNAC smoke-test: shape={tuple(_test_out.shape)} ({'OK' if _test_out.shape[-1] > 0 else 'FAIL'})")

_START_OF_HUMAN    = 128259
_END_OF_HUMAN      = 128260
_START_OF_AI       = 128261
_END_OF_AI         = 128262
_START_OF_SPEECH   = 128257
_END_OF_SPEECH     = 128258
_AUDIO_CODE_OFFSET = 128266
_CODEBOOK_SIZE     = 4096
_TOKENS_PER_FRAME  = 7
_SLIDING_WINDOW    = 21
_SAMPLES_PER_FRAME = 2048
_SPK_MAITRI        = tokenizer.convert_tokens_to_ids("<spk_maitri>")
print(f"  <spk_maitri>={_SPK_MAITRI}")

# =====================================================================
# SECTION 3: Generation functions — BROKEN baseline + FIXED with SpeakerLock
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 3: Generation functions")
print("=" * 70)

def build_input_ids(text: str, speaker: str = "maitri") -> torch.Tensor:
    prompt = f"<spk_{speaker}> {text}"
    ids = tokenizer.encode(prompt, add_special_tokens=False)
    full = [_START_OF_HUMAN, *ids, _END_OF_HUMAN, _START_OF_AI, _START_OF_SPEECH]
    return torch.tensor([full], device=DEVICE)

def generate_tokens(text: str, speaker: str = "maitri", seed=None) -> tuple:
    if seed is not None:
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    input_ids = build_input_ids(text, speaker)
    max_new = min(int(len(text) * 1.3) * _TOKENS_PER_FRAME + 21, 700)
    t0 = time.monotonic()
    with torch.no_grad():
        out = model.generate(
            input_ids,
            max_new_tokens=max_new,
            do_sample=True,
            temperature=0.4,
            top_p=0.9,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.pad_token_id or _END_OF_SPEECH,
            eos_token_id=[_END_OF_SPEECH, _END_OF_AI],
        )
    gen_ms = int((time.monotonic() - t0) * 1000)
    raw_tokens = out[0, input_ids.shape[1]:].tolist()
    return raw_tokens, gen_ms

# ── SpeakerLockProcessor ─────────────────────────────────────────────
# Ported from Sprint-29 Kavya fix. Pre-scales logits for first lock_tokens
# generated tokens by (base_temp / boot_temp), so generate()'s internal
# TemperatureLogitsWarper(0.4) produces effective temperature = boot_temp.
# This strongly biases early tokens toward the female cluster.
from transformers import LogitsProcessor, LogitsProcessorList

class _SpeakerLockProcessor(LogitsProcessor):
    def __init__(self, prompt_len: int, lock_tokens: int, boot_temp: float = 0.05, base_temp: float = 0.4):
        self.prompt_len  = prompt_len
        self.lock_tokens = int(lock_tokens)
        self.scale       = float(base_temp) / max(float(boot_temp), 1e-6)

    def __call__(self, input_ids, scores):
        gen_pos = input_ids.shape[1] - self.prompt_len
        if gen_pos < self.lock_tokens:
            scores = scores * self.scale
        return scores

def generate_tokens_fixed(
    text: str,
    speaker: str = "maitri",
    seed=None,
    lock_tokens: int = 21,
    boot_temp: float = 0.05,
) -> tuple:
    if seed is not None:
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    input_ids = build_input_ids(text, speaker)
    max_new = min(int(len(text) * 1.3) * _TOKENS_PER_FRAME + 21, 700)
    processors = LogitsProcessorList([
        _SpeakerLockProcessor(
            prompt_len=input_ids.shape[1],
            lock_tokens=lock_tokens,
            boot_temp=boot_temp,
            base_temp=0.4,
        )
    ])
    t0 = time.monotonic()
    with torch.no_grad():
        out = model.generate(
            input_ids,
            max_new_tokens=max_new,
            do_sample=True,
            temperature=0.4,
            top_p=0.9,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.pad_token_id or _END_OF_SPEECH,
            eos_token_id=[_END_OF_SPEECH, _END_OF_AI],
            logits_processor=processors,
        )
    gen_ms = int((time.monotonic() - t0) * 1000)
    raw_tokens = out[0, input_ids.shape[1]:].tolist()
    return raw_tokens, gen_ms

print("  generate_tokens() + generate_tokens_fixed() defined")
print(f"  SpeakerLockProcessor: scale at lock_tokens=21 = {0.4/0.05:.1f}x (effective_temp=0.05)")

# =====================================================================
# SECTION 4: SNAC decode (sliding-window, production method)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 4: SNAC decode")
print("=" * 70)

def extract_audio_tokens(raw_tokens):
    audio = []
    for t in raw_tokens:
        if _AUDIO_CODE_OFFSET <= t < _AUDIO_CODE_OFFSET + _TOKENS_PER_FRAME * _CODEBOOK_SIZE:
            audio.append(t)
        elif t in (_END_OF_SPEECH, _END_OF_AI):
            break
    return audio

def audio_tokens_to_codebook_vals(audio_tokens):
    vals = []
    for pos, t in enumerate(audio_tokens):
        frame_pos = pos % _TOKENS_PER_FRAME
        val = t - _AUDIO_CODE_OFFSET - frame_pos * _CODEBOOK_SIZE
        vals.append(max(0, min(val, _CODEBOOK_SIZE - 1)))
    return vals

def sliding_window_decode_pcm(audio_tokens):
    vals = audio_tokens_to_codebook_vals(audio_tokens)
    n = len(vals)
    chunks = []
    pos = 0
    while pos + _SLIDING_WINDOW <= n:
        window = vals[pos:pos + _SLIDING_WINDOW]
        n_frames = _SLIDING_WINDOW // _TOKENS_PER_FRAME
        codes_0, codes_1, codes_2 = [], [], []
        for i in range(n_frames):
            b = i * _TOKENS_PER_FRAME
            codes_0.append(window[b])
            codes_1.append(window[b+1])
            codes_2.append(window[b+2])
            codes_2.append(window[b+3])
            codes_1.append(window[b+4])
            codes_2.append(window[b+5])
            codes_2.append(window[b+6])
        def _clamp(v):
            return [min(max(x, 0), _CODEBOOK_SIZE-1) for x in v]
        codes = [
            torch.tensor(_clamp(codes_0), dtype=torch.long, device=DEVICE).unsqueeze(0),
            torch.tensor(_clamp(codes_1), dtype=torch.long, device=DEVICE).unsqueeze(0),
            torch.tensor(_clamp(codes_2), dtype=torch.long, device=DEVICE).unsqueeze(0),
        ]
        with torch.no_grad():
            audio_hat = snac_model.decode(codes)
        middle = audio_hat.squeeze()[_SAMPLES_PER_FRAME:_SAMPLES_PER_FRAME*2]
        pcm_chunk = (middle.cpu().float().clamp(-1.0, 1.0).numpy() * 32767).astype("int16")
        chunks.append(pcm_chunk.tobytes())
        pos += _TOKENS_PER_FRAME
    return b"".join(chunks)

print("  sliding_window_decode_pcm() defined")

# =====================================================================
# SECTION 5: F0 analysis helpers
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 5: F0 analysis helpers")
print("=" * 70)

OUT_DIR = "/kaggle/working/output"
WAV_DIR = os.path.join(OUT_DIR, "wavs_v4")
os.makedirs(WAV_DIR, exist_ok=True)

def save_wav(pcm_bytes, path, sample_rate=24000):
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return os.path.getsize(path)

def estimate_f0_profile(pcm24k, window_ms=40, hop_ms=20):
    if len(pcm24k) < 4:
        return []
    from scipy.signal import correlate
    sr = 24000
    samples = np.frombuffer(pcm24k, dtype=np.int16).astype(np.float32) / 32768.0
    win_samples = int(sr * window_ms / 1000)
    hop_samples = int(sr * hop_ms / 1000)
    min_lag = int(sr / 400)
    max_lag = int(sr / 60)
    profile = []
    n = len(samples)
    pos = 0
    while pos + win_samples <= n:
        frame = samples[pos:pos + win_samples]
        t_ms = (pos + win_samples // 2) / sr * 1000
        rms = float(np.sqrt(np.mean(frame ** 2)))
        energy_db = float(20 * np.log10(rms + 1e-10))
        voiced = energy_db > -40
        f0_hz = 0.0
        if voiced and len(frame) >= max_lag * 2:
            ac = correlate(frame, frame, mode='full')
            ac = ac[len(ac)//2:]
            ac = ac / (ac[0] + 1e-10)
            ac_range = ac[min_lag:max_lag]
            if len(ac_range) > 0:
                peak_idx = int(np.argmax(ac_range))
                peak_val = float(ac_range[peak_idx])
                lag = peak_idx + min_lag
                if peak_val > 0.3 and lag > 0:
                    f0_hz = float(sr / lag)
        profile.append({"t_ms": round(t_ms, 1), "f0_hz": round(f0_hz, 1), "voiced": voiced})
        pos += hop_samples
    return profile

def classify_gender(profile):
    voiced = [p for p in profile if p["voiced"] and p["f0_hz"] > 60]
    if not voiced:
        return {"pct_female": 0, "pct_male": 0, "median_f0": 0, "switch_detected": False}
    female = [p for p in voiced if p["f0_hz"] > 165]
    male   = [p for p in voiced if p["f0_hz"] < 140]
    labels = ["F" if p["f0_hz"] > 165 else "M" if p["f0_hz"] < 140 else "U" for p in voiced]
    label_str = "".join(labels)
    switch = "FMF" in label_str or ("FM" in label_str and "MF" in label_str)
    return {
        "pct_female": round(len(female) / len(voiced) * 100, 1),
        "pct_male":   round(len(male)   / len(voiced) * 100, 1),
        "median_f0":  round(float(np.median([p["f0_hz"] for p in voiced])), 1),
        "switch_detected": switch,
        "label_seq": label_str[:60],
    }

print("  F0 helpers defined")

# =====================================================================
# SECTION 6: Corpus — same texts as v3 P01-P06
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 6: Corpus (same texts as v3 forensic)")
print("=" * 70)

# v3 baseline drift rates (from forensic run):
V3_BASELINE = {
    "P01": 3,   # 3/30 = 10%
    "P02": 22,  # 22/30 = 73%
    # P03-P06: TBD at time of writing
}

CORPUS = [
    {"id": "P01", "text": "Namaste. Main Maitri baat kar rahi hun. Kya aap sunne mein saksham hain?"},
    {"id": "P02", "text": (
        "Namaste, Prateek Das ji. Main Maitri hun, Rajat Finance ki collections team se. "
        "Aapke account mein pachaas hazaar rupay ka outstanding balance hai jo tees July tak due tha. "
        "Kya aap aaj is baare mein baat kar sakte hain?"
    )},
    {"id": "P03", "text": (
        "Main samajh sakti hun ki yeh mushkil waqt hai. "
        "Lekin main aapko batana chahti hun ki hum aapke saath kaam karne ke liye taiyaar hain. "
        "Aap ek partial payment bhi kar sakte hain — jo bhi aap ke liye sambhav ho. "
        "Hum aapki situation ko samajhte hain aur ek flexible repayment plan bana sakte hain. "
        "Kya aap mujhe bata sakte hain ki aapke liye kya comfortable hoga?"
    )},
    {"id": "P04", "text": (
        "Aapke loan account number teen saat paanch do ka balance ek lakh pachaas hazaar rupay hai. "
        "Due date paanch September do hazaar chhabbees thi. "
        "Late payment penalty teen percent per month ke hisaab se calculate hoti hai."
    )},
    {"id": "P05", "text": (
        "Aapke saath seedha baat karna chahti hun — aapka account abhi severe overdue category mein hai. "
        "Hamari records ke mutabiq, aapne pichhle teen mahine mein koi payment nahi ki hai. "
        "Yeh aapke credit score ko bahut buri tarah affect kar sakta hai. "
        "Main chahti hun ki aap yeh clearly samjhein: agar hum abhi koi resolution nahi nikaalte, "
        "toh humein legal recovery process shuru karni padegi. "
        "Lekin main yeh nahi chahti. Main aapko ek mouka dena chahti hun. "
        "Kya aap abhi phone pe mujhe bata sakte hain ki aap kitni payment kar sakte hain? "
        "Koi bhi amount helpful hogi — chahe woh teen hazaar rupay hi kyon na ho."
    )},
    {"id": "P06", "text": (
        "Aapka karz chukana bahut zaroori hai. "
        "Humara collection department aapke case ko priority de raha hai. "
        "Please ek baar sochiyen aur mujhe call karein. "
        "Main Maitri hun aur main hamesha aapki help ke liye available hun."
    )},
]

for c in CORPUS:
    max_new = min(int(len(c["text"]) * 1.3) * _TOKENS_PER_FRAME + 21, 700)
    baseline = V3_BASELINE.get(c["id"], "?")
    print(f"  {c['id']}: {len(c['text'])} chars | max_new={max_new} | v3_baseline={baseline}/30")

# =====================================================================
# SECTION 7: SpeakerLock sweep — 4 lock_token values × 6 texts × 30 runs
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 7: SpeakerLock fix sweep")
print("=" * 70)

# lock_tokens values to test: 7=1 frame, 21=3 frames, 35=5 frames, 49=7 frames
LOCK_SWEEP = [7, 21, 35, 49]
N_RUNS = 30

# Results: lock_tokens -> text_id -> {n_switch, runs}
FIXED_RESULTS = {}

for lock_n in LOCK_SWEEP:
    FIXED_RESULTS[lock_n] = {}
    print(f"\n{'='*60}")
    print(f"LOCK_TOKENS = {lock_n}  (boot_temp=0.05, base_temp=0.4, effective_temp={0.4/0.05:.2f}x sharper)")
    print(f"{'='*60}")

    for corpus_item in CORPUS:
        cid = corpus_item["id"]
        text = corpus_item["text"]
        print(f"\n  TEXT: {cid} (lock_n={lock_n})")

        runs = []
        drift_runs = []

        for run_idx in range(N_RUNS):
            raw_tokens, gen_ms = generate_tokens_fixed(
                text, speaker="maitri", seed=None,
                lock_tokens=lock_n, boot_temp=0.05,
            )
            audio_tokens = extract_audio_tokens(raw_tokens)
            n_audio = len(audio_tokens)

            if n_audio < _SLIDING_WINDOW:
                print(f"    Run {run_idx:02d}: SKIP ({n_audio} audio tok)")
                continue

            pcm24k = sliding_window_decode_pcm(audio_tokens)
            profile = estimate_f0_profile(pcm24k)
            gender = classify_gender(profile)

            run_result = {
                "run": run_idx,
                "n_audio": n_audio,
                "gen_ms": gen_ms,
                **gender,
            }
            runs.append(run_result)

            switch_marker = " *** DRIFT ***" if gender["switch_detected"] else ""
            print(f"    Run {run_idx:02d}: {n_audio} tok | {gen_ms}ms | "
                  f"F0={gender['median_f0']}Hz | "
                  f"F%={gender['pct_female']:.0f} M%={gender['pct_male']:.0f}"
                  f"{switch_marker}")

            if gender["switch_detected"] or gender["pct_male"] > 20:
                wav_path = os.path.join(WAV_DIR, f"{cid}_lock{lock_n}_run{run_idx:02d}_DRIFT.wav")
                save_wav(pcm24k, wav_path)
                drift_runs.append(run_result)
                print(f"         SAVED: {wav_path}")

        n_switch = sum(1 for r in runs if r["switch_detected"])
        max_male = max((r["pct_male"] for r in runs), default=0)
        print(f"\n  SUMMARY {cid} lock={lock_n}: {n_switch}/{len(runs)} switches | max_male%={max_male:.1f}")

        baseline = V3_BASELINE.get(cid, None)
        if baseline is not None:
            reduction = baseline - n_switch
            print(f"  vs v3_baseline: {baseline}/30 -> {n_switch}/30  (reduced by {reduction})")

        FIXED_RESULTS[lock_n][cid] = {
            "n_runs": len(runs),
            "n_switch": n_switch,
            "max_male_pct": round(max_male, 1),
            "v3_baseline": baseline,
        }

    # Early exit if this lock_n already eliminates all drift
    all_texts_clean = all(
        v["n_switch"] == 0 for v in FIXED_RESULTS[lock_n].values()
    )
    if all_texts_clean:
        print(f"\n*** ALL TEXTS CLEAN AT lock_tokens={lock_n} — STOPPING SWEEP ***")
        break

# =====================================================================
# SECTION 8: Final comparison table
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 8: FINAL COMPARISON TABLE")
print("=" * 70)
print(f"\n{'Text':<8} | {'v3 broken':>10} | " + " | ".join(f"{'lock='+str(n):>10}" for n in LOCK_SWEEP if n in FIXED_RESULTS))
print("-" * 80)

for c in CORPUS:
    cid = c["id"]
    baseline = V3_BASELINE.get(cid, "?")
    row = f"{cid:<8} | {str(baseline)+'/30':>10}"
    for lock_n in LOCK_SWEEP:
        if lock_n not in FIXED_RESULTS:
            break
        result = FIXED_RESULTS[lock_n].get(cid, {})
        n_sw = result.get("n_switch", "?")
        row += f" | {str(n_sw)+'/30':>10}"
    print(row)

print("\n")

# Find optimal lock_tokens (first that gives 0 drift on all texts)
optimal = None
for lock_n in LOCK_SWEEP:
    if lock_n not in FIXED_RESULTS:
        break
    if all(v["n_switch"] == 0 for v in FIXED_RESULTS[lock_n].values()):
        optimal = lock_n
        break

if optimal:
    print(f"VERDICT: lock_tokens={optimal} ELIMINATES ALL MALE DRIFT")
    print(f"FIX: Add SpeakerLockProcessor(lock_tokens={optimal}, boot_temp=0.05) to Maitri generate call")
else:
    best = min(LOCK_SWEEP, key=lambda n: sum(
        v.get("n_switch", 999) for v in FIXED_RESULTS.get(n, {}).values()
    ) if n in FIXED_RESULTS else 999)
    total_best = sum(v.get("n_switch", 0) for v in FIXED_RESULTS.get(best, {}).values())
    total_broken = sum(V3_BASELINE.values())
    print(f"VERDICT: Best tested is lock_tokens={best} | total drift {total_broken}/180 -> {total_best}/180")
    print(f"RECOMMENDATION: Try higher lock_tokens or lower boot_temp in next iteration")

# Save full results
results_path = os.path.join(OUT_DIR, "v4_speaker_lock_results.json")
with open(results_path, "w") as f:
    json.dump(FIXED_RESULTS, f, indent=2)
print(f"\nResults saved: {results_path}")
print("\n" + "=" * 70)
print("v4 KERNEL COMPLETE")
print("=" * 70)
