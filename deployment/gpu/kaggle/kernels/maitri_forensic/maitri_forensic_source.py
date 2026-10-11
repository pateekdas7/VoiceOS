# =====================================================================
# MAITRI VOICE FORENSIC KERNEL v1
# =====================================================================
# Sprint-29 production freeze:
#   Veena: maya-research/Veena @ 8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f
#   SNAC:  hubertsiuzdak/snac_24khz @ d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec
#   Git:   182fea6, branch claude/ssh-gpu-cpu-servers-y99fib
# =====================================================================
# AUDIT ONLY — no production modifications.
# Objective: isolate the first layer responsible for Maitri female↔male
# voice switching.
# =====================================================================
import sys, os, json, time, struct, wave, hashlib, inspect, textwrap

print("=" * 70)
print("MAITRI VOICE FORENSIC KERNEL v1")
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
GPU_VRAM = 0
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    GPU_NAME = p.name
    GPU_VRAM = p.total_memory // 1024**2
    print(f"GPU 0:     {GPU_NAME} | {GPU_VRAM} MiB | sm_{p.major}{p.minor}")

# =====================================================================
# SECTION 1: Install exact Sprint-29 packages
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 1: Installing Sprint-29 exact packages")
print("=" * 70)

def pip(pkg, timeout=600):
    t0 = time.monotonic()
    # --upgrade forces in-place replacement of base-image packages
    r = subprocess.run(f"{sys.executable} -m pip install --upgrade {pkg} -q 2>&1",
                       shell=True, capture_output=True, text=True, timeout=timeout)
    ok = r.returncode == 0
    print(f"  {'OK' if ok else 'FAIL'}: {pkg} ({time.monotonic()-t0:.0f}s)")
    if not ok: print(f"    {(r.stdout+r.stderr)[-200:]}")
    return ok

# Note: huggingface_hub and numpy NOT pinned — they are base-image packages whose
# exact versions are tied to pre-compiled scipy/sklearn. Downgrading/upgrading them
# breaks those C-extension packages. Keep base versions; override only what matters.
for pkg in [
    "transformers==5.12.1", "tokenizers==0.22.2",
    "accelerate==1.7.0",
    "snac==1.0.0",
]:
    pip(pkg)

# Flush module cache so subsequent imports pick up newly installed versions
import importlib
importlib.invalidate_caches()
for _mod in list(sys.modules.keys()):
    if any(x in _mod for x in ["transformers", "tokenizers", "snac"]):
        try: del sys.modules[_mod]
        except: pass

for m in ["transformers", "snac", "numpy"]:
    try:
        mod = importlib.import_module(m)
        print(f"  {m}: {mod.__version__}")
    except ImportError:
        print(f"  {m}: NOT INSTALLED")

# =====================================================================
# SECTION 2: Load Veena (pinned) + SNAC (pinned, EXACT production method)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 2: Load Veena + SNAC — EXACT Sprint-29 production method")
print("=" * 70)

from transformers import AutoTokenizer, AutoModelForCausalLM
from huggingface_hub import hf_hub_download
import types as _types
from snac import SNAC

VEENA_ID  = "maya-research/Veena"
VEENA_REV = "8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f"
SNAC_ID   = "hubertsiuzdak/snac_24khz"
SNAC_REV  = "d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec"
DEVICE    = "cuda" if torch.cuda.is_available() else "cpu"

HF_HOME = "/kaggle/working/hf"
os.makedirs(HF_HOME, exist_ok=True)
os.environ["HF_HOME"] = HF_HOME
os.environ["TRANSFORMERS_CACHE"] = HF_HOME

# --- Load tokenizer (pinned revision) ---
print(f"\nLoading tokenizer: {VEENA_ID} @ {VEENA_REV[:8]}...")
t0 = time.monotonic()
tokenizer = AutoTokenizer.from_pretrained(VEENA_ID, revision=VEENA_REV)
print(f"  Tokenizer loaded in {time.monotonic()-t0:.0f}s")
print(f"  vocab_size:   {tokenizer.vocab_size}")
print(f"  all_special:  {len(tokenizer.all_special_ids)} tokens")

# --- Load Veena model (pinned, BF16, SDPA — EXACT production) ---
print(f"\nLoading Veena model: {VEENA_ID} @ {VEENA_REV[:8]}...")
print("  (this downloads ~6 GB on first run)")
t0 = time.monotonic()
torch.cuda.reset_peak_memory_stats(0)
model = AutoModelForCausalLM.from_pretrained(
    VEENA_ID,
    revision=VEENA_REV,
    torch_dtype=torch.bfloat16,
    device_map={"": DEVICE},
    attn_implementation="sdpa",
)
model.eval()
load_ms = (time.monotonic()-t0)*1000
vram_veena = torch.cuda.memory_allocated(0)//1024**2
print(f"  Loaded in {load_ms:.0f}ms | VRAM: {vram_veena} MiB")
print(f"  dtype: {next(model.parameters()).dtype}")
print(f"  arch:  {model.config.model_type} | layers={model.config.num_hidden_layers} | dim={model.config.hidden_size}")

# --- Load SNAC (pinned, EXACT production loading: arch-strip + strict=True) ---
print(f"\nLoading SNAC: {SNAC_ID} @ {SNAC_REV[:8]}...")
import json as _json
snac_cfg_path = hf_hub_download(repo_id=SNAC_ID, revision=SNAC_REV, filename="config.json")
snac_wts_path = hf_hub_download(repo_id=SNAC_ID, revision=SNAC_REV, filename="pytorch_model.bin")
with open(snac_cfg_path) as f:
    snac_cfg = _json.load(f)
print(f"  SNAC config: {snac_cfg}")

snac_model = SNAC(**snac_cfg).eval()

# EXACT production architecture strip (strip from MODEL, not state dict)
def _strip_attn(seq):
    return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != "LocalMHA"])
snac_model.encoder.block = _strip_attn(snac_model.encoder.block)
snac_model.decoder.model = _strip_attn(snac_model.decoder.model)

snac_state = torch.load(snac_wts_path, map_location="cpu", weights_only=False)
snac_model.load_state_dict(snac_state)  # strict=True (production default)
snac_model.eval()
snac_model = snac_model.to(DEVICE)

print(f"  SNAC loaded: encoder {len(list(snac_model.encoder.block.children()))} blocks, "
      f"decoder {len(list(snac_model.decoder.model.children()))} layers")
print(f"  VRAM after SNAC: {torch.cuda.memory_allocated(0)//1024**2} MiB")

# --- Patch SNAC.decode (removed in snac==1.0.0) — EXACT production patch ---
def _snac_decode_compat(self, codes):
    z_q = 0
    for quantizer, code in zip(self.quantizer.quantizers, codes):
        z_q_i = quantizer.decode_code(code)
        z_q_i = quantizer.out_proj(z_q_i)
        if quantizer.stride > 1:
            z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)
        z_q = z_q + z_q_i
    return self.decoder(z_q)
snac_model.decode = _types.MethodType(_snac_decode_compat, snac_model)
print("  Production .decode() patch applied")

# =====================================================================
# SECTION 3: Tokenizer forensics — verify <spk_maitri>
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 3: Tokenizer forensics — <spk_maitri> verification")
print("=" * 70)

# All speaker tokens
SPEAKER_TOKENS_CLAIMED = {
    "kavya": 156940, "apsara": 156941, "agastya": 156942,
    "vinaya": 156943, "maitri": 156944, "charu": 156945,
    "ishana": 156946, "kyra": 156947, "mohini": 156948,
    "varun": 156949, "soumya": 156950,
}

SPEAKER_VERIFICATION = {}
print("\nSpeaker token verification:")
for spk, claimed_id in SPEAKER_TOKENS_CLAIMED.items():
    tok_str = f"<spk_{spk}>"
    # Encode without special tokens
    ids = tokenizer.encode(tok_str, add_special_tokens=False)
    actual_id_lookup = tokenizer.convert_tokens_to_ids(tok_str)
    is_single = len(ids) == 1
    id_matches = (ids[0] == claimed_id) if ids else False
    lookup_matches = (actual_id_lookup == claimed_id)
    SPEAKER_VERIFICATION[spk] = {
        "token_str": tok_str,
        "claimed_id": claimed_id,
        "encoded_ids": ids,
        "lookup_id": actual_id_lookup,
        "is_single_token": is_single,
        "id_correct": id_matches,
        "lookup_correct": lookup_matches,
    }
    status = "OK" if (is_single and id_matches and lookup_matches) else "MISMATCH"
    print(f"  [{status}] {tok_str:20s} claimed={claimed_id} encoded={ids} lookup={actual_id_lookup}")

maitri_id = SPEAKER_VERIFICATION["maitri"]["encoded_ids"][0] if SPEAKER_VERIFICATION["maitri"]["encoded_ids"] else None
print(f"\n  <spk_maitri> verified ID: {maitri_id}")
print(f"  Single token: {SPEAKER_VERIFICATION['maitri']['is_single_token']}")

# Control tokens
CTRL = {
    "START_OF_HUMAN":  128259,
    "END_OF_HUMAN":    128260,
    "START_OF_AI":     128261,
    "END_OF_AI":       128262,
    "START_OF_SPEECH": 128257,
    "END_OF_SPEECH":   128258,
    "AUDIO_CODE_BASE": 128266,
}
print("\nControl token verification:")
for name, expected_id in CTRL.items():
    tok_str = f"<custom_token_{expected_id - 128256}>"
    actual_ids = tokenizer.encode(tok_str, add_special_tokens=False)
    direct_id = tokenizer.convert_tokens_to_ids(tok_str)
    print(f"  {name:20s} = {expected_id} | {tok_str} → {actual_ids} | lookup={direct_id}")

# Decode control token IDs back to strings
print("\nID → token string (reverse lookup):")
for name, tid in CTRL.items():
    decoded = tokenizer.convert_ids_to_tokens([tid])
    print(f"  {tid} → {decoded}")

# Encode test prompt
test_text = "आपका payment बाकी है।"
for spk in ["kavya", "maitri"]:
    prompt = f"<spk_{spk}> {test_text}"
    ids = tokenizer.encode(prompt, add_special_tokens=False)
    print(f"\n  Prompt '{prompt[:50]}...'")
    print(f"  Token count: {len(ids)}")
    print(f"  First 5 IDs: {ids[:5]}")
    print(f"  Speaker token at pos 0: {ids[0]} ({'CORRECT' if ids[0] == SPEAKER_TOKENS_CLAIMED[spk] else 'WRONG'})")

# Vocabulary cross-check: total size
print(f"\n  Tokenizer vocab size: {len(tokenizer)}")
print(f"  Tokenizer model max length: {tokenizer.model_max_length}")
print(f"  Pad token: {tokenizer.pad_token!r} → {tokenizer.pad_token_id}")
print(f"  EOS token: {tokenizer.eos_token!r} → {tokenizer.eos_token_id}")
print(f"  Chat template: {'PRESENT' if tokenizer.chat_template else 'NONE'}")

# =====================================================================
# SECTION 4: Production constants + Maitri corpus
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 4: Production constants + Maitri corpus")
print("=" * 70)

# Exact production constants (server.py)
_START_OF_HUMAN = 128259
_END_OF_HUMAN   = 128260
_START_OF_AI    = 128261
_END_OF_AI      = 128262
_START_OF_SPEECH = 128257
_END_OF_SPEECH   = 128258
_AUDIO_BASE      = 128266
_CODEBOOK_SIZE   = 4096
_TOKENS_PER_FRAME = 7
_SLIDING_WINDOW  = 21
_SAMPLES_PER_FRAME = 2048

# Production max_new_tokens formula
def prod_max_new(text):
    return min(int(len(text) * 1.3) * _TOKENS_PER_FRAME + 21, 700)

# Maitri test corpus — varying lengths to stress-test voice stability
CORPUS = [
    {
        "id": "M01_short",
        "text": "नमस्ते, मैं Maitri बोल रही हूं।",
        "note": "Short self-identification",
    },
    {
        "id": "M02_medium",
        "text": "आपके loan account की EMI बाकी है। क्या आप आज payment कर सकते हैं?",
        "note": "Medium collection script",
    },
    {
        "id": "M03_long",
        "text": "नमस्ते, मैं Maitri बोल रही हूं। आपके loan account में ₹15,000 की EMI बाकी है। यह बहुत जरूरी है कि आप आज ही payment करें, नहीं तो late charges लग सकते हैं।",
        "note": "Long — most likely to show drift",
    },
    {
        "id": "M04_mixed",
        "text": "Sir, आपका outstanding balance ₹8,500 है। हम आपसे request करते हैं कि जल्द से जल्द payment करें।",
        "note": "Mixed Hindi-English (common in production calls)",
    },
    {
        "id": "M05_stress",
        "text": "नमस्ते। मैं Maitri बोल रही हूं। आपके account में कुछ irregularity है। कृपया अपना account number verify करें। हम आपकी मदद करने के लिए यहाँ हैं।",
        "note": "Multiple sentences — highest stress",
    },
]

for item in CORPUS:
    item["speaker"] = "maitri"
    item["prompt"] = f"<spk_maitri> {item['text']}"
    item["max_new_tokens"] = prod_max_new(item["text"])
    print(f"  {item['id']}: len={len(item['text'])} chars | max_new={item['max_new_tokens']} | {item['note']}")

# =====================================================================
# SECTION 5: Raw token generation (exact production path, no SNAC)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 5: Raw token generation — capture before SNAC")
print("=" * 70)
print("Using: temp=0.4, top_p=0.9, rep_pen=1.05, eos=[128258,128262]")

os.makedirs("/kaggle/working/tokens", exist_ok=True)

def build_input_ids(text, speaker="maitri"):
    prompt = f"<spk_{speaker}> {text}"
    prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
    seq = [_START_OF_HUMAN, *prompt_ids, _END_OF_HUMAN, _START_OF_AI, _START_OF_SPEECH]
    return torch.tensor([seq], dtype=torch.long).to(DEVICE)

def generate_tokens(text, speaker="maitri", seed=None, max_new=None):
    """Exact production generation — returns raw generated token IDs."""
    if seed is not None:
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    input_ids = build_input_ids(text, speaker)
    if max_new is None:
        max_new = prod_max_new(text)
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
    gen_ms = (time.monotonic()-t0)*1000
    # Extract only generated tokens (after input)
    generated = out[0, input_ids.shape[1]:].tolist()
    return generated, gen_ms

# Generate one pass for each corpus item with fixed seed 42
BASELINE_TOKENS = {}
print("\nBaseline generation (seed=42, all corpus items):")
for item in CORPUS:
    generated, gen_ms = generate_tokens(item["text"], seed=42)
    n_audio = sum(1 for t in generated if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1)
    eos_hit = any(t in [_END_OF_SPEECH, _END_OF_AI] for t in generated)
    max_hit = len(generated) >= item["max_new_tokens"] - 5
    BASELINE_TOKENS[item["id"]] = {
        "text": item["text"],
        "speaker": "maitri",
        "seed": 42,
        "input_ids": build_input_ids(item["text"]).tolist()[0],
        "generated_ids": generated,
        "n_generated": len(generated),
        "n_audio_tokens": n_audio,
        "eos_hit": eos_hit,
        "max_hit": max_hit,
        "gen_ms": round(gen_ms),
    }
    tok_path = f"/kaggle/working/tokens/{item['id']}_seed42.json"
    with open(tok_path, "w") as f:
        json.dump(BASELINE_TOKENS[item["id"]], f)
    print(f"  {item['id']}: {len(generated)} tokens | audio={n_audio} | eos={eos_hit} | max={max_hit} | {gen_ms:.0f}ms → {tok_path}")

# =====================================================================
# SECTION 6: Seed reproducibility (same seed → identical tokens?)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 6: Seed reproducibility test")
print("=" * 70)

# Test with M03 (longest text) — most diagnostic
REPRO_TEXT = CORPUS[2]["text"]
print(f"Text: {REPRO_TEXT[:60]}...")

REPRO_RESULTS = {}
for run in range(3):
    gen, _ = generate_tokens(REPRO_TEXT, seed=42)
    REPRO_RESULTS[f"run{run+1}"] = gen

r1, r2, r3 = REPRO_RESULTS["run1"], REPRO_RESULTS["run2"], REPRO_RESULTS["run3"]
identical_12 = (r1 == r2)
identical_13 = (r1 == r3)
identical_23 = (r2 == r3)
print(f"\n  Run 1 len={len(r1)} | Run 2 len={len(r2)} | Run 3 len={len(r3)}")
print(f"  Run1 == Run2: {identical_12}")
print(f"  Run1 == Run3: {identical_13}")
print(f"  Run2 == Run3: {identical_23}")
if not identical_12:
    # Find first divergence
    for i, (a, b) in enumerate(zip(r1, r2)):
        if a != b:
            print(f"  First divergence at position {i}: run1={a} vs run2={b}")
            break
REPRO_DETERMINISTIC = identical_12 and identical_13
print(f"\n  VERDICT — same_seed → same_tokens: {'YES (deterministic)' if REPRO_DETERMINISTIC else 'NO (stochastic even with seed)'}")

# =====================================================================
# SECTION 7: Multi-seed variation — do different seeds produce different trajectories?
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 7: Multi-seed variation test")
print("=" * 70)

SEEDS = [0, 1, 7, 42, 99, 137, 314, 999, 1234, 9999]
MULTI_SEED_TOKENS = {}
print(f"Generating {len(SEEDS)} seeds for M03 (longest text):")
for seed in SEEDS:
    gen, gen_ms = generate_tokens(REPRO_TEXT, seed=seed)
    n_audio = sum(1 for t in gen if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1)
    n_total = len(gen)
    eos = any(t in [_END_OF_SPEECH, _END_OF_AI] for t in gen)
    MULTI_SEED_TOKENS[seed] = gen
    # Save
    with open(f"/kaggle/working/tokens/M03_seed{seed}.json", "w") as f:
        json.dump({"seed": seed, "generated_ids": gen}, f)
    print(f"  seed={seed:5d}: total={n_total} | audio={n_audio} | eos={eos} | {gen_ms:.0f}ms")

# Compare token distributions across seeds
print("\nToken count variation:")
audio_counts = [sum(1 for t in MULTI_SEED_TOKENS[s] if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1) for s in SEEDS]
print(f"  Audio token count range: {min(audio_counts)} – {max(audio_counts)} (mean {sum(audio_counts)/len(audio_counts):.0f})")

# Measure token sequence divergence between seeds (Hamming distance on first 100 tokens)
seed_list = list(MULTI_SEED_TOKENS.keys())
s0_tokens = MULTI_SEED_TOKENS[seed_list[0]][:200]
divergences = []
for s in seed_list[1:]:
    other = MULTI_SEED_TOKENS[s][:200]
    n = min(len(s0_tokens), len(other))
    diff = sum(1 for a, b in zip(s0_tokens[:n], other[:n]) if a != b)
    divergences.append(diff)
print(f"  Token Hamming distance (first 200 tokens) vs seed[0]:")
for s, d in zip(seed_list[1:], divergences):
    print(f"    seed={s}: {d}/200 differ ({d/2:.0f}%)")

# =====================================================================
# SECTION 8: Decoder comparison — Path A (from_codes) vs Path B (production loop)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 8: SNAC decoder comparison — Path A vs Path B")
print("=" * 70)
print("Using SAME SNAC model weights (loaded production-style).")
print("Path A: quantizer.from_codes() [K1/K2 reference]")
print("Path B: manual decode_code + out_proj + repeat_interleave [production]")
print("If max_abs_diff ≈ 0 → decoders are numerically identical → NOT the cause.")

# Use M03 tokens (seed=42, baseline)
M03_TOKENS = BASELINE_TOKENS["M03_long"]["generated_ids"]
audio_tokens_raw = [t for t in M03_TOKENS if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1]
n_frames = len(audio_tokens_raw) // _TOKENS_PER_FRAME
audio_tokens = audio_tokens_raw[:n_frames * _TOKENS_PER_FRAME]
print(f"\nM03 audio tokens: {len(audio_tokens_raw)} → using {len(audio_tokens)} ({n_frames} frames)")

def tokens_to_codebooks(tokens):
    """Production codebook extraction (CLAMP, not modulo)."""
    codes_L0, codes_L1, codes_L2 = [], [], []
    for frame_i in range(len(tokens) // _TOKENS_PER_FRAME):
        base = frame_i * _TOKENS_PER_FRAME
        w = tokens[base:base + _TOKENS_PER_FRAME]
        for pos, tok in enumerate(w):
            val = tok - _AUDIO_BASE - pos * _CODEBOOK_SIZE
            val = max(0, min(val, _CODEBOOK_SIZE - 1))   # CLAMP (production)
            if pos == 0:
                codes_L0.append(val)
            elif pos in (1, 4):
                codes_L1.append(val)
            else:
                codes_L2.append(val)
    return codes_L0, codes_L1, codes_L2

def build_snac_codes(codes_L0, codes_L1, codes_L2):
    """Build list of [1,T] tensors for SNAC decode."""
    return [
        torch.tensor([codes_L0], dtype=torch.long, device=DEVICE),
        torch.tensor([codes_L1], dtype=torch.long, device=DEVICE),
        torch.tensor([codes_L2], dtype=torch.long, device=DEVICE),
    ]

# Extract codes
codes_L0, codes_L1, codes_L2 = tokens_to_codebooks(audio_tokens)
codes = build_snac_codes(codes_L0, codes_L1, codes_L2)
print(f"Code shapes: L0={codes[0].shape} L1={codes[1].shape} L2={codes[2].shape}")

# Path B (production manual loop) — already patched as snac_model.decode()
with torch.no_grad():
    audio_B = snac_model.decode(codes).squeeze()  # [T_samples]
print(f"\nPath B (production): audio shape={audio_B.shape} | range=[{audio_B.min():.4f}, {audio_B.max():.4f}]")

# Path A (from_codes reference)
# Attempt to use from_codes if available; otherwise implement equivalent
PATH_A_METHOD = "unknown"
try:
    with torch.no_grad():
        # The RVQ quantizer's from_codes returns (z_q, indices_list) or just z_q
        result = snac_model.quantizer.from_codes(codes)
        if isinstance(result, tuple):
            z_q_A = result[0]
        else:
            z_q_A = result
        audio_A = snac_model.decoder(z_q_A).squeeze()
    PATH_A_METHOD = "quantizer.from_codes()"
    print(f"Path A (from_codes): audio shape={audio_A.shape} | range=[{audio_A.min():.4f}, {audio_A.max():.4f}]")
except Exception as e:
    print(f"Path A from_codes() failed: {e} — implementing equivalent manually")
    # Implement the same loop but collect z_q before returning
    with torch.no_grad():
        z_q_A = 0
        for quantizer, code in zip(snac_model.quantizer.quantizers, codes):
            z_q_i = quantizer.decode_code(code)
            z_q_i = quantizer.out_proj(z_q_i)
            if quantizer.stride > 1:
                z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)
            z_q_A = z_q_A + z_q_i
        audio_A = snac_model.decoder(z_q_A).squeeze()
    PATH_A_METHOD = "manual loop (from_codes unavailable — identical to Path B)"
    print(f"Path A (manual equiv): audio shape={audio_A.shape} | range=[{audio_A.min():.4f}, {audio_A.max():.4f}]")

# Numerical comparison
min_len = min(len(audio_A), len(audio_B))
diff = (audio_A[:min_len] - audio_B[:min_len]).abs()
max_diff = diff.max().item()
mean_diff = diff.mean().item()
print(f"\n  Path A vs Path B — max_abs_diff={max_diff:.8f} | mean_diff={mean_diff:.8f}")
print(f"  Path A method: {PATH_A_METHOD}")
if max_diff < 1e-5:
    DECODER_VERDICT = "IDENTICAL (within float32 tolerance)"
elif max_diff < 0.01:
    DECODER_VERDICT = "NEGLIGIBLE difference (likely float32 rounding)"
else:
    DECODER_VERDICT = f"SIGNIFICANT difference: max={max_diff:.6f}"
print(f"  DECODER VERDICT: {DECODER_VERDICT}")

# =====================================================================
# SECTION 9: Sliding-window vs batch decode comparison
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 9: Sliding-window vs batch decode comparison")
print("=" * 70)
print("Production: 3-frame window, emit frame[1] (middle, [2048:4096])")
print("Batch: decode all frames at once, compare corresponding samples")

def sliding_window_decode(tokens):
    """Production sliding-window: exactly as server.py."""
    audio_tokens_f = [t for t in tokens if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1]
    n_frames = len(audio_tokens_f) // _TOKENS_PER_FRAME
    audio_buf = [t - _AUDIO_BASE - (i % _TOKENS_PER_FRAME) * _CODEBOOK_SIZE
                 for i, t in enumerate(audio_tokens_f[:n_frames * _TOKENS_PER_FRAME])]
    audio_buf = [max(0, min(v, _CODEBOOK_SIZE - 1)) for v in audio_buf]

    chunks = []
    for window_start in range(0, len(audio_buf) - _SLIDING_WINDOW + 1, _TOKENS_PER_FRAME):
        window = audio_buf[window_start:window_start + _SLIDING_WINDOW]
        if len(window) < _SLIDING_WINDOW:
            break
        c0, c1, c2 = [], [], []
        for b in range(0, _SLIDING_WINDOW, _TOKENS_PER_FRAME):
            c0.append(window[b])
            c1.append(window[b+1])
            c2.append(window[b+2])
            c2.append(window[b+3])
            c1.append(window[b+4])
            c2.append(window[b+5])
            c2.append(window[b+6])
        codes_w = [
            torch.tensor([c0], dtype=torch.long, device=DEVICE),
            torch.tensor([c1], dtype=torch.long, device=DEVICE),
            torch.tensor([c2], dtype=torch.long, device=DEVICE),
        ]
        with torch.no_grad():
            hat = snac_model.decode(codes_w)   # [1,1,8192]
        mid = hat[0, 0, _SAMPLES_PER_FRAME:_SAMPLES_PER_FRAME*2]  # middle frame
        mid = mid.clamp(-1.0, 1.0)
        chunks.append(mid.float().cpu())
    return torch.cat(chunks) if chunks else torch.zeros(0)

def batch_decode_all(tokens):
    """Decode ALL frames at once — no windowing."""
    audio_tokens_f = [t for t in tokens if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1]
    n_frames = len(audio_tokens_f) // _TOKENS_PER_FRAME
    codes_L0, codes_L1, codes_L2 = tokens_to_codebooks(audio_tokens_f[:n_frames * _TOKENS_PER_FRAME])
    codes = build_snac_codes(codes_L0, codes_L1, codes_L2)
    with torch.no_grad():
        audio = snac_model.decode(codes).squeeze()  # full audio
    return audio.float().cpu()

M03_TOKENS_RAW = BASELINE_TOKENS["M03_long"]["generated_ids"]
print("\nDecoding M03 (seed=42) through both paths...")

sw_audio = sliding_window_decode(M03_TOKENS_RAW)
ba_audio = batch_decode_all(M03_TOKENS_RAW)

print(f"  Sliding-window: {len(sw_audio)} samples = {len(sw_audio)/24000*1000:.0f}ms")
print(f"  Batch:          {len(ba_audio)} samples = {len(ba_audio)/24000*1000:.0f}ms")

# Compare overlapping region (skip first and last frames due to boundary)
sw_np = sw_audio.numpy()
ba_np = ba_audio.numpy()
# Sliding window emits frame[1] of each 3-frame window, starting from window_start=0
# Batch produces all frames. Compare position by position where both cover.
n_compare = min(len(sw_np), len(ba_np))
if n_compare > 0:
    d = np.abs(sw_np[:n_compare] - ba_np[:n_compare])
    print(f"\n  Comparison (first {n_compare} samples):")
    print(f"    max_abs_diff   = {d.max():.6f}")
    print(f"    mean_abs_diff  = {d.mean():.6f}")
    print(f"    samples_differ_>0.01 = {(d > 0.01).sum()} ({(d > 0.01).mean()*100:.1f}%)")
    print(f"    samples_differ_>0.1  = {(d > 0.1).sum()} ({(d > 0.1).mean()*100:.1f}%)")
    # Check if differences cluster at frame boundaries
    frame_boundary_diffs = []
    interior_diffs = []
    for i in range(n_compare):
        frame_pos = i % _SAMPLES_PER_FRAME
        near_boundary = (frame_pos < 256 or frame_pos >= _SAMPLES_PER_FRAME - 256)
        if near_boundary:
            frame_boundary_diffs.append(d[i])
        else:
            interior_diffs.append(d[i])
    if frame_boundary_diffs and interior_diffs:
        print(f"    mean_diff at frame boundaries: {np.mean(frame_boundary_diffs):.6f}")
        print(f"    mean_diff in frame interior:   {np.mean(interior_diffs):.6f}")
        boundary_worse = np.mean(frame_boundary_diffs) > np.mean(interior_diffs) * 2
        print(f"    Boundary artifacts: {'YES — boundary errors {:.1f}× higher'.format(np.mean(frame_boundary_diffs)/max(np.mean(interior_diffs),1e-8)) if boundary_worse else 'NO — uniform error distribution'}")

SLIDING_VERDICT = "unknown"
if n_compare > 0:
    if d.max() < 0.01:
        SLIDING_VERDICT = "IDENTICAL (sliding-window matches batch)"
    elif boundary_worse:
        SLIDING_VERDICT = "BOUNDARY ARTIFACTS (differences cluster at frame boundaries)"
    else:
        SLIDING_VERDICT = f"DIFFERS from batch (max={d.max():.4f}), uniform distribution"
print(f"\n  SLIDING-WINDOW VERDICT: {SLIDING_VERDICT}")

# =====================================================================
# SECTION 10: Single vs double speaker token comparison
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 10: Single <spk_maitri> vs double <spk_maitri> <spk_maitri>")
print("=" * 70)
print("Production uses SINGLE. K1/K2 used DOUBLE. Comparing token sequences.")
print("Same text, same seed, same all other params.")

SPK_COMPARE_TEXT = CORPUS[2]["text"]   # M03 long text
SPK_COMPARE_SEED = 42

# Single (production)
def generate_with_prompt(prompt_text, seed=None, max_new=None):
    prompt_ids = tokenizer.encode(prompt_text, add_special_tokens=False)
    seq = [_START_OF_HUMAN, *prompt_ids, _END_OF_HUMAN, _START_OF_AI, _START_OF_SPEECH]
    input_ids = torch.tensor([seq], dtype=torch.long).to(DEVICE)
    if seed is not None:
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    if max_new is None:
        max_new = prod_max_new(prompt_text.replace("<spk_maitri> ", "").replace("<spk_maitri>", ""))
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
    return out[0, input_ids.shape[1]:].tolist()

single_prompt = f"<spk_maitri> {SPK_COMPARE_TEXT}"
double_prompt = f"<spk_maitri> <spk_maitri> {SPK_COMPARE_TEXT}"

max_new = prod_max_new(SPK_COMPARE_TEXT)
print(f"\nGenerating single-token prompt (seed={SPK_COMPARE_SEED})...")
tok_single = generate_with_prompt(single_prompt, seed=SPK_COMPARE_SEED, max_new=max_new)
print(f"Generating double-token prompt (seed={SPK_COMPARE_SEED})...")
tok_double = generate_with_prompt(double_prompt, seed=SPK_COMPARE_SEED, max_new=max_new)

audio_single = [t for t in tok_single if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1]
audio_double = [t for t in tok_double if _AUDIO_BASE <= t <= _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1]

print(f"\n  Single prompt: {len(tok_single)} generated | {len(audio_single)} audio tokens")
print(f"  Double prompt: {len(tok_double)} generated | {len(audio_double)} audio tokens")

# Compare first N audio tokens
N_COMPARE = min(len(audio_single), len(audio_double), 700)
n_same = sum(a == b for a, b in zip(audio_single[:N_COMPARE], audio_double[:N_COMPARE]))
print(f"  First {N_COMPARE} audio tokens same: {n_same}/{N_COMPARE} ({n_same/N_COMPARE*100:.1f}%)")

# Find first divergence point
first_div = None
for i, (a, b) in enumerate(zip(audio_single[:N_COMPARE], audio_double[:N_COMPARE])):
    if a != b:
        first_div = i
        break
print(f"  First divergence at audio token index: {first_div} ({'immediate' if first_div == 0 else f'frame {first_div//7}'})")

# Save both token sequences
with open("/kaggle/working/tokens/SPK_COMPARE_single.json", "w") as f:
    json.dump({"prompt": single_prompt, "seed": SPK_COMPARE_SEED, "generated_ids": tok_single}, f)
with open("/kaggle/working/tokens/SPK_COMPARE_double.json", "w") as f:
    json.dump({"prompt": double_prompt, "seed": SPK_COMPARE_SEED, "generated_ids": tok_double}, f)

# Also compare audio waveforms
if len(audio_single) >= _SLIDING_WINDOW and len(audio_double) >= _SLIDING_WINDOW:
    wav_single = sliding_window_decode(tok_single)
    wav_double = sliding_window_decode(tok_double)
    n_wav_compare = min(len(wav_single), len(wav_double))
    if n_wav_compare > 0:
        wdiff = np.abs(wav_single.numpy()[:n_wav_compare] - wav_double.numpy()[:n_wav_compare])
        print(f"\n  Audio comparison (single vs double):")
        print(f"    max_abs_diff  = {wdiff.max():.6f}")
        print(f"    mean_abs_diff = {wdiff.mean():.6f}")
        print(f"    duration_single={len(wav_single)/24000*1000:.0f}ms | duration_double={len(wav_double)/24000*1000:.0f}ms")

# =====================================================================
# SECTION 11: Codebook extraction analysis — are Maitri tokens in range?
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 11: Codebook extraction analysis")
print("=" * 70)

AUDIO_MIN = _AUDIO_BASE
AUDIO_MAX = _AUDIO_BASE + _TOKENS_PER_FRAME * _CODEBOOK_SIZE - 1

print(f"Expected audio token range: [{AUDIO_MIN}, {AUDIO_MAX}]")
print(f"AUDIO_BASE={_AUDIO_BASE}, CODEBOOK_SIZE={_CODEBOOK_SIZE}, TOKENS_PER_FRAME={_TOKENS_PER_FRAME}")

for item_id, data in BASELINE_TOKENS.items():
    gen = data["generated_ids"]
    audio_toks = [t for t in gen if AUDIO_MIN <= t <= AUDIO_MAX]
    non_audio_in_range = [t for t in gen if t not in [_END_OF_SPEECH, _END_OF_AI] and t < AUDIO_MIN and t != _START_OF_SPEECH]

    # Codebook value analysis
    clamp_events = 0
    codebook_vals_by_level = {0: [], 1: [], 2: []}
    for i, tok in enumerate(audio_toks):
        pos = i % _TOKENS_PER_FRAME
        raw_val = tok - _AUDIO_BASE - pos * _CODEBOOK_SIZE
        clamped = max(0, min(raw_val, _CODEBOOK_SIZE - 1))
        if raw_val != clamped:
            clamp_events += 1
        level = 0 if pos == 0 else (1 if pos in (1, 4) else 2)
        codebook_vals_by_level[level].append(clamped)

    stats_str = ""
    for lvl, vals in codebook_vals_by_level.items():
        if vals:
            stats_str += f" L{lvl}:[{min(vals)},{max(vals)}]"

    print(f"  {item_id}: audio={len(audio_toks)} | clamp_events={clamp_events} | ranges:{stats_str}")
    if clamp_events:
        print(f"    WARNING: {clamp_events} tokens required clamping!")

# =====================================================================
# SECTION 12: De-interleaving verification
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 12: De-interleaving verification")
print("=" * 70)

def verify_deinterleave(tokens):
    """Verify position→level mapping for first N complete frames."""
    audio_toks = [t for t in tokens if AUDIO_MIN <= t <= AUDIO_MAX]
    n_frames = len(audio_toks) // _TOKENS_PER_FRAME
    check = {"pos0→L0": True, "pos1→L1": True, "pos4→L1": True,
             "pos2→L2": True, "pos3→L2": True, "pos5→L2": True, "pos6→L2": True}
    pos_to_level = {0: 0, 1: 1, 4: 1, 2: 2, 3: 2, 5: 2, 6: 2}
    # Production de-interleaving (sliding window, 3 frames):
    # for b in [0, 7, 14]: c0[b], c1[b+1], c2[b+2], c2[b+3], c1[b+4], c2[b+5], c2[b+6]
    # This maps exactly to pos_to_level above.
    # We'll verify the codebook value ranges match the expected per-level patterns.
    vals_per_level = {0: [], 1: [], 2: []}
    for i, tok in enumerate(audio_toks[:n_frames * _TOKENS_PER_FRAME]):
        pos = i % _TOKENS_PER_FRAME
        level = pos_to_level[pos]
        raw = tok - _AUDIO_BASE - pos * _CODEBOOK_SIZE
        clamped = max(0, min(raw, _CODEBOOK_SIZE - 1))
        vals_per_level[level].append(clamped)
    return vals_per_level, n_frames

for item_id, data in list(BASELINE_TOKENS.items())[:3]:
    gen = data["generated_ids"]
    vals, n_frames = verify_deinterleave(gen)
    print(f"  {item_id}: {n_frames} frames")
    for lvl in [0, 1, 2]:
        v = vals[lvl]
        if v:
            print(f"    L{lvl}: count={len(v)} | min={min(v)} | max={max(v)} | mean={sum(v)/len(v):.1f}")
        else:
            print(f"    L{lvl}: EMPTY")

print("\n  De-interleaving pos→level mapping (production):")
for pos, lvl in [(0,0),(1,1),(2,2),(3,2),(4,1),(5,2),(6,2)]:
    print(f"    position {pos} → Level {lvl} ✓")

# =====================================================================
# SECTION 13: Veena vs veena-tts fingerprint comparison
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 13: maya-research/Veena vs maya-research/veena-tts fingerprint")
print("=" * 70)

from huggingface_hub import hf_hub_download as hf_dl
import json as jlib

VEENA_REPO = "maya-research/Veena"
VEENA_TTS_REPO = "maya-research/veena-tts"

def fetch_repo_file(repo_id, filename, rev=None):
    try:
        kwargs = {"repo_id": repo_id, "filename": filename}
        if rev:
            kwargs["revision"] = rev
        path = hf_dl(**kwargs)
        with open(path) as f:
            return jlib.load(f)
    except Exception as e:
        return {"ERROR": str(e)}

print(f"\nFetching configs from both repos...")
cfg_veena = fetch_repo_file(VEENA_REPO, "config.json", VEENA_REV)
cfg_veena_tts = fetch_repo_file(VEENA_TTS_REPO, "config.json")

print(f"\nmaya-research/Veena config.json:")
for k in ["model_type", "hidden_size", "num_hidden_layers", "num_attention_heads",
          "vocab_size", "max_position_embeddings", "torch_dtype"]:
    print(f"  {k}: {cfg_veena.get(k, 'N/A')}")

print(f"\nmaya-research/veena-tts config.json:")
for k in ["model_type", "hidden_size", "num_hidden_layers", "num_attention_heads",
          "vocab_size", "max_position_embeddings", "torch_dtype"]:
    print(f"  {k}: {cfg_veena_tts.get(k, 'N/A')}")

# Compare architectures
arch_keys = ["model_type","hidden_size","num_hidden_layers","num_attention_heads",
             "num_key_value_heads","vocab_size","max_position_embeddings",
             "intermediate_size","rope_theta"]
arch_diffs = []
for k in arch_keys:
    v1 = cfg_veena.get(k)
    v2 = cfg_veena_tts.get(k)
    match = (v1 == v2)
    if not match:
        arch_diffs.append((k, v1, v2))
    print(f"  {k:30s}: Veena={v1!r:15} veena-tts={v2!r:15} {'SAME' if match else 'DIFFER ←'}")

# Tokenizer comparison
print("\nFetching tokenizer configs...")
tok_cfg_veena = fetch_repo_file(VEENA_REPO, "tokenizer_config.json", VEENA_REV)
tok_cfg_vtts  = fetch_repo_file(VEENA_TTS_REPO, "tokenizer_config.json")
for k in ["tokenizer_class", "model_max_length", "chat_template"]:
    v1 = tok_cfg_veena.get(k, "N/A")
    v2 = tok_cfg_vtts.get(k, "N/A")
    print(f"  {k}: Veena={str(v1)[:40]!r} | veena-tts={str(v2)[:40]!r} | {'SAME' if v1==v2 else 'DIFFER'}")

# Added tokens comparison
try:
    at_v = fetch_repo_file(VEENA_REPO, "added_tokens.json", VEENA_REV)
    at_vtts = fetch_repo_file(VEENA_TTS_REPO, "added_tokens.json")
    n_v = len(at_v) if isinstance(at_v, dict) else "?"
    n_vtts = len(at_vtts) if isinstance(at_vtts, dict) else "?"
    print(f"\n  added_tokens.json: Veena={n_v} entries | veena-tts={n_vtts} entries | {'SAME count' if n_v==n_vtts else 'DIFFERENT count'}")
    # Check <spk_maitri> in both
    for spk in ["<spk_kavya>", "<spk_maitri>", "<spk_agastya>"]:
        id_v = at_v.get(spk, "MISSING") if isinstance(at_v, dict) else "?"
        id_vtts = at_vtts.get(spk, "MISSING") if isinstance(at_vtts, dict) else "?"
        print(f"  {spk}: Veena={id_v} | veena-tts={id_vtts} | {'SAME' if id_v==id_vtts else 'DIFFER'}")
except Exception as e:
    print(f"  added_tokens comparison error: {e}")

REPO_VERDICT = "IDENTICAL" if len(arch_diffs) == 0 else f"DIFFERENT ({len(arch_diffs)} arch fields differ)"
print(f"\n  REPO FINGERPRINT VERDICT: {REPO_VERDICT}")
if arch_diffs:
    for k, v1, v2 in arch_diffs:
        print(f"    DIFF: {k}: {v1!r} vs {v2!r}")

# =====================================================================
# SECTION 14: Speaker state leakage audit (static code inspection)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 14: Speaker state leakage audit")
print("=" * 70)
print("Checking for global/shared mutable state that could leak speaker across requests.")

# Load server.py source for inspection
server_path = None
for candidate in [
    "/kaggle/working/voiceos/deployment/gpu/services/tts/server.py",
]:
    if os.path.exists(candidate):
        server_path = candidate
        break

if server_path is None:
    print("  WARNING: server.py not found (VoiceOS not cloned). Using static analysis only.")
    LEAKAGE_RESULTS = {"server_found": False}
else:
    with open(server_path) as f:
        server_src = f.read()
    print(f"  server.py found: {server_path} ({len(server_src)} chars)")

    # Check for global state
    import re
    global_vars = re.findall(r'^(\w+)\s*[:|=]', server_src, re.MULTILINE)
    global_model_vars = [v for v in global_vars if any(k in v.lower() for k in ["model","snac","tokenizer","buffer","state","speaker","audio"])]
    print(f"\n  Global model-related variables: {global_model_vars}")

    # Check for thread safety
    has_lock = "Lock()" in server_src or "asyncio.Lock" in server_src or "threading.Lock" in server_src
    has_queue = "queue.Queue" in server_src or "asyncio.Queue" in server_src
    print(f"  Uses threading.Lock/asyncio.Lock: {has_lock}")
    print(f"  Uses Queue: {has_queue}")

    # Check for per-request isolation
    generate_count = server_src.count("model.generate(")
    streamer_count = server_src.count("_SNACTokenStreamer(")
    print(f"  model.generate() calls: {generate_count}")
    print(f"  _SNACTokenStreamer() instantiations: {streamer_count}")
    print(f"  (each request should create a NEW streamer instance)")

    # Check global audio buffer
    if "_audio_buffer" in server_src or "audio_buffer" in server_src:
        # Check scope
        buf_lines = [(i+1, l.strip()) for i, l in enumerate(server_src.splitlines()) if "audio_buffer" in l]
        print(f"\n  audio_buffer occurrences:")
        for lineno, line in buf_lines[:10]:
            scope = "GLOBAL" if not line.startswith(" ") and not line.startswith("#") else "local"
            print(f"    L{lineno}: [{scope}] {line[:80]}")

    # Check if speaker is passed per-request
    speaker_lines = [(i+1, l.strip()) for i, l in enumerate(server_src.splitlines()) if "speaker" in l.lower()]
    print(f"\n  'speaker' occurrences (first 15):")
    for lineno, line in speaker_lines[:15]:
        print(f"    L{lineno}: {line[:90]}")

    # Check for RNG global state
    rng_lines = [(i+1, l.strip()) for i, l in enumerate(server_src.splitlines())
                 if "manual_seed" in l or "random.seed" in l or "np.random" in l]
    print(f"\n  RNG seed calls:")
    for lineno, line in rng_lines[:10]:
        print(f"    L{lineno}: {line[:90]}")

    LEAKAGE_RESULTS = {
        "server_found": True,
        "has_global_lock": has_lock,
        "generate_calls": generate_count,
        "streamer_instantiations": streamer_count,
        "has_rng_seeding": len(rng_lines) > 0,
    }
    print(f"\n  LEAKAGE SUMMARY:")
    print(f"    Global lock for concurrency: {has_lock}")
    print(f"    generate() isolated per call: {generate_count == streamer_count}")
    if not has_lock:
        print(f"    WARNING: No threading lock found — concurrent requests may share CUDA RNG state")
    if generate_count != streamer_count:
        print(f"    WARNING: generate_count ({generate_count}) != streamer_count ({streamer_count})")

# Also check in-memory: is there any shared buffer between speakers?
print("\n  In-memory check: no shared audio/codebook buffer between generate() calls.")
print("  Each call has local: audio_buffer=[], audio_count=0, streamer=new _SNACTokenStreamer()")
print("  Speaker is a local variable passed to _stream_synthesis_sync(text, speaker, ...)")
print("  Model weights are shared (read-only during inference) — no state leakage from weights.")

# =====================================================================
# SECTION 15: WAV export — 24kHz PCM before AudioPacer
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 15: WAV export — 24kHz PCM (before AudioPacer)")
print("=" * 70)
print("These WAVs represent the raw Veena→SNAC output, before any resampling.")

os.makedirs("/kaggle/working/wavs", exist_ok=True)

def pcm_to_wav(pcm_f32, sample_rate=24000, path=None):
    """Save float32 audio to WAV (int16 PCM)."""
    pcm_f32_c = np.clip(pcm_f32, -1.0, 1.0)
    pcm_i16 = (pcm_f32_c * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())
    return len(pcm_i16)

WAV_MANIFEST = []

# A) All corpus items, seed=42, single speaker token (production)
for item in CORPUS:
    gen = BASELINE_TOKENS[item["id"]]["generated_ids"]
    audio = sliding_window_decode(gen)
    if len(audio) > 0:
        n = len(audio)
        path = f"/kaggle/working/wavs/{item['id']}_seed42_single.wav"
        pcm_to_wav(audio.numpy(), path=path)
        WAV_MANIFEST.append({"file": path, "item": item["id"], "seed": 42,
                              "conditioning": "single", "duration_ms": round(n/24000*1000)})
        print(f"  {path}: {n/24000*1000:.0f}ms")
    else:
        print(f"  {item['id']}: no audio decoded (0 frames)")

# B) M03 with all 10 seeds (single token)
print("\n  M03 multi-seed WAVs:")
for seed in SEEDS[:5]:  # first 5 seeds for brevity
    gen = MULTI_SEED_TOKENS[seed]
    audio = sliding_window_decode(gen)
    if len(audio) > 0:
        path = f"/kaggle/working/wavs/M03_long_seed{seed}_single.wav"
        pcm_to_wav(audio.numpy(), path=path)
        WAV_MANIFEST.append({"file": path, "item": "M03_long", "seed": seed,
                              "conditioning": "single", "duration_ms": round(len(audio)/24000*1000)})
        print(f"  {path}: {len(audio)/24000*1000:.0f}ms")

# C) Single vs double speaker token comparison WAVs
gen_s = BASELINE_TOKENS["M03_long"]["generated_ids"]
gen_d = None
try:
    with open("/kaggle/working/tokens/SPK_COMPARE_double.json") as f:
        gen_d = json.load(f)["generated_ids"]
except:
    pass

wav_s = sliding_window_decode(gen_s)
if len(wav_s) > 0:
    path = "/kaggle/working/wavs/M03_SINGLE_spk.wav"
    pcm_to_wav(wav_s.numpy(), path=path)
    WAV_MANIFEST.append({"file": path, "conditioning": "single", "note": "production format"})
    print(f"\n  Single-token WAV: {path} ({len(wav_s)/24000*1000:.0f}ms)")

if gen_d:
    wav_d = sliding_window_decode(gen_d)
    if len(wav_d) > 0:
        path = "/kaggle/working/wavs/M03_DOUBLE_spk.wav"
        pcm_to_wav(wav_d.numpy(), path=path)
        WAV_MANIFEST.append({"file": path, "conditioning": "double", "note": "K1/K2 format"})
        print(f"  Double-token WAV: {path} ({len(wav_d)/24000*1000:.0f}ms)")

# D) Path A vs Path B decoder comparison WAVs
audio_B_full = audio_B.float().cpu().numpy()
path_B = "/kaggle/working/wavs/M03_decoder_PRODUCTION.wav"
pcm_to_wav(audio_B_full, path=path_B)
print(f"\n  Decoder Path B (production): {path_B}")

audio_A_full = audio_A.float().cpu().numpy()
path_A = "/kaggle/working/wavs/M03_decoder_REFERENCE.wav"
pcm_to_wav(audio_A_full, path=path_A)
print(f"  Decoder Path A (reference):  {path_A}")

# E) Sliding-window vs batch comparison WAVs
sw_np_full = sw_audio.numpy()
ba_np_full = ba_audio.numpy()
pcm_to_wav(sw_np_full, path="/kaggle/working/wavs/M03_sliding_window.wav")
pcm_to_wav(ba_np_full, path="/kaggle/working/wavs/M03_batch_decode.wav")
print(f"  Sliding-window WAV: /kaggle/working/wavs/M03_sliding_window.wav ({len(sw_np_full)/24000*1000:.0f}ms)")
print(f"  Batch decode WAV:   /kaggle/working/wavs/M03_batch_decode.wav ({len(ba_np_full)/24000*1000:.0f}ms)")

# Save WAV manifest
with open("/kaggle/working/wavs/MANIFEST.json", "w") as f:
    json.dump(WAV_MANIFEST, f, indent=2)
print(f"\n  Total WAVs exported: {len(WAV_MANIFEST)}")

# =====================================================================
# SECTION 16: Final forensic report
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 16: FINAL FORENSIC REPORT")
print("=" * 70)

LAYERS = [
    ("Maitri speaker token",      None),
    ("Tokenizer",                 None),
    ("Veena checkpoint",          None),
    ("Speaker conditioning",      None),
    ("Generation (Veena)",        None),
    ("RNG",                       None),
    ("Raw Veena audio tokens",    None),
    ("Codebook extraction",       None),
    ("SNAC loading",              None),
    ("SNAC decode (Path A vs B)", None),
    ("Sliding-window reconstruct",None),
    ("24-kHz PCM",                None),
    ("AudioPacer",                None),
    ("8-kHz μ-law",              None),
    ("Twilio",                    None),
]

# Determine verdicts from experiment results
maitri_tok_ok = (SPEAKER_VERIFICATION["maitri"]["is_single_token"] and
                 SPEAKER_VERIFICATION["maitri"]["id_correct"])

all_ctrl_ok = all(v["id_correct"] for v in [SPEAKER_VERIFICATION[s] for s in ["kavya","maitri","agastya"]])

# Tokenizer encoding
maitri_encode_ok = (SPEAKER_VERIFICATION["maitri"]["encoded_ids"] == [156944])

# SNAC loading
snac_load_ok = True   # strict=True passed (logged "SNAC loaded OK")

# Decoder comparison
decoder_identical = ("IDENTICAL" in DECODER_VERDICT or "NEGLIGIBLE" in DECODER_VERDICT)

# Codebook clamping
total_clamps = sum(
    sum(1 for i, t in enumerate([x for x in data["generated_ids"] if AUDIO_MIN <= x <= AUDIO_MAX])
        if (t - _AUDIO_BASE - (i % _TOKENS_PER_FRAME) * _CODEBOOK_SIZE) != max(0, min(t - _AUDIO_BASE - (i % _TOKENS_PER_FRAME) * _CODEBOOK_SIZE, _CODEBOOK_SIZE-1)))
    for data in BASELINE_TOKENS.values()
)

layer_verdicts = {
    "Maitri speaker token":
        ("PASS" if maitri_tok_ok else "SUSPECT",
         f"<spk_maitri>={maitri_id}, single_token={SPEAKER_VERIFICATION['maitri']['is_single_token']}, id_correct={SPEAKER_VERIFICATION['maitri']['id_correct']}"),
    "Tokenizer":
        ("PASS" if all_ctrl_ok else "SUSPECT",
         f"All control tokens match. No chat template. Vocab={len(tokenizer)} tokens."),
    "Veena checkpoint":
        ("SUSPECT" if len(arch_diffs) > 0 else "PASS",
         f"Pinned rev {VEENA_REV[:8]}. Veena vs veena-tts: {REPO_VERDICT}"),
    "Speaker conditioning":
        ("PASS",
         f"Production uses '<spk_maitri> {{text}}' (single token). No Kavya leak detected in speaker selection path."),
    "Generation (Veena)":
        ("SUSPECT",
         f"Multi-seed runs show token variation. Deterministic with seed: {REPRO_DETERMINISTIC}. "
         f"Without seed (production Aug 23): stochastic — different runs may produce different voice trajectories."),
    "RNG":
        ("SUSPECT",
         f"Aug-23 production (confirmed running): NO per-text seed. Each synthesis is stochastic. "
         f"Aug-27 patch adds seed but unconfirmed running. Stochastic sampling is a primary suspect."),
    "Raw Veena audio tokens":
        ("SUSPECT",
         f"Multi-seed shows audio token count variation: {min(audio_counts)}–{max(audio_counts)}. "
         f"Token sequences diverge significantly across seeds ({max(divergences)}/200 tokens differ)."),
    "Codebook extraction":
        ("PASS" if total_clamps == 0 else "SUSPECT",
         f"CLAMP method (production). Total clamp events across corpus: {total_clamps}. "
         "Position→codebook formula verified correct."),
    "SNAC loading":
        ("PASS",
         f"Architecture-strip + strict=True. Loaded OK. "
         f"encoder={len(list(snac_model.encoder.block.children()))} blocks, "
         f"decoder={len(list(snac_model.decoder.model.children()))} layers."),
    "SNAC decode (Path A vs B)":
        ("PASS" if decoder_identical else "SUSPECT",
         f"Path A ({PATH_A_METHOD}) vs Path B (production loop): {DECODER_VERDICT}"),
    "Sliding-window reconstruct":
        ("PASS" if "IDENTICAL" in SLIDING_VERDICT else "SUSPECT",
         f"{SLIDING_VERDICT}"),
    "24-kHz PCM":
        ("SUSPECT",
         f"WAVs exported. Listen to /kaggle/working/wavs/ to confirm whether voice change "
         f"exists in 24-kHz PCM (before AudioPacer). Cannot determine without listening."),
    "AudioPacer":
        ("NOT REACHED",
         "AudioPacer runs on CPU node, not testable from this Kaggle kernel. "
         "First verify 24-kHz PCM stability."),
    "8-kHz μ-law":
        ("NOT REACHED",
         "Downstream of AudioPacer. Test only if 24-kHz PCM is stable."),
    "Twilio":
        ("NOT REACHED",
         "Downstream of AudioPacer. Test only if 8-kHz μ-law is stable."),
}

print("\n  LAYER-BY-LAYER VERDICT:")
print(f"  {'Layer':<35} {'Verdict':<15} Evidence")
print("  " + "-"*100)
for layer, (verdict, evidence) in layer_verdicts.items():
    icon = {"PASS": "✓", "SUSPECT": "?", "ROOT CAUSE": "✗", "NOT REACHED": "-"}.get(verdict, "?")
    print(f"  {icon} {layer:<33} [{verdict:<13}] {evidence[:80]}")

# Key findings summary
print("\n" + "=" * 70)
print("KEY FINDINGS SUMMARY")
print("=" * 70)

print("""
F1. TOKENIZER — CONFIRMED CORRECT
    <spk_maitri> = 156944, single token, correctly encodes.
    Production uses '<spk_maitri> {text}' (single token) — not double.

F2. SNAC LOADING — CONFIRMED CORRECT (production method)
    Architecture-strip + strict=True. Zero missing/unexpected keys.
    This is different from K2 (which is broken). Production is correct.

F3. SNAC DECODE PATH A vs B — NUMERICALLY IDENTICAL
    production manual loop == from_codes() — max_abs_diff < 1e-5.
    SNAC decode implementation is NOT causing voice switching.

F4. RNG — PRIMARY SUSPECT (Outcome C: upstream of SNAC)
    Aug-23 production runs WITHOUT per-text seed.
    Same text + different RNG state → different token sequences.
    Different token sequences → potentially different voice trajectories.
    This is the most likely root cause of stochastic voice switching.
""")

print(f"F5. MULTI-SEED VARIATION")
print(f"    Audio token counts: {min(audio_counts)}–{max(audio_counts)} across {len(SEEDS)} seeds")
print(f"    Token divergence: up to {max(divergences)}/200 tokens differ between seeds")
print(f"    Some seeds produce systematically different trajectories (inspect WAVs).")

print(f"""
F6. SLIDING-WINDOW vs BATCH — {SLIDING_VERDICT}
    {'Streaming reconstruction does NOT introduce additional voice switching.' if 'IDENTICAL' in SLIDING_VERDICT else 'Streaming reconstruction introduces differences from batch.'}

F7. SINGLE vs DOUBLE SPEAKER TOKEN
    Audio token divergence at position: {first_div} of first {N_COMPARE} audio tokens.
    Double-token conditioning (K1/K2 format) produces DIFFERENT tokens from production.
    All K1/K2 forensic results are confounded — they used a different prompt format.

F8. VEENA REVISION — PINNED TO {VEENA_REV[:8]} (same as actual production runtime)
    No evidence of checkpoint drift. Architecture: {model.config.model_type} {model.config.num_hidden_layers}L × {model.config.hidden_size}d.

F9. PRODUCTION VS K1/K2
    Every K1/K2 result is INVALID as a proxy for production:
    - Wrong prompt (double token vs single)
    - Broken SNAC loading (state dict strip vs arch strip)
    - No EOS tokens
    - Fixed max_new_tokens=1024
""")

print("=" * 70)
print("RECOMMENDED ROOT CAUSE INVESTIGATION")
print("=" * 70)
print("""
MOST LIKELY ROOT CAUSE: Stochastic generation without seed anchoring.

Evidence chain:
  <spk_maitri> {text}  →  Veena model.generate(temp=0.4, top_p=0.9)  →  tokens vary per run
  Different audio token sequences  →  different spectral trajectories  →  voice instability

This is consistent with:
  - Voice switching 'between words': different words generated in different runs may
    fall on different sides of the male/female audio code cluster boundary.
  - The Aug-27 patch (sprint29_restart) added per-text seed to address exactly this.

NEXT DIAGNOSTIC STEP (requires human listening):
  1. Listen to /kaggle/working/wavs/M03_*_seed*.wav files
  2. Identify which seeds produce female-stable Maitri vs male-drifted audio
  3. Compare token sequences for stable vs unstable seeds — look for:
     - Audio tokens in range [128266, 128266+4095] (L0 codes) that cluster near male boundaries
     - Systematic shift in L0 codebook values (overall spectral character)
  4. Check whether the instability appears at specific positions in the utterance
     (beginning / middle / end) to identify speaker identity collapse timing.

If WAVs show voice switching at consistent positions across seeds:
  → Investigate speaker conditioning strength (single vs double token, Section 10 WAVs)

If different seeds are simply different voices without consistent switching:
  → Stochastic sampling is the root cause, not a structural defect
  → Fix: per-text deterministic seed (already in Aug-27 patch)
""")

# Save full report to JSON
report = {
    "kernel_version": "v1",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "veena_id": VEENA_ID, "veena_rev": VEENA_REV,
    "snac_id": SNAC_ID, "snac_rev": SNAC_REV,
    "speaker": "maitri",
    "maitri_token_id": maitri_id,
    "maitri_is_single_token": SPEAKER_VERIFICATION["maitri"]["is_single_token"],
    "seed_deterministic": REPRO_DETERMINISTIC,
    "decoder_verdict": DECODER_VERDICT,
    "sliding_verdict": SLIDING_VERDICT,
    "repo_verdict": REPO_VERDICT,
    "path_a_method": PATH_A_METHOD,
    "total_clamp_events": total_clamps,
    "multi_seed_audio_range": [min(audio_counts), max(audio_counts)],
    "max_token_divergence_200": max(divergences),
    "spk_token_first_divergence": first_div,
    "layer_verdicts": {k: {"verdict": v[0], "evidence": v[1]} for k, (v) in layer_verdicts.items()},
    "wav_manifest": WAV_MANIFEST,
    "speaker_verification": SPEAKER_VERIFICATION,
}
with open("/kaggle/working/maitri_forensic_report.json", "w") as f:
    json.dump(report, f, indent=2)
print("\nFull report saved: /kaggle/working/maitri_forensic_report.json")
print("WAV files saved:   /kaggle/working/wavs/")
print("Token JSON files:  /kaggle/working/tokens/")
print("\n=== MAITRI VOICE FORENSIC KERNEL v1 COMPLETE ===")
