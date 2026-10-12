# =====================================================================
# MAITRI VOICE SWITCH CAUSAL ANALYSIS KERNEL v6
# =====================================================================
# Goal: Find the EXACT causal token pattern responsible for male drift.
# NOT a blacklist. NOT a temp fix. CAUSAL ANALYSIS ONLY.
#
# Method:
#   1. Generate 60 unseeded runs on P02 (production greeting — highest drift)
#   2. For each run, store: full raw Veena token seq + full audio token seq
#   3. F0-profile each output at per-SNAC-frame resolution (85ms/frame)
#   4. Classify each run as CLEAN or DRIFT
#   5. For DRIFT runs: find first frame where F0 < 140 Hz (male onset frame)
#   6. At that frame position:
#       a. Extract the 7 audio tokens of that frame
#       b. Extract ±3 frame context (21-token window each side)
#       c. For each token position 0-6 within the frame:
#          - P(token_id | drift) vs P(token_id | clean at same position)
#   7. Find EARLIEST position where drift and clean diverge reliably
#   8. Check: does any specific token appear ONLY in drift, NEVER in clean?
#   9. Check: is the divergence a sequence pattern (frame N and N+1) vs single token?
#  10. Also run P01 (low drift, 3/30) as a control comparison
#
# Output: CAUSAL_ANALYSIS_REPORT.json + per-run token files
# NO PRODUCTION MODIFICATIONS. NO BLACKLIST.
# =====================================================================
import sys, os, json, time, struct, wave, collections
print("=" * 70)
print("MAITRI VOICE SWITCH CAUSAL ANALYSIS KERNEL v6")
print("=" * 70)
print(f"Python: {sys.version}")
print(f"Time:   {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

import subprocess, torch, numpy as np

r = subprocess.run("nvidia-smi", shell=True, capture_output=True, text=True)
print(r.stdout[:500])

def pip(pkg, timeout=600):
    t0 = time.monotonic()
    r = subprocess.run(f"{sys.executable} -m pip install --upgrade {pkg} -q 2>&1",
        shell=True, capture_output=True, text=True, timeout=timeout)
    print(f"  {'OK' if r.returncode==0 else 'FAIL'}: {pkg} ({time.monotonic()-t0:.0f}s)")

for pkg in ["transformers==5.12.1","tokenizers==0.22.2","accelerate==1.7.0","snac==1.0.0"]:
    pip(pkg)

snac_site = subprocess.run(
    f"{sys.executable} -c 'import snac; import os; print(os.path.dirname(os.path.dirname(snac.__file__)))'",
    shell=True, capture_output=True, text=True).stdout.strip()
if snac_site and snac_site not in sys.path:
    sys.path.insert(0, snac_site)

from transformers import AutoTokenizer, AutoModelForCausalLM
from huggingface_hub import snapshot_download
from snac import SNAC
import types as _types

VEENA_REV = "8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f"
SNAC_REV  = "d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec"
HF_HOME   = "/kaggle/working/hf"
os.makedirs(HF_HOME, exist_ok=True)
os.environ["HF_HOME"] = HF_HOME
DEVICE = "cuda:0"

print("\nLoading Veena...")
veena_path = snapshot_download("maya-research/Veena", revision=VEENA_REV, cache_dir=HF_HOME,
    ignore_patterns=["*.msgpack","*.h5","flax_model*","tf_model*"])
tokenizer = AutoTokenizer.from_pretrained(veena_path)
model = AutoModelForCausalLM.from_pretrained(veena_path, torch_dtype=torch.bfloat16,
    attn_implementation="sdpa", device_map="cuda:0", low_cpu_mem_usage=True).eval()
print(f"  Veena loaded | {sum(p.numel() for p in model.parameters())//1e6:.0f}M params")

print("\nLoading SNAC...")
snac_dir = snapshot_download("hubertsiuzdak/snac_24khz", revision=SNAC_REV, cache_dir=HF_HOME)
with open(os.path.join(snac_dir, "config.json")) as f:
    snac_cfg_full = json.load(f)
SNAC_KEYS = {"sampling_rate","encoder_dim","encoder_rates","latent_dim","decoder_dim",
    "decoder_rates","attn_window_size","codebook_size","codebook_dim","vq_strides","noise","depthwise"}
snac_cfg = {k: v for k, v in snac_cfg_full.items() if k in SNAC_KEYS}
snac_cfg["attn_window_size"] = None
snac_model = SNAC(**snac_cfg).eval()
def _strip(seq):
    return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != "LocalMHA"])
snac_model.encoder.block = _strip(snac_model.encoder.block)
snac_model.decoder.model = _strip(snac_model.decoder.model)
sd = torch.load(os.path.join(snac_dir, "pytorch_model.bin"), map_location="cpu", weights_only=False)
snac_model.load_state_dict(sd)
snac_model = snac_model.to(DEVICE).eval()
def _snac_decode(self, codes):
    z_q = None
    for i, q in enumerate(self.quantizer.quantizers):
        z = q.decode_code(codes[i])
        z = q.out_proj(z)
        if q.stride > 1:
            z = z.repeat_interleave(q.stride, dim=-1)
        z_q = z if z_q is None else z_q + z
    return self.decoder(z_q)
snac_model.decode = _types.MethodType(_snac_decode, snac_model)
print("  SNAC loaded OK")

_SOH=128259; _EOH=128260; _SOA=128261; _EOA=128262
_SOS=128257; _EOS=128258; _ACO=128266; _CBS=4096; _TPF=7; _SW=21; _SPF=2048

def build_ids(text):
    ids = tokenizer.encode(f"<spk_maitri> {text}", add_special_tokens=False)
    return torch.tensor([[_SOH, *ids, _EOH, _SOA, _SOS]], device=DEVICE)

def generate_raw(text):
    inp = build_ids(text)
    max_new = min(int(len(text)*1.3)*_TPF+21, 700)
    t0 = time.monotonic()
    with torch.no_grad():
        out = model.generate(inp, max_new_tokens=max_new, do_sample=True, temperature=0.4,
            top_p=0.9, repetition_penalty=1.05,
            pad_token_id=tokenizer.pad_token_id or _EOS, eos_token_id=[_EOS, _EOA])
    gen_ms = int((time.monotonic()-t0)*1000)
    raw = out[0, inp.shape[1]:].tolist()
    return raw, gen_ms, inp.shape[1]

def extract_audio(raw):
    audio = []
    for t in raw:
        if _ACO <= t < _ACO + _TPF*_CBS:
            audio.append(t)
        elif t in (_EOS, _EOA):
            break
    return audio

def to_vals(audio):
    return [max(0, min(t - _ACO - (i%_TPF)*_CBS, _CBS-1)) for i, t in enumerate(audio)]

def decode_frames(audio):
    """Decode each SNAC frame independently. Returns list of (frame_idx, pcm_bytes)."""
    vals = to_vals(audio)
    n_frames = len(vals) // _TPF
    results = []
    for fi in range(n_frames):
        b = fi * _TPF
        w = vals[b:b+_TPF]
        c0=[w[0]]; c1=[w[1],w[4]]; c2=[w[2],w[3],w[5],w[6]]
        def cl(v): return [min(max(x,0),_CBS-1) for x in v]
        codes=[torch.tensor(cl(c0),dtype=torch.long,device=DEVICE).unsqueeze(0),
               torch.tensor(cl(c1),dtype=torch.long,device=DEVICE).unsqueeze(0),
               torch.tensor(cl(c2),dtype=torch.long,device=DEVICE).unsqueeze(0)]
        with torch.no_grad():
            ah = snac_model.decode(codes)
        pcm = (ah.squeeze().cpu().float().clamp(-1,1).numpy()*32767).astype("int16").tobytes()
        results.append((fi, pcm))
    return results

def f0_from_pcm(pcm_bytes, sr=24000):
    """Estimate F0 from a PCM chunk. Returns Hz or 0 if unvoiced."""
    from scipy.signal import correlate
    if len(pcm_bytes) < 4:
        return 0.0
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples**2)))
    if rms < 0.002:
        return 0.0
    mlag = int(sr/400); xlag = int(sr/60)
    if len(samples) < xlag*2:
        return 0.0
    ac = np.correlate(samples, samples, mode='full')
    ac = ac[len(ac)//2:]
    ac = ac / (ac[0]+1e-10)
    ar = ac[mlag:xlag]
    if len(ar) == 0:
        return 0.0
    pi = int(np.argmax(ar)); pv = float(ar[pi]); lag = pi+mlag
    if pv > 0.25 and lag > 0:
        return float(sr/lag)
    return 0.0

def sw_decode_full(audio):
    """Production sliding-window decode → full PCM."""
    vals = to_vals(audio)
    chunks = []
    pos = 0
    while pos + _SW <= len(vals):
        w = vals[pos:pos+_SW]
        c0,c1,c2=[],[],[]
        for i in range(_SW//_TPF):
            b=i*_TPF; c0.append(w[b]); c1+=[w[b+1],w[b+4]]; c2+=[w[b+2],w[b+3],w[b+5],w[b+6]]
        def cl(v): return [min(max(x,0),_CBS-1) for x in v]
        codes=[torch.tensor(cl(c0),dtype=torch.long,device=DEVICE).unsqueeze(0),
               torch.tensor(cl(c1),dtype=torch.long,device=DEVICE).unsqueeze(0),
               torch.tensor(cl(c2),dtype=torch.long,device=DEVICE).unsqueeze(0)]
        with torch.no_grad(): ah=snac_model.decode(codes)
        mid=ah.squeeze()[_SPF:_SPF*2]
        chunks.append((mid.cpu().float().clamp(-1,1).numpy()*32767).astype("int16").tobytes())
        pos+=_TPF
    return b"".join(chunks)

def save_wav(pcm, path, sr=24000):
    with wave.open(path,"wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(pcm)

OUT = "/kaggle/working/output"
os.makedirs(OUT, exist_ok=True)

# =====================================================================
# SECTION A: Generate 60 runs on P02, save full token sequences
# =====================================================================
print("\n" + "="*70)
print("SECTION A: 60-run generation sweep on P02 (production greeting)")
print("="*70)
print("Saving full token sequences + per-frame F0 for each run.")
print()

P02_TEXT = (
    "Namaste, Prateek Das ji. Main Maitri hun, Rajat Finance ki collections team se. "
    "Aapke account mein pachaas hazaar rupay ka outstanding balance hai jo tees July tak due tha. "
    "Kya aap aaj is baare mein baat kar sakte hain?"
)
P01_TEXT = "Namaste. Main Maitri baat kar rahi hun. Kya aap sunne mein saksham hain?"

N_RUNS = 60
all_runs = []  # list of dicts with full data

for run_i in range(N_RUNS):
    raw, gen_ms, prompt_len = generate_raw(P02_TEXT)
    audio = extract_audio(raw)
    n_audio = len(audio)

    if n_audio < _TPF:
        print(f"  Run {run_i:02d}: SKIP (only {n_audio} audio tokens)")
        continue

    n_frames = n_audio // _TPF

    # Per-frame F0: decode each frame independently for clean F0 attribution
    frame_f0s = []
    frame_decoded = decode_frames(audio[:n_frames*_TPF])
    for fi, pcm_bytes in frame_decoded:
        f0 = f0_from_pcm(pcm_bytes)
        frame_f0s.append({"frame": fi, "f0_hz": round(f0,1),
                          "gender": "F" if f0>165 else "M" if 60<f0<140 else "U" if f0>=140 else "S"})

    # Overall classification
    voiced = [f for f in frame_f0s if f["gender"] in ("F","M","U")]
    male_frames = [f for f in voiced if f["gender"] == "M"]
    female_frames = [f for f in voiced if f["gender"] == "F"]

    pct_male = round(len(male_frames)/max(len(voiced),1)*100, 1)
    pct_female = round(len(female_frames)/max(len(voiced),1)*100, 1)

    # Find first male-onset frame
    first_male_frame = None
    for f in frame_f0s:
        if f["gender"] == "M":
            first_male_frame = f["frame"]
            break

    is_drift = len(male_frames) > 0 and first_male_frame is not None

    # Production sliding-window PCM for listening
    pcm_full = sw_decode_full(audio)
    wav_label = "DRIFT" if is_drift else "CLEAN"
    wav_path = os.path.join(OUT, f"P02_run{run_i:02d}_{wav_label}.wav")
    save_wav(pcm_full, wav_path)

    run_data = {
        "run": run_i,
        "is_drift": is_drift,
        "pct_male": pct_male,
        "pct_female": pct_female,
        "first_male_frame": first_male_frame,
        "n_frames": n_frames,
        "gen_ms": gen_ms,
        "audio_tokens": audio,          # full audio token sequence
        "raw_tokens": raw,              # full veena token sequence
        "frame_f0s": frame_f0s,         # per-frame F0
        "wav_path": wav_path,
    }
    all_runs.append(run_data)

    print(f"  Run {run_i:02d}: {n_audio} tok | {n_frames} frames | "
          f"F%={pct_female:.0f} M%={pct_male:.0f} | "
          f"first_male_frame={first_male_frame} | {wav_label}")

drift_runs  = [r for r in all_runs if r["is_drift"]]
clean_runs  = [r for r in all_runs if not r["is_drift"]]
print(f"\nTotal: {len(all_runs)} runs | {len(drift_runs)} DRIFT | {len(clean_runs)} CLEAN")

# =====================================================================
# SECTION B: Frame-level causal analysis
# =====================================================================
print("\n" + "="*70)
print("SECTION B: Frame-level causal analysis")
print("="*70)
print(f"Comparing {len(drift_runs)} drift runs vs {len(clean_runs)} clean runs")
print("Question: At each frame position, do drift runs use different tokens?")
print()

if not drift_runs:
    print("  No drift runs found — cannot do causal analysis. Increase N_RUNS.")
else:
    # --- B1: Distribution of first_male_frame ---
    first_male_frames = [r["first_male_frame"] for r in drift_runs]
    print("B1: Distribution of first male-onset frame position:")
    frame_counts = collections.Counter(first_male_frames)
    for frame_pos, cnt in sorted(frame_counts.items()):
        t_ms = round(frame_pos * _SPF / 24000 * 1000)
        print(f"   frame {frame_pos:3d} ({t_ms:4d}ms): {cnt} drift runs")
    median_onset = int(np.median(first_male_frames))
    print(f"   Median onset: frame {median_onset} ({round(median_onset*_SPF/24000*1000)}ms)")

    # --- B2: Token comparison at onset frame and preceding frames ---
    print(f"\nB2: Token-level comparison at onset frame ± 3 frames")
    print("    For each token position within the SNAC 7-token frame:")
    print("    Checking if drift runs use different token IDs than clean runs")
    print()

    ANALYSIS_WINDOW = range(max(0, median_onset-5), median_onset+3)
    report_frames = {}

    for fi in ANALYSIS_WINDOW:
        drift_tok_at_frame = []  # list of 7-token lists
        clean_tok_at_frame = []

        for r in drift_runs:
            start = fi * _TPF
            end = start + _TPF
            if end <= len(r["audio_tokens"]):
                drift_tok_at_frame.append(r["audio_tokens"][start:end])

        for r in clean_runs:
            start = fi * _TPF
            end = start + _TPF
            if end <= len(r["audio_tokens"]):
                clean_tok_at_frame.append(r["audio_tokens"][start:end])

        if not drift_tok_at_frame or not clean_tok_at_frame:
            continue

        # For each of 7 positions in the frame, compare token ID distributions
        frame_report = {"frame": fi, "positions": []}
        t_ms = round(fi * _SPF / 24000 * 1000)
        is_onset = fi == median_onset
        onset_marker = " <<< ONSET FRAME" if is_onset else ""
        print(f"   Frame {fi} ({t_ms}ms){onset_marker}:")

        for pos_in_frame in range(_TPF):
            drift_ids = [toks[pos_in_frame] for toks in drift_tok_at_frame if len(toks)>pos_in_frame]
            clean_ids = [toks[pos_in_frame] for toks in clean_tok_at_frame if len(toks)>pos_in_frame]

            drift_counter = collections.Counter(drift_ids)
            clean_counter = collections.Counter(clean_ids)

            drift_only = set(drift_counter.keys()) - set(clean_counter.keys())
            clean_only = set(clean_counter.keys()) - set(drift_counter.keys())
            shared = set(drift_counter.keys()) & set(clean_counter.keys())

            # Top token in drift runs
            top_drift_tok, top_drift_cnt = drift_counter.most_common(1)[0] if drift_counter else (None, 0)
            top_clean_tok, top_clean_cnt = clean_counter.most_common(1)[0] if clean_counter else (None, 0)

            drift_only_list = sorted(drift_only)[:5]
            pos_report = {
                "pos_in_frame": pos_in_frame,
                "n_drift_runs": len(drift_ids),
                "n_clean_runs": len(clean_ids),
                "drift_only_tokens": drift_only_list,
                "clean_only_count": len(clean_only),
                "shared_count": len(shared),
                "top_drift": top_drift_tok,
                "top_drift_pct": round(top_drift_cnt/max(len(drift_ids),1)*100,1),
                "top_clean": top_clean_tok,
                "top_clean_pct": round(top_clean_cnt/max(len(clean_ids),1)*100,1),
            }
            frame_report["positions"].append(pos_report)

            # Print only if there's meaningful divergence
            if drift_only or (top_drift_tok != top_clean_tok and top_drift_cnt > 1):
                print(f"     pos[{pos_in_frame}]: drift_top={top_drift_tok}({top_drift_cnt/max(len(drift_ids),1)*100:.0f}%) "
                      f"clean_top={top_clean_tok}({top_clean_cnt/max(len(clean_ids),1)*100:.0f}%) "
                      f"| drift_only_tokens={drift_only_list}")
            else:
                print(f"     pos[{pos_in_frame}]: SHARED top={top_drift_tok}  (no divergence)")

        report_frames[fi] = frame_report

    # --- B3: Pre-onset frames — is there an earlier divergence? ---
    print(f"\nB3: Checking for pre-onset divergence (frames before {median_onset})")
    print("    Looking for any frame where drift and clean use systematically different tokens")

    pre_onset_divergences = []
    for fi in range(0, median_onset):
        drift_toks_flat = []
        clean_toks_flat = []
        for r in drift_runs:
            start = fi * _TPF
            end = start + _TPF
            if end <= len(r["audio_tokens"]):
                drift_toks_flat.extend(r["audio_tokens"][start:end])
        for r in clean_runs:
            start = fi * _TPF
            end = start + _TPF
            if end <= len(r["audio_tokens"]):
                clean_toks_flat.extend(r["audio_tokens"][start:end])

        if not drift_toks_flat or not clean_toks_flat:
            continue

        drift_set = set(drift_toks_flat)
        clean_set = set(clean_toks_flat)
        drift_exclusive = drift_set - clean_set
        clean_exclusive = clean_set - drift_set

        if len(drift_exclusive) > 0:
            t_ms = round(fi * _SPF / 24000 * 1000)
            pre_onset_divergences.append({
                "frame": fi, "t_ms": t_ms,
                "drift_exclusive_count": len(drift_exclusive),
                "drift_exclusive_sample": sorted(drift_exclusive)[:10],
            })
            print(f"   Frame {fi} ({t_ms}ms): {len(drift_exclusive)} drift-exclusive tokens: {sorted(drift_exclusive)[:5]}")

    if not pre_onset_divergences:
        print("   No pre-onset frame divergence found — drift appears at onset frame, not earlier")
        print("   IMPLICATION: The male trajectory starts at the onset frame, not accumulated earlier")

    # --- B4: Check if "drift-exclusive" tokens at onset also appear in clean runs elsewhere ---
    print(f"\nB4: Cross-run token presence check")
    print("    For each token that appears in drift-onset frame but NOT clean-onset frame:")
    print("    Does it appear ANYWHERE in clean runs (at any frame)?")

    onset_fi = median_onset
    drift_onset_ids = set()
    clean_onset_ids = set()
    for r in drift_runs:
        start = onset_fi * _TPF
        if start + _TPF <= len(r["audio_tokens"]):
            drift_onset_ids.update(r["audio_tokens"][start:start+_TPF])
    for r in clean_runs:
        start = onset_fi * _TPF
        if start + _TPF <= len(r["audio_tokens"]):
            clean_onset_ids.update(r["audio_tokens"][start:start+_TPF])

    onset_drift_exclusive = drift_onset_ids - clean_onset_ids

    clean_all_tokens = set()
    for r in clean_runs:
        clean_all_tokens.update(r["audio_tokens"])

    truly_male_exclusive = onset_drift_exclusive - clean_all_tokens
    appears_in_clean_elsewhere = onset_drift_exclusive & clean_all_tokens

    print(f"\n   Tokens at onset frame in drift but NOT clean-onset: {len(onset_drift_exclusive)}")
    print(f"   Of those, NEVER in any clean run: {len(truly_male_exclusive)}")
    print(f"   Of those, appear elsewhere in clean runs: {len(appears_in_clean_elsewhere)}")
    if truly_male_exclusive:
        print(f"   Truly male-exclusive token IDs: {sorted(truly_male_exclusive)[:20]}")
        print(f"   -> These are CANDIDATES for blacklist (but context verification needed)")
    if appears_in_clean_elsewhere:
        print(f"   Appear-in-clean-elsewhere sample: {sorted(appears_in_clean_elsewhere)[:10]}")
        print(f"   -> These tokens are NOT male-exclusive — context is key")

    # --- B5: Sequence pattern check — does frame N predict frame N+1? ---
    print(f"\nB5: Sequence pattern — does onset frame N predict frame N+1 (drift persistence)?")
    for r in drift_runs[:5]:
        fi = r["first_male_frame"]
        frames_after = [f for f in r["frame_f0s"] if f["frame"] >= fi]
        genders = "".join(f["gender"] for f in frames_after[:8])
        print(f"   Run {r['run']:02d}: onset=frame{fi} | next 8 frames: {genders}")

    # --- B6: Veena raw token comparison at prompt boundary ---
    print(f"\nB6: Raw Veena token comparison at first generated position")
    print("    Comparing first 14 raw tokens (2 SNAC frames) between drift and clean runs")
    for r in drift_runs[:3]:
        print(f"   DRIFT run {r['run']:02d}: {r['raw_tokens'][:14]}")
    for r in clean_runs[:3]:
        print(f"   CLEAN run {r['run']:02d}: {r['raw_tokens'][:14]}")

# =====================================================================
# SECTION C: Same analysis on P01 (control — low drift rate)
# =====================================================================
print("\n" + "="*70)
print("SECTION C: Control analysis on P01 (short text, 10% drift rate)")
print("="*70)

P01_RUNS = 40
p01_runs = []

for run_i in range(P01_RUNS):
    raw, gen_ms, _ = generate_raw(P01_TEXT)
    audio = extract_audio(raw)
    n_frames = len(audio) // _TPF
    if n_frames == 0:
        continue
    frame_decoded = decode_frames(audio[:n_frames*_TPF])
    frame_f0s = []
    for fi, pcm_bytes in frame_decoded:
        f0 = f0_from_pcm(pcm_bytes)
        frame_f0s.append({"frame": fi, "f0_hz": round(f0,1),
                          "gender": "F" if f0>165 else "M" if 60<f0<140 else "U" if f0>=140 else "S"})
    voiced = [f for f in frame_f0s if f["gender"] in ("F","M","U")]
    male_frames = [f for f in voiced if f["gender"]=="M"]
    first_male = next((f["frame"] for f in frame_f0s if f["gender"]=="M"), None)
    is_drift = len(male_frames) > 0
    p01_runs.append({"run": run_i, "is_drift": is_drift, "first_male_frame": first_male,
                     "audio_tokens": audio, "frame_f0s": frame_f0s})
    label = "DRIFT" if is_drift else "CLEAN"
    print(f"  P01 Run {run_i:02d}: {len(audio)} tok | M_frames={len(male_frames)} | {label}")

p01_drift = [r for r in p01_runs if r["is_drift"]]
p01_clean = [r for r in p01_runs if not r["is_drift"]]
print(f"\nP01 total: {len(p01_runs)} | drift={len(p01_drift)} | clean={len(p01_clean)}")

# =====================================================================
# SECTION D: Final Report
# =====================================================================
print("\n" + "="*70)
print("SECTION D: CAUSAL ANALYSIS REPORT")
print("="*70)

report = {
    "P02": {
        "n_runs": len(all_runs),
        "n_drift": len(drift_runs),
        "n_clean": len(clean_runs),
        "drift_rate_pct": round(len(drift_runs)/max(len(all_runs),1)*100, 1),
        "onset_frame_distribution": dict(collections.Counter(
            r["first_male_frame"] for r in drift_runs
        )),
        "median_onset_frame": int(np.median([r["first_male_frame"] for r in drift_runs])) if drift_runs else None,
        "median_onset_ms": round(int(np.median([r["first_male_frame"] for r in drift_runs]))*_SPF/24000*1000) if drift_runs else None,
        "truly_male_exclusive_tokens": sorted(truly_male_exclusive) if drift_runs else [],
        "tokens_appearing_in_clean_elsewhere": len(appears_in_clean_elsewhere) if drift_runs else 0,
        "pre_onset_divergences": pre_onset_divergences if drift_runs else [],
        "verdict": "",
    },
    "P01_control": {
        "n_runs": len(p01_runs),
        "n_drift": len(p01_drift),
        "n_clean": len(p01_clean),
        "drift_rate_pct": round(len(p01_drift)/max(len(p01_runs),1)*100, 1),
    },
}

# Verdict
if drift_runs:
    n_truly_exclusive = len(truly_male_exclusive)
    n_context_dependent = len(appears_in_clean_elsewhere)
    if n_truly_exclusive > 0 and n_context_dependent == 0:
        verdict = f"SIMPLE_BLACKLIST_VIABLE: {n_truly_exclusive} tokens are truly male-exclusive"
    elif n_context_dependent > n_truly_exclusive:
        verdict = f"CONTEXT_DEPENDENT: Most onset tokens ({n_context_dependent}) appear in clean runs elsewhere — drift is positional/sequential, not token-identity based. Fix must be context-aware."
    else:
        verdict = f"MIXED: {n_truly_exclusive} exclusive + {n_context_dependent} context-dependent tokens"
    report["P02"]["verdict"] = verdict
    print(f"\nVERDICT: {verdict}")
    print(f"Onset: frame {report['P02']['median_onset_frame']} = {report['P02']['median_onset_ms']}ms into utterance")
    print(f"Drift rate: {report['P02']['drift_rate_pct']}% ({len(drift_runs)}/{len(all_runs)})")

with open(os.path.join(OUT, "CAUSAL_ANALYSIS_REPORT.json"), "w") as f:
    # Don't serialize token sequences in main report (too large)
    report_clean = json.loads(json.dumps(report))
    json.dump(report_clean, f, indent=2)
print(f"\nReport saved: {OUT}/CAUSAL_ANALYSIS_REPORT.json")

# Save per-run data (token sequences) separately
per_run_out = []
for r in all_runs:
    per_run_out.append({
        "run": r["run"], "is_drift": r["is_drift"],
        "first_male_frame": r["first_male_frame"],
        "audio_tokens": r["audio_tokens"],
        "frame_f0s": r["frame_f0s"],
    })
with open(os.path.join(OUT, "P02_all_runs_tokens.json"), "w") as f:
    json.dump(per_run_out, f)
print(f"Token data saved: {OUT}/P02_all_runs_tokens.json")
print("\n" + "="*70)
print("v6 CAUSAL ANALYSIS KERNEL COMPLETE")
print("="*70)
