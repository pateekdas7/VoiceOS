# =====================================================================
# MAITRI VOICE SWITCH FIX KERNEL v5
# =====================================================================
# v4 showed SpeakerLockProcessor alone was insufficient.
# v5 adds TWO-LAYER defense:
#   Layer 1: SpeakerLockProcessor (lock first N tokens to female temp)
#   Layer 2: MaleClusterBlocker — hard logit_bias=-100 on tokens that
#             appear ONLY in male-voiced frames in a calibration sweep.
#
# Approach:
#   1. Run 50 calibration generations on P02 (highest drift text)
#   2. For each run, F0-classify each frame as male/female
#   3. Collect audio token IDs from male vs female frames separately
#   4. male_only_tokens = tokens that appear in male frames but NOT female
#   5. Add logit_bias = {token_id: -100} for all male_only_tokens
#   6. Verify on P01-P06 (30 runs each) — should be 0/30 drift
# =====================================================================
import sys, os, json, time, struct, wave
print("=" * 70)
print("MAITRI VOICE SWITCH FIX KERNEL v5")
print("=" * 70)
print(f"Python: {sys.version}")
print(f"Time:   {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

import subprocess, torch, numpy as np

r = subprocess.run("nvidia-smi", shell=True, capture_output=True, text=True)
print(r.stdout[:600])

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

from transformers import AutoTokenizer, AutoModelForCausalLM, LogitsProcessor, LogitsProcessorList
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
print(f"  Model loaded | params={sum(p.numel() for p in model.parameters())//1e6:.0f}M")

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

_SOH = 128259; _EOH = 128260; _SOA = 128261; _EOA = 128262
_SOS = 128257; _EOS = 128258; _ACO = 128266; _CBS = 4096; _TPF = 7; _SW = 21; _SPF = 2048

def build_ids(text):
    ids = tokenizer.encode(f"<spk_maitri> {text}", add_special_tokens=False)
    return torch.tensor([[_SOH, *ids, _EOH, _SOA, _SOS]], device=DEVICE)

def gen(text, seed=None, processors=None):
    if seed is not None:
        torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    inp = build_ids(text)
    max_new = min(int(len(text)*1.3)*_TPF+21, 700)
    kwargs = dict(max_new_tokens=max_new, do_sample=True, temperature=0.4, top_p=0.9,
        repetition_penalty=1.05, pad_token_id=tokenizer.pad_token_id or _EOS,
        eos_token_id=[_EOS, _EOA])
    if processors:
        kwargs["logits_processor"] = processors
    with torch.no_grad():
        out = model.generate(inp, **kwargs)
    return out[0, inp.shape[1]:].tolist()

def extract_audio(raw):
    audio = []
    for t in raw:
        if _ACO <= t < _ACO + _TPF * _CBS:
            audio.append(t)
        elif t in (_EOS, _EOA):
            break
    return audio

def to_vals(audio):
    return [max(0, min(t - _ACO - (i%_TPF)*_CBS, _CBS-1)) for i, t in enumerate(audio)]

def sw_decode(audio):
    vals = to_vals(audio)
    chunks = []
    pos = 0
    while pos + _SW <= len(vals):
        w = vals[pos:pos+_SW]
        c0,c1,c2 = [],[],[]
        for i in range(_SW//_TPF):
            b=i*_TPF; c0.append(w[b]); c1.append(w[b+1]); c2+=[w[b+2],w[b+3]]; c1.append(w[b+4]); c2+=[w[b+5],w[b+6]]
        def cl(v): return [min(max(x,0),_CBS-1) for x in v]
        codes=[torch.tensor(cl(c0),dtype=torch.long,device=DEVICE).unsqueeze(0),
               torch.tensor(cl(c1),dtype=torch.long,device=DEVICE).unsqueeze(0),
               torch.tensor(cl(c2),dtype=torch.long,device=DEVICE).unsqueeze(0)]
        with torch.no_grad(): ah=snac_model.decode(codes)
        mid=ah.squeeze()[_SPF:_SPF*2]
        chunks.append((mid.cpu().float().clamp(-1,1).numpy()*32767).astype("int16").tobytes())
        pos+=_TPF
    return b"".join(chunks)

def f0_classify(pcm):
    from scipy.signal import correlate
    sr=24000; samples=np.frombuffer(pcm,dtype=np.int16).astype(np.float32)/32768.0
    ws=int(sr*0.04); hs=int(sr*0.02); mlag=int(sr/400); xlag=int(sr/60)
    frames=[]; pos=0
    while pos+ws<=len(samples):
        fr=samples[pos:pos+ws]; rms=float(np.sqrt(np.mean(fr**2)))
        if rms>0.003 and len(fr)>=xlag*2:
            ac=correlate(fr,fr,mode="full"); ac=ac[len(ac)//2:]; ac/=(ac[0]+1e-10)
            ar=ac[mlag:xlag]
            if len(ar)>0:
                pi=int(np.argmax(ar)); pv=float(ar[pi]); lag=pi+mlag
                if pv>0.3 and lag>0:
                    frames.append({"t_pos":pos,"f0":float(sr/lag)})
        pos+=hs
    voiced=[f for f in frames if f["f0"]>60]
    if not voiced: return {"pct_female":0,"pct_male":0,"median_f0":0,"switch":False,"male_frame_positions":[]}
    male=[f for f in voiced if f["f0"]<140]
    female=[f for f in voiced if f["f0"]>165]
    labels=["F" if f["f0"]>165 else "M" if f["f0"]<140 else "U" for f in voiced]
    ls="".join(labels)
    switch="FMF" in ls or ("FM" in ls and "MF" in ls)
    return {
        "pct_female":round(len(female)/len(voiced)*100,1),
        "pct_male":round(len(male)/len(voiced)*100,1),
        "median_f0":round(float(np.median([f["f0"] for f in voiced])),1),
        "switch":switch,
        "male_frame_positions":[f["t_pos"] for f in male],
        "voiced_frame_count":len(voiced),
    }

def save_wav(pcm, path, sr=24000):
    with wave.open(path,"wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(pcm)

OUT_DIR="/kaggle/working/output"
WAV_DIR=os.path.join(OUT_DIR,"wavs_v5")
os.makedirs(WAV_DIR, exist_ok=True)

CORPUS = [
    {"id":"P01","text":"Namaste. Main Maitri baat kar rahi hun. Kya aap sunne mein saksham hain?"},
    {"id":"P02","text":"Namaste, Prateek Das ji. Main Maitri hun, Rajat Finance ki collections team se. Aapke account mein pachaas hazaar rupay ka outstanding balance hai jo tees July tak due tha. Kya aap aaj is baare mein baat kar sakte hain?"},
    {"id":"P03","text":"Main samajh sakti hun ki yeh mushkil waqt hai. Lekin main aapko batana chahti hun ki hum aapke saath kaam karne ke liye taiyaar hain. Aap ek partial payment bhi kar sakte hain — jo bhi aap ke liye sambhav ho. Hum aapki situation ko samajhte hain aur ek flexible repayment plan bana sakte hain. Kya aap mujhe bata sakte hain ki aapke liye kya comfortable hoga?"},
    {"id":"P04","text":"Aapke loan account number teen saat paanch do ka balance ek lakh pachaas hazaar rupay hai. Due date paanch September do hazaar chhabbees thi. Late payment penalty teen percent per month ke hisaab se calculate hoti hai."},
    {"id":"P05","text":"Aapke saath seedha baat karna chahti hun — aapka account abhi severe overdue category mein hai. Hamari records ke mutabiq, aapne pichhle teen mahine mein koi payment nahi ki hai. Yeh aapke credit score ko bahut buri tarah affect kar sakta hai. Main chahti hun ki aap yeh clearly samjhein: agar hum abhi koi resolution nahi nikaalte, toh humein legal recovery process shuru karni padegi. Lekin main yeh nahi chahti. Main aapko ek mouka dena chahti hun. Kya aap abhi phone pe mujhe bata sakte hain ki aap kitni payment kar sakte hain? Koi bhi amount helpful hogi — chahe woh teen hazaar rupay hi kyon na ho."},
    {"id":"P06","text":"Aapka karz chukana bahut zaroori hai. Humara collection department aapke case ko priority de raha hai. Please ek baar sochiyen aur mujhe call karein. Main Maitri hun aur main hamesha aapki help ke liye available hun."},
]

# =====================================================================
# SECTION A: Calibration — identify male-cluster token IDs
# =====================================================================
print("\n" + "="*70)
print("SECTION A: Calibration sweep — build male-cluster token blacklist")
print("="*70)
CAL_RUNS = 50
CAL_TEXT = CORPUS[1]["text"]  # P02 — highest drift rate

male_tokens = {}   # token_id -> count of appearances in male frames
female_tokens = {} # token_id -> count of appearances in female frames

for run_i in range(CAL_RUNS):
    raw = gen(CAL_TEXT)
    audio = extract_audio(raw)
    if len(audio) < _SW:
        continue
    pcm = sw_decode(audio)
    f0 = f0_classify(pcm)

    # Map frame positions (sample offsets) back to token positions
    # Each token covers _SPF/2 samples at 24kHz (hop of _TPF tokens = _SPF samples)
    # Approximate: token position i covers sample range [i//_TPF * _SPF, ...]
    n_toks = len(audio)
    n_frames_per_token_group = _SPF  # samples per token advance
    male_sample_set = set(f0["male_frame_positions"])

    for tok_idx, tok_id in enumerate(audio):
        # Which sample range does this token fall in?
        frame_group = tok_idx // _TPF
        sample_start = frame_group * _SPF
        is_male_frame = any(abs(s - sample_start) < _SPF for s in male_sample_set)
        if is_male_frame:
            male_tokens[tok_id] = male_tokens.get(tok_id, 0) + 1
        else:
            female_tokens[tok_id] = female_tokens.get(tok_id, 0) + 1

    pct_m = f0.get("pct_male", 0)
    switch = f0.get("switch", False)
    print(f"  Cal run {run_i:02d}: M%={pct_m:.0f} switch={switch} | male_pool={len(male_tokens)} female_pool={len(female_tokens)}")

# Build blacklist: tokens that appear in male frames but NEVER in female frames
# OR appear in male frames at 10x+ rate vs female
male_only = set()
for tok_id, male_count in male_tokens.items():
    female_count = female_tokens.get(tok_id, 0)
    if female_count == 0 or (male_count / max(female_count, 1)) > 10:
        male_only.add(tok_id)

print(f"\nCalibration complete:")
print(f"  Total male-frame tokens seen: {len(male_tokens)}")
print(f"  Total female-frame tokens seen: {len(female_tokens)}")
print(f"  Male-exclusive blacklist size: {len(male_only)}")
print(f"  Top 20 blacklist tokens: {sorted(male_only)[:20]}")

# Save blacklist
with open(os.path.join(OUT_DIR, "male_cluster_blacklist.json"), "w") as f:
    json.dump(sorted(male_only), f)

# =====================================================================
# SECTION B: Two-layer fix: SpeakerLock + MaleClusterBlocker
# =====================================================================
print("\n" + "="*70)
print("SECTION B: Two-layer fix sweep")
print("="*70)

class _SpeakerLockProcessor(LogitsProcessor):
    def __init__(self, prompt_len, lock_tokens=21, boot_temp=0.05, base_temp=0.4):
        self.prompt_len = prompt_len
        self.lock_tokens = lock_tokens
        self.scale = base_temp / max(boot_temp, 1e-6)
    def __call__(self, input_ids, scores):
        if input_ids.shape[1] - self.prompt_len < self.lock_tokens:
            scores = scores * self.scale
        return scores

class _MaleClusterBlocker(LogitsProcessor):
    def __init__(self, blacklist_ids):
        self.blacklist = torch.tensor(sorted(blacklist_ids), dtype=torch.long)
    def __call__(self, input_ids, scores):
        if len(self.blacklist) > 0:
            bl = self.blacklist.to(scores.device)
            scores[:, bl] = float("-inf")
        return scores

def gen_fixed(text, lock_tokens=21, boot_temp=0.05):
    inp = build_ids(text)
    processors = LogitsProcessorList([
        _SpeakerLockProcessor(inp.shape[1], lock_tokens, boot_temp),
        _MaleClusterBlocker(male_only),
    ])
    return gen(text, processors=processors)

N_RUNS = 30
RESULTS = {}

for c in CORPUS:
    cid, text = c["id"], c["text"]
    print(f"\n  TEXT: {cid}")
    runs = []; drifts = 0
    for run_i in range(N_RUNS):
        raw = gen_fixed(text)
        audio = extract_audio(raw)
        if len(audio) < _SW:
            print(f"    Run {run_i:02d}: SKIP"); continue
        pcm = sw_decode(audio)
        f0 = f0_classify(pcm)
        marker = " *** DRIFT ***" if f0["switch"] else ""
        print(f"    Run {run_i:02d}: {len(audio)} tok | F0={f0['median_f0']}Hz | F%={f0['pct_female']:.0f} M%={f0['pct_male']:.0f}{marker}")
        if f0["switch"] or f0["pct_male"] > 20:
            drifts += 1
            save_wav(pcm, os.path.join(WAV_DIR, f"{cid}_run{run_i:02d}_DRIFT.wav"))
        runs.append(f0)
    n_sw = sum(1 for r in runs if r["switch"])
    print(f"  SUMMARY {cid}: {n_sw}/{len(runs)} switches")
    RESULTS[cid] = {"n_switch": n_sw, "n_runs": len(runs)}

print("\n" + "="*70)
print("FINAL TABLE — v5 two-layer fix")
print("="*70)
V3 = {"P01":3,"P02":22}
for c in CORPUS:
    cid = c["id"]
    n = RESULTS.get(cid, {}).get("n_switch", "?")
    b = V3.get(cid, "?")
    delta = (b - n) if isinstance(b, int) and isinstance(n, int) else "?"
    print(f"  {cid}: {b}/30 -> {n}/30  (reduced by {delta})")

all_clean = all(v["n_switch"]==0 for v in RESULTS.values())
if all_clean:
    print("\nVERDICT: ALL CLEAN — two-layer fix eliminates all male drift")
    print("FIX: SpeakerLockProcessor(lock=21) + MaleClusterBlocker(blacklist from calibration)")
else:
    total = sum(v["n_switch"] for v in RESULTS.values())
    print(f"\nVERDICT: {total} drifts remain — need deeper investigation")
    print("POSSIBLE CAUSE: Female/male cluster overlap too large; try longer calibration or lower boot_temp")

with open(os.path.join(OUT_DIR, "v5_results.json"), "w") as f:
    json.dump(RESULTS, f, indent=2)
print("\nv5 KERNEL COMPLETE")
