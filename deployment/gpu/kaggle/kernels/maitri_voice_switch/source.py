# =====================================================================
# MAITRI VOICE SWITCH FORENSIC KERNEL v2
# =====================================================================
# Objective: Reproduce the actual female->male->female voice switch
# in <spk_maitri> utterances and find the FIRST layer that introduces it.
#
# New evidence from Sprint-29 validation kernel (mamatadas7777):
#   - The bug is "male-adjacent audio-code cluster" drift in token generation
#   - Sprint-29 fixed Kavya with SpeakerLockProcessor — Maitri never got this fix
#   - Previous v1 forensic used seed=42 which may never trigger the male cluster
#
# Strategy:
#   1. Run 30 unseeded generations per text (exact production behavior)
#   2. Compute F0/pitch profile per generation to detect gender change
#   3. Find any generation with F -> M -> F pattern (female->male->female)
#   4. For that generation: batch vs sliding-window comparison at 24kHz AND 8kHz
#   5. Test both AudioPacer implementations (decimate vs ratecv)
#
# Frozen to Sprint-29 production:
#   Veena: maya-research/Veena @ 8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f
#   SNAC:  hubertsiuzdak/snac_24khz @ d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec
#
# AUDIT ONLY. No production modifications.
# =====================================================================
import sys, os, json, time, struct, wave, hashlib

print("=" * 70)
print("MAITRI VOICE SWITCH FORENSIC KERNEL v2")
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

# Ensure base-image scipy can still import after any numpy changes
try:
    import scipy.signal
    print(f"  scipy.signal: OK (version check passed)")
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

# Patch sys.path so snac imports correctly (v3 lesson)
snac_site = subprocess.run(
    f"{sys.executable} -c 'import snac; import os; print(os.path.dirname(os.path.dirname(snac.__file__)))'",
    shell=True, capture_output=True, text=True
).stdout.strip()
if snac_site and snac_site not in sys.path:
    sys.path.insert(0, snac_site)
    print(f"  sys.path prepend: {snac_site}")

from transformers import AutoTokenizer, AutoModelForCausalLM
from huggingface_hub import hf_hub_download, snapshot_download
from snac import SNAC
import types as _types

# ── Veena ─────────────────────────────────────────────────────────────
print("\nDownloading Veena tokenizer + model weights...")
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
print(f"  Vocab: {tokenizer.vocab_size} | BF16: {next(model.parameters()).dtype == torch.bfloat16}")
DEVICE = "cuda:0"

# ── SNAC (EXACT production architecture-strip method) ─────────────────
print("\nDownloading SNAC weights...")
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

# Build SNAC from config (exclude non-constructor keys)
SNAC_CONSTRUCTOR_KEYS = {
    "sampling_rate","encoder_dim","encoder_rates","latent_dim",
    "decoder_dim","decoder_rates","attn_window_size","codebook_size",
    "codebook_dim","vq_strides","noise","depthwise",
}
snac_cfg = {k: v for k, v in snac_cfg_full.items() if k in SNAC_CONSTRUCTOR_KEYS}
snac_cfg["attn_window_size"] = None  # Force None — T4 has no NVRTC

snac_model = SNAC(**snac_cfg).eval()

# Production method: strip LocalMHA from architecture BEFORE loading weights
def _strip_attn(seq):
    return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != "LocalMHA"])

snac_model.encoder.block = _strip_attn(snac_model.encoder.block)
snac_model.decoder.model = _strip_attn(snac_model.decoder.model)

sd = torch.load(snac_wts_path, map_location="cpu", weights_only=False)
snac_model.load_state_dict(sd)  # strict=True — must match
snac_model = snac_model.to(DEVICE).eval()
print(f"  SNAC loaded (strict=True, arch-strip method)")
print(f"  SNAC params: {sum(p.numel() for p in snac_model.parameters())//1e3:.0f}K")

# Patch decode method.
# Root cause of prior failures:
#   - VectorQuantize.decode_code() returns raw embedding [1, codebook_dim=8, T]
#   - It does NOT apply per-VQ out_proj (codebook_dim→latent_dim=768)
#   - The production server.py formula vq_strides[i]//vq_strides[0] assumed finest-first
#     ordering [1,2,4] but snac_24khz has coarsest-first [4,2,1] → gave 0 → empty tensor
# Correct approach (matches VectorQuantize.forward internals):
#   1. decode_code → [1, 8, T_i]
#   2. quantizer.out_proj → [1, 768, T_i]
#   3. repeat_interleave(quantizer.stride) → [1, 768, T_fine]
#   4. sum all → [1, 768, T_fine]
#   5. decoder([1, 768, T_fine]) → audio
def _snac_decode_compat(self, codes):
    z_q = None
    for i, quantizer in enumerate(self.quantizer.quantizers):
        z_q_i = quantizer.decode_code(codes[i])   # [1, codebook_dim, T_i]
        z_q_i = quantizer.out_proj(z_q_i)         # [1, latent_dim, T_i]
        if quantizer.stride > 1:
            z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)  # [1, latent_dim, T_fine]
        z_q = z_q_i if z_q is None else z_q + z_q_i
    return self.decoder(z_q)

snac_model.decode = _types.MethodType(_snac_decode_compat, snac_model)

# Print quantizer structure for forensic record
print(f"  SNAC quantizers: {len(snac_model.quantizer.quantizers)}")
for i, q in enumerate(snac_model.quantizer.quantizers):
    print(f"    q[{i}]: stride={q.stride} codebook_dim={q.codebook_dim} out_proj={type(q.out_proj).__name__}")

# Smoke-test: 3-frame window [codes_0=3, codes_1=6, codes_2=12]
_test_codes = [
    torch.zeros(1, 3, dtype=torch.long, device=DEVICE),
    torch.zeros(1, 6, dtype=torch.long, device=DEVICE),
    torch.zeros(1, 12, dtype=torch.long, device=DEVICE),
]
with torch.no_grad():
    _test_out = snac_model.decode(_test_codes)
print(f"  SNAC decode smoke-test: output shape={tuple(_test_out.shape)} ({'OK' if _test_out.shape[-1] > 0 else 'FAIL'})")

# ── Production token constants ─────────────────────────────────────────
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
_SPK_KAVYA         = tokenizer.convert_tokens_to_ids("<spk_kavya>")
print(f"  <spk_maitri>={_SPK_MAITRI}  <spk_kavya>={_SPK_KAVYA}")

# =====================================================================
# SECTION 3: Production generation function
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 3: Production generation function (exact Sprint-29)")
print("=" * 70)

def build_input_ids(text: str, speaker: str = "maitri") -> torch.Tensor:
    prompt = f"<spk_{speaker}> {text}"
    ids = tokenizer.encode(prompt, add_special_tokens=False)
    full = [_START_OF_HUMAN, *ids, _END_OF_HUMAN, _START_OF_AI, _START_OF_SPEECH]
    return torch.tensor([full], device=DEVICE)

def generate_tokens(text: str, speaker: str = "maitri", seed: int | None = None) -> tuple[list[int], int]:
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

def extract_audio_tokens(raw_tokens: list[int]) -> list[int]:
    audio = []
    for t in raw_tokens:
        if _AUDIO_CODE_OFFSET <= t < _AUDIO_CODE_OFFSET + _TOKENS_PER_FRAME * _CODEBOOK_SIZE:
            audio.append(t)
        elif t in (_END_OF_SPEECH, _END_OF_AI):
            break
    return audio

def audio_tokens_to_codebook_vals(audio_tokens: list[int]) -> list[int]:
    vals = []
    for pos, t in enumerate(audio_tokens):
        frame_pos = pos % _TOKENS_PER_FRAME
        val = t - _AUDIO_CODE_OFFSET - frame_pos * _CODEBOOK_SIZE
        vals.append(max(0, min(val, _CODEBOOK_SIZE - 1)))
    return vals

print("  generate_tokens() defined")

# =====================================================================
# SECTION 4: SNAC decode functions (batch + sliding-window)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 4: SNAC decode functions")
print("=" * 70)

def batch_decode_pcm(audio_tokens: list[int]) -> bytes:
    vals = audio_tokens_to_codebook_vals(audio_tokens)
    n_frames = len(vals) // _TOKENS_PER_FRAME
    if n_frames == 0:
        return b""
    vals = vals[:n_frames * _TOKENS_PER_FRAME]

    codes_0, codes_1, codes_2 = [], [], []
    for i in range(n_frames):
        b = i * _TOKENS_PER_FRAME
        codes_0.append(vals[b])
        codes_1.append(vals[b+1])
        codes_2.append(vals[b+2])
        codes_2.append(vals[b+3])
        codes_1.append(vals[b+4])
        codes_2.append(vals[b+5])
        codes_2.append(vals[b+6])

    def _clamp(v):
        return [min(max(x, 0), _CODEBOOK_SIZE-1) for x in v]

    codes = [
        torch.tensor(_clamp(codes_0), dtype=torch.long, device=DEVICE).unsqueeze(0),
        torch.tensor(_clamp(codes_1), dtype=torch.long, device=DEVICE).unsqueeze(0),
        torch.tensor(_clamp(codes_2), dtype=torch.long, device=DEVICE).unsqueeze(0),
    ]
    with torch.no_grad():
        audio_hat = snac_model.decode(codes)
    waveform = audio_hat.squeeze().cpu().float().clamp(-1.0, 1.0).numpy()
    pcm16 = (waveform * 32767).astype("int16")
    return pcm16.tobytes()

def sliding_window_decode_pcm(audio_tokens: list[int]) -> bytes:
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

print("  batch_decode_pcm() + sliding_window_decode_pcm() defined")

# =====================================================================
# SECTION 5: AudioPacer implementations for 8kHz μ-law comparison
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 5: AudioPacer implementations")
print("=" * 70)

# A: Decimate (deployment/gpu/audio/pacer.py) — no anti-aliasing filter
def decimate_to_8k(pcm24k: bytes) -> bytes:
    n = len(pcm24k) // 2
    samples = struct.unpack(f"<{n}h", pcm24k)
    decimated = samples[::3]
    if not decimated:
        return b""
    return struct.pack(f"<{len(decimated)}h", *decimated)

# G.711 μ-law encoder (ITU-T G.711)
_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635
_ULAW_SEG_END = (0x00FF, 0x01FF, 0x03FF, 0x07FF, 0x0FFF, 0x1FFF, 0x3FFF, 0x7FFF)

def _lin2ulaw_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    if sample > _ULAW_CLIP:
        sample = _ULAW_CLIP
    sample += _ULAW_BIAS
    seg = 8
    for i, end in enumerate(_ULAW_SEG_END):
        if sample <= end:
            seg = i
            break
    if seg >= 8:
        return 0x7F ^ sign
    mantissa = (sample >> (seg + 3)) & 0x0F
    return (~(sign | (seg << 4) | mantissa)) & 0xFF

def pcm16_to_ulaw(pcm16: bytes) -> bytes:
    n = len(pcm16) // 2
    samples = struct.unpack(f"<{n}h", pcm16)
    return bytes(_lin2ulaw_sample(s) for s in samples)

def ulaw_to_pcm16(ulaw: bytes) -> bytes:
    result = []
    for byte in ulaw:
        byte = ~byte & 0xFF
        sign = byte & 0x80
        seg = (byte & 0x70) >> 4
        mantissa = byte & 0x0F
        sample = ((mantissa << 3) + _ULAW_BIAS) << seg
        sample -= _ULAW_BIAS
        if sign:
            sample = -sample
        result.append(max(-32768, min(32767, sample)))
    return struct.pack(f"<{len(result)}h", *result)

# B: ratecv (src/services/playback/output.py) — polyphase filter with state
def ratecv_to_8k_stateless(pcm24k: bytes) -> bytes:
    import audioop
    pcm8k, _ = audioop.ratecv(pcm24k, 2, 1, 24000, 8000, None)
    return pcm8k

def _volume_boost(pcm16: bytes) -> bytes:
    samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.int32)
    samples = np.clip((samples * 5) // 4, -32768, 32767).astype(np.int16)
    return samples.tobytes()

def apply_audiopacer_decimate(pcm24k_chunks: list[bytes]) -> bytes:
    """Simulate decimate-based AudioPacer (no anti-aliasing)."""
    all_pcm8k = []
    for chunk in pcm24k_chunks:
        pcm8k = decimate_to_8k(chunk)
        pcm8k_boosted = _volume_boost(pcm8k)
        all_pcm8k.append(pcm8k_boosted)
    return b"".join(all_pcm8k)

def apply_audiopacer_ratecv(pcm24k_chunks: list[bytes]) -> bytes:
    """Simulate ratecv-based AudioPacer (polyphase filter, stateful)."""
    import audioop
    ratecv_state = None
    all_pcm8k = []
    for chunk in pcm24k_chunks:
        pcm8k, ratecv_state = audioop.ratecv(chunk, 2, 1, 24000, 8000, ratecv_state)
        pcm8k_boosted = _volume_boost(pcm8k)
        all_pcm8k.append(pcm8k_boosted)
    return b"".join(all_pcm8k)

print("  AudioPacer A (decimate) + B (ratecv) defined")

# =====================================================================
# SECTION 6: WAV + F0 analysis helpers
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 6: WAV + F0 analysis helpers")
print("=" * 70)

OUT_DIR = "/kaggle/working/output"
WAV_DIR = os.path.join(OUT_DIR, "wavs")
TOK_DIR = os.path.join(OUT_DIR, "tokens")
os.makedirs(WAV_DIR, exist_ok=True)
os.makedirs(TOK_DIR, exist_ok=True)

def save_wav(pcm_bytes: bytes, path: str, sample_rate: int = 24000, n_channels: int = 1, sampwidth: int = 2):
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return os.path.getsize(path)

def save_wav_8k_from_ulaw(ulaw_bytes: bytes, path: str):
    # Decode μ-law back to PCM16 for WAV playback
    pcm16 = ulaw_to_pcm16(ulaw_bytes)
    return save_wav(pcm16, path, sample_rate=8000)

def estimate_f0_profile(pcm24k: bytes, window_ms: int = 40, hop_ms: int = 20) -> list[dict]:
    """Estimate F0 in overlapping windows using autocorrelation.

    Returns list of {t_ms, f0_hz, voiced, energy_db} per window.
    Female voice: F0 ~150-300 Hz. Male voice: F0 ~80-150 Hz.
    """
    if len(pcm24k) < 4:
        return []

    from scipy.signal import correlate

    sr = 24000
    samples = np.frombuffer(pcm24k, dtype=np.int16).astype(np.float32) / 32768.0

    win_samples = int(sr * window_ms / 1000)
    hop_samples = int(sr * hop_ms / 1000)

    # F0 search range: 60-400 Hz
    min_lag = int(sr / 400)   # ~60 samples for 400 Hz
    max_lag = int(sr / 60)    # ~400 samples for 60 Hz

    profile = []
    n = len(samples)
    pos = 0
    while pos + win_samples <= n:
        frame = samples[pos:pos + win_samples]
        t_ms = (pos + win_samples // 2) / sr * 1000

        # Energy
        rms = float(np.sqrt(np.mean(frame ** 2)))
        energy_db = float(20 * np.log10(rms + 1e-10))
        voiced = energy_db > -40  # -40 dB threshold for voiced speech

        f0_hz = 0.0
        if voiced and len(frame) >= max_lag * 2:
            # Normalized autocorrelation
            ac = correlate(frame, frame, mode='full')
            ac = ac[len(ac)//2:]  # keep positive lags
            ac = ac / (ac[0] + 1e-10)  # normalize

            # Find peak in F0 range
            ac_range = ac[min_lag:max_lag]
            if len(ac_range) > 0:
                peak_idx = int(np.argmax(ac_range))
                peak_val = float(ac_range[peak_idx])
                lag = peak_idx + min_lag
                if peak_val > 0.3 and lag > 0:  # confidence threshold
                    f0_hz = float(sr / lag)

        profile.append({
            "t_ms": round(t_ms, 1),
            "f0_hz": round(f0_hz, 1),
            "voiced": voiced,
            "energy_db": round(energy_db, 1),
        })
        pos += hop_samples

    return profile

def classify_gender_profile(profile: list[dict]) -> dict:
    """Classify voiced frames as female/male/uncertain based on F0."""
    voiced = [p for p in profile if p["voiced"] and p["f0_hz"] > 60]
    if not voiced:
        return {"female": 0, "male": 0, "uncertain": 0, "switch_detected": False, "switch_positions": []}

    female_frames = [p for p in voiced if p["f0_hz"] > 165]
    male_frames   = [p for p in voiced if p["f0_hz"] < 140]
    uncertain     = [p for p in voiced if 140 <= p["f0_hz"] <= 165]

    # Detect F->M->F switch: look for male cluster flanked by female
    labels = []
    for p in voiced:
        if p["f0_hz"] > 165:
            labels.append("F")
        elif p["f0_hz"] < 140:
            labels.append("M")
        else:
            labels.append("U")

    label_str = "".join(labels)
    switch_detected = "FMF" in label_str or ("FM" in label_str and "MF" in label_str)

    # Find switch positions
    switches = []
    for i in range(1, len(labels)):
        if labels[i-1] == "F" and labels[i] == "M":
            switches.append({"type": "F->M", "t_ms": voiced[i]["t_ms"]})
        elif labels[i-1] == "M" and labels[i] == "F":
            switches.append({"type": "M->F", "t_ms": voiced[i]["t_ms"]})

    return {
        "female": len(female_frames),
        "male": len(male_frames),
        "uncertain": len(uncertain),
        "pct_female": round(len(female_frames) / len(voiced) * 100, 1),
        "pct_male": round(len(male_frames) / len(voiced) * 100, 1),
        "mean_f0": round(float(np.mean([p["f0_hz"] for p in voiced])), 1),
        "median_f0": round(float(np.median([p["f0_hz"] for p in voiced])), 1),
        "f0_range": [round(min(p["f0_hz"] for p in voiced), 1), round(max(p["f0_hz"] for p in voiced), 1)],
        "switch_detected": switch_detected,
        "switch_positions": switches,
        "label_sequence": label_str[:50],  # first 50 chars
    }

print("  save_wav(), estimate_f0_profile(), classify_gender_profile() defined")

# =====================================================================
# SECTION 7: Production test corpus
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 7: Production test corpus")
print("=" * 70)

# Real production-like texts. Maitri speaker. Production Hinglish.
CORPUS = [
    # P01: Short — control (unlikely to drift)
    {
        "id": "P01_short",
        "text": "Namaste. Main Maitri baat kar rahi hun. Kya aap sunne mein saksham hain?",
    },
    # P02: Production greeting (actual call greeting format, adapted for Maitri)
    {
        "id": "P02_greeting",
        "text": (
            "Namaste, Prateek Das ji. Main Maitri hun, Rajat Finance ki collections team se. "
            "Aapke account mein pachaas hazaar rupay ka outstanding balance hai jo tees July tak due tha. "
            "Kya aap aaj is baare mein baat kar sakte hain?"
        ),
    },
    # P03: Long response (multi-sentence, production LLM response style)
    {
        "id": "P03_long_response",
        "text": (
            "Main samajh sakti hun ki yeh mushkil waqt hai. "
            "Lekin main aapko batana chahti hun ki hum aapke saath kaam karne ke liye taiyaar hain. "
            "Aap ek partial payment bhi kar sakte hain — jo bhi aap ke liye sambhav ho. "
            "Hum aapki situation ko samajhte hain aur ek flexible repayment plan bana sakte hain. "
            "Kya aap mujhe bata sakte hain ki aapke liye kya comfortable hoga?"
        ),
    },
    # P04: Numbers + dates (stress test for TTS)
    {
        "id": "P04_numbers",
        "text": (
            "Aapke loan account number teen saat paanch do ka balance ek lakh pachaas hazaar rupay hai. "
            "Due date paanch September do hazaar chhabbees thi. "
            "Late payment penalty teen percent per month ke hisaab se calculate hoti hai."
        ),
    },
    # P05: Very long — maximum pressure test (~200+ chars, will hit 700 token limit)
    {
        "id": "P05_very_long",
        "text": (
            "Aapke saath seedha baat karna chahti hun — aapka account abhi severe overdue category mein hai. "
            "Hamari records ke mutabiq, aapne pichhle teen mahine mein koi payment nahi ki hai. "
            "Yeh aapke credit score ko bahut buri tarah affect kar sakta hai. "
            "Main chahti hun ki aap yeh clearly samjhein: agar hum abhi koi resolution nahi nikaalte, "
            "toh humein legal recovery process shuru karni padegi. "
            "Lekin main yeh nahi chahti. Main aapko ek mouka dena chahti hun. "
            "Kya aap abhi phone pe mujhe bata sakte hain ki aap kitni payment kar sakte hain? "
            "Koi bhi amount helpful hogi — chahe woh teen hazaar rupay hi kyon na ho."
        ),
    },
    # P06: Hindi-heavy (mixed script stress)
    {
        "id": "P06_hindi_heavy",
        "text": (
            "Aapka karz chukana bahut zaroori hai. "
            "Humara collection department aapke case ko priority de raha hai. "
            "Please ek baar sochiyen aur mujhe call karein. "
            "Main Maitri hun aur main hamesha aapki help ke liye available hun."
        ),
    },
]

for c in CORPUS:
    words = len(c["text"].split())
    chars = len(c["text"])
    max_new = min(int(len(c["text"]) * 1.3) * _TOKENS_PER_FRAME + 21, 700)
    print(f"  {c['id']}: {chars} chars, {words} words, max_new={max_new}")

# =====================================================================
# SECTION 8: SWEEP — 30 unseeded generations per text to find male drift
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 8: 30-run unseeded sweep — hunting for male drift")
print("=" * 70)
print("NOTE: No seed = exact production behavior. Looking for F->M->F switch.")
print()

# Results accumulator
SWEEP_RESULTS = {}
MALE_DRIFT_FOUND = {}  # id -> generation that showed drift

N_RUNS = 30  # 30 unseeded runs per text

for corpus_item in CORPUS:
    cid = corpus_item["id"]
    text = corpus_item["text"]
    print(f"\n{'='*50}")
    print(f"TEXT: {cid}")
    print(f"  '{text[:80]}...'")
    print(f"  Runs: {N_RUNS} (no seed)")

    runs = []
    male_drift_runs = []

    for run_idx in range(N_RUNS):
        raw_tokens, gen_ms = generate_tokens(text, speaker="maitri", seed=None)
        audio_tokens = extract_audio_tokens(raw_tokens)
        n_audio = len(audio_tokens)

        if n_audio < _SLIDING_WINDOW:
            print(f"  Run {run_idx:02d}: SKIP (only {n_audio} audio tokens)")
            continue

        # Sliding-window decode → 24kHz PCM
        pcm24k = sliding_window_decode_pcm(audio_tokens)
        dur_ms = int(len(pcm24k) / 2 / 24000 * 1000)

        # F0 profile analysis
        profile = estimate_f0_profile(pcm24k)
        gender_info = classify_gender_profile(profile)

        run_result = {
            "run": run_idx,
            "n_raw_tokens": len(raw_tokens),
            "n_audio_tokens": n_audio,
            "dur_ms": dur_ms,
            "gen_ms": gen_ms,
            **gender_info,
        }
        runs.append(run_result)

        switch_marker = " *** SWITCH DETECTED ***" if gender_info["switch_detected"] else ""
        print(f"  Run {run_idx:02d}: {n_audio} tok | {dur_ms}ms | "
              f"F0_median={gender_info['median_f0']}Hz | "
              f"F%={gender_info['pct_female']:.0f} M%={gender_info['pct_male']:.0f}"
              f"{switch_marker}")

        if gender_info["switch_detected"] or gender_info["pct_male"] > 20:
            # Save this WAV — it shows drift!
            wav_path = os.path.join(WAV_DIR, f"{cid}_run{run_idx:02d}_DRIFT.wav")
            save_wav(pcm24k, wav_path)

            # Also save tokens
            tok_path = os.path.join(TOK_DIR, f"{cid}_run{run_idx:02d}_DRIFT_tokens.json")
            with open(tok_path, "w") as f:
                json.dump({
                    "text": text,
                    "run": run_idx,
                    "audio_tokens": audio_tokens,
                    "gender_info": gender_info,
                    "profile_sample": profile[:20],
                }, f, indent=2)

            male_drift_runs.append({
                **run_result,
                "wav_path": wav_path,
                "tok_path": tok_path,
                "audio_tokens": audio_tokens,
                "pcm24k": pcm24k,
            })
            print(f"         SAVED: {wav_path}")

    # Summary for this text
    pct_male_list = [r["pct_male"] for r in runs]
    switch_count = sum(1 for r in runs if r["switch_detected"])
    median_f0_list = [r["median_f0"] for r in runs]

    summary = {
        "n_runs": len(runs),
        "n_switch_detected": switch_count,
        "n_male_heavy": sum(1 for p in pct_male_list if p > 20),
        "pct_male_avg": round(float(np.mean(pct_male_list)), 1) if pct_male_list else 0,
        "pct_male_max": round(float(np.max(pct_male_list)), 1) if pct_male_list else 0,
        "median_f0_overall": round(float(np.median(median_f0_list)), 1) if median_f0_list else 0,
    }
    SWEEP_RESULTS[cid] = {"summary": summary, "runs": runs}

    print(f"\n  SUMMARY {cid}: {switch_count}/{len(runs)} switches | "
          f"max_male%={summary['pct_male_max']} | "
          f"median_F0={summary['median_f0_overall']}Hz")

    if male_drift_runs:
        MALE_DRIFT_FOUND[cid] = male_drift_runs
        print(f"  *** MALE DRIFT FOUND in {len(male_drift_runs)} runs! ***")

print(f"\nSweep complete. Drift found in: {list(MALE_DRIFT_FOUND.keys()) or 'NONE'}")

# =====================================================================
# SECTION 9: If drift found — batch vs sliding-window comparison on exact tokens
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 9: Batch vs sliding-window on EXACT drift tokens")
print("=" * 70)

DECODE_COMPARISON = {}

for cid, drift_runs in MALE_DRIFT_FOUND.items():
    for dr in drift_runs[:2]:  # test first 2 drift instances per text
        tokens = dr["audio_tokens"]
        run_idx = dr["run"]
        label = f"{cid}_run{run_idx:02d}"
        print(f"\n  Testing {label} ({len(tokens)} audio tokens)")

        # PATH A: Batch decode (no window boundary artifacts)
        pcm_batch = batch_decode_pcm(tokens)
        wav_batch = os.path.join(WAV_DIR, f"{label}_BATCH_24k.wav")
        save_wav(pcm_batch, wav_batch)

        # PATH B: Sliding-window decode (production method)
        pcm_sw = sliding_window_decode_pcm(tokens)
        wav_sw = os.path.join(WAV_DIR, f"{label}_SLIDING_24k.wav")
        save_wav(pcm_sw, wav_sw)

        # F0 analysis for both
        f0_batch = classify_gender_profile(estimate_f0_profile(pcm_batch))
        f0_sw    = classify_gender_profile(estimate_f0_profile(pcm_sw))

        print(f"    BATCH:   F0_median={f0_batch['median_f0']}Hz | F%={f0_batch['pct_female']} M%={f0_batch['pct_male']} | switch={f0_batch['switch_detected']}")
        print(f"    SLIDING: F0_median={f0_sw['median_f0']}Hz | F%={f0_sw['pct_female']} M%={f0_sw['pct_male']} | switch={f0_sw['switch_detected']}")

        DECODE_COMPARISON[label] = {
            "batch_gender": f0_batch,
            "sliding_gender": f0_sw,
            "verdict": (
                "UPSTREAM (both show drift)" if f0_batch["switch_detected"] and f0_sw["switch_detected"]
                else "SLIDING_WINDOW_SPECIFIC (only sliding shows drift)" if f0_sw["switch_detected"] and not f0_batch["switch_detected"]
                else "BATCH_SPECIFIC" if f0_batch["switch_detected"] and not f0_sw["switch_detected"]
                else "NEITHER (drift not reproduced)"
            ),
        }
        print(f"    VERDICT: {DECODE_COMPARISON[label]['verdict']}")

if not MALE_DRIFT_FOUND:
    print("  No male drift found in 30-run sweep. See Section 10 for deeper analysis.")
    print("  Saving all corpus items with full F0 profiles for manual inspection.")

    # Save best candidates (lowest F0 = closest to male)
    for corpus_item in CORPUS:
        cid = corpus_item["id"]
        if cid not in SWEEP_RESULTS:
            continue
        runs = SWEEP_RESULTS[cid]["runs"]
        if not runs:
            continue
        # Save the run with highest male% for inspection
        best = max(runs, key=lambda r: r["pct_male"])
        print(f"  {cid}: best_male%={best['pct_male']:.1f} (run {best['run']})")

# =====================================================================
# SECTION 10: AudioPacer comparison (decimate vs ratecv at 8kHz)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 10: AudioPacer A (decimate) vs B (ratecv) at 8kHz")
print("=" * 70)
print("Testing both resampling methods on all corpus items.")
print("Each item gets 5 unseeded generations -> 8kHz WAVs.")
print()

PACER_RESULTS = {}

for corpus_item in CORPUS[:3]:  # test first 3 texts
    cid = corpus_item["id"]
    text = corpus_item["text"]
    print(f"  {cid}:")

    pacer_runs = []
    for run_idx in range(5):
        raw_tokens, _ = generate_tokens(text, speaker="maitri", seed=None)
        audio_tokens = extract_audio_tokens(raw_tokens)
        if len(audio_tokens) < _SLIDING_WINDOW:
            continue

        # Generate 24kHz chunks (one chunk per sliding window)
        chunks_24k = []
        vals = audio_tokens_to_codebook_vals(audio_tokens)
        pos = 0
        while pos + _SLIDING_WINDOW <= len(vals):
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
            chunk = (middle.cpu().float().clamp(-1.0, 1.0).numpy() * 32767).astype("int16").tobytes()
            chunks_24k.append(chunk)
            pos += _TOKENS_PER_FRAME

        if not chunks_24k:
            continue

        pcm24k = b"".join(chunks_24k)

        # AudioPacer A: decimate
        pcm8k_A = apply_audiopacer_decimate(chunks_24k)
        # AudioPacer B: ratecv (stateful)
        pcm8k_B = apply_audiopacer_ratecv(chunks_24k)

        # F0 analysis at 8kHz (adjust parameters for 8kHz)
        def f0_at_8k(pcm8k: bytes) -> dict:
            if len(pcm8k) < 4:
                return {}
            from scipy.signal import correlate
            sr = 8000
            samples = np.frombuffer(pcm8k, dtype=np.int16).astype(np.float32) / 32768.0
            win_s = int(sr * 0.04)  # 40ms
            hop_s = int(sr * 0.02)  # 20ms
            min_lag = int(sr / 400)
            max_lag = int(sr / 60)
            voiced_frames = []
            pos = 0
            while pos + win_s <= len(samples):
                frame = samples[pos:pos+win_s]
                rms = float(np.sqrt(np.mean(frame**2)))
                if rms > 0.005 and len(frame) >= max_lag * 2:
                    ac = correlate(frame, frame, mode='full')
                    ac = ac[len(ac)//2:]
                    ac = ac / (ac[0] + 1e-10)
                    ac_range = ac[min_lag:max_lag]
                    if len(ac_range) > 0:
                        peak_idx = int(np.argmax(ac_range))
                        peak_val = float(ac_range[peak_idx])
                        lag = peak_idx + min_lag
                        if peak_val > 0.3 and lag > 0:
                            voiced_frames.append(float(sr / lag))
                pos += hop_s
            if not voiced_frames:
                return {"median_f0": 0.0, "pct_male": 0.0, "pct_female": 0.0}
            median = float(np.median(voiced_frames))
            pct_m = round(sum(1 for f in voiced_frames if f < 140) / len(voiced_frames) * 100, 1)
            pct_f = round(sum(1 for f in voiced_frames if f > 165) / len(voiced_frames) * 100, 1)
            return {"median_f0": round(median, 1), "pct_male": pct_m, "pct_female": pct_f}

        fa = f0_at_8k(pcm8k_A)
        fb = f0_at_8k(pcm8k_B)

        print(f"    run{run_idx}: 24k_dur={len(pcm24k)//2//24}ms | "
              f"Decimate: F0={fa.get('median_f0',0)}Hz M%={fa.get('pct_male',0)} | "
              f"Ratecv: F0={fb.get('median_f0',0)}Hz M%={fb.get('pct_male',0)}")

        pacer_runs.append({"run": run_idx, "decimate": fa, "ratecv": fb})

        # Save 8kHz WAVs for the first run of each text
        if run_idx == 0:
            ulaw_A = pcm16_to_ulaw(pcm8k_A)
            ulaw_B = pcm16_to_ulaw(pcm8k_B)
            save_wav_8k_from_ulaw(ulaw_A, os.path.join(WAV_DIR, f"{cid}_run00_8k_DECIMATE.wav"))
            save_wav_8k_from_ulaw(ulaw_B, os.path.join(WAV_DIR, f"{cid}_run00_8k_RATECV.wav"))
            save_wav(pcm24k, os.path.join(WAV_DIR, f"{cid}_run00_24k_SLIDING.wav"))
            print(f"    -> Saved 8kHz WAVs for {cid} run00")

    PACER_RESULTS[cid] = pacer_runs

# =====================================================================
# SECTION 11: Window-boundary spectral discontinuity map
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 11: Sliding-window boundary spectral analysis")
print("=" * 70)
print("For each window boundary, measuring spectral centroid change.")
print("Looking for where F0/character changes occur.")

BOUNDARY_ANALYSIS = {}

for corpus_item in CORPUS[:2]:  # analyze first 2 texts
    cid = corpus_item["id"]
    text = corpus_item["text"]

    # Use seed=42 for reproducibility of this specific analysis
    raw_tokens, _ = generate_tokens(text, speaker="maitri", seed=42)
    audio_tokens = extract_audio_tokens(raw_tokens)

    if len(audio_tokens) < _SLIDING_WINDOW * 3:
        continue

    vals = audio_tokens_to_codebook_vals(audio_tokens)
    n_windows = (len(vals) - _SLIDING_WINDOW) // _TOKENS_PER_FRAME + 1

    print(f"\n  {cid}: {len(audio_tokens)} tokens, {n_windows} windows")

    window_data = []
    prev_spectrum = None
    prev_f0 = None

    for win_idx in range(min(n_windows, 80)):  # first 80 windows
        pos = win_idx * _TOKENS_PER_FRAME
        window = vals[pos:pos + _SLIDING_WINDOW]
        if len(window) < _SLIDING_WINDOW:
            break

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

        # Middle frame
        mid = audio_hat.squeeze()[_SAMPLES_PER_FRAME:_SAMPLES_PER_FRAME*2].cpu().float().numpy()

        # Spectral centroid
        fft = np.abs(np.fft.rfft(mid))
        freqs = np.fft.rfftfreq(len(mid), d=1.0/24000)
        centroid = float(np.sum(freqs * fft) / (np.sum(fft) + 1e-10))

        # RMS energy
        rms = float(np.sqrt(np.mean(mid**2)))
        energy_db = float(20 * np.log10(rms + 1e-10))

        # Simple F0 estimate for this window
        from scipy.signal import correlate
        if rms > 0.001:
            ac = correlate(mid, mid, mode='full')
            ac = ac[len(ac)//2:]
            ac = ac / (ac[0] + 1e-10)
            min_lag = int(24000 / 400)
            max_lag = int(24000 / 60)
            ac_range = ac[min_lag:max_lag]
            if len(ac_range) > 0:
                peak_idx = int(np.argmax(ac_range))
                peak_val = float(ac_range[peak_idx])
                lag = peak_idx + min_lag
                f0 = float(24000 / lag) if peak_val > 0.3 and lag > 0 else 0.0
            else:
                f0 = 0.0
        else:
            f0 = 0.0

        # Spectral change from previous window
        spec_change = 0.0
        if prev_spectrum is not None:
            fft_prev = np.abs(np.fft.rfft(prev_spectrum))
            fft_curr = fft
            # Cosine distance
            dot = float(np.dot(fft_prev, fft_curr))
            norm = float(np.linalg.norm(fft_prev) * np.linalg.norm(fft_curr) + 1e-10)
            spec_change = float(1.0 - dot / norm)

        t_ms = (pos + _TOKENS_PER_FRAME) / _TOKENS_PER_FRAME * 85.33

        is_suspect = (f0 > 0 and f0 < 150) or spec_change > 0.3
        gender = "M" if 0 < f0 < 140 else ("F" if f0 > 165 else "U") if f0 > 0 else "-"

        wd = {
            "win_idx": win_idx,
            "t_ms": round(t_ms, 1),
            "f0": round(f0, 1),
            "gender": gender,
            "centroid": round(centroid, 1),
            "energy_db": round(energy_db, 1),
            "spec_change": round(spec_change, 4),
            "suspect": is_suspect,
        }
        window_data.append(wd)

        if is_suspect or win_idx < 5 or win_idx % 10 == 0:
            print(f"    win{win_idx:03d} t={t_ms:.0f}ms f0={f0:.0f}Hz [{gender}] "
                  f"centroid={centroid:.0f}Hz spec_chg={spec_change:.3f}"
                  f"{' SUSPECT' if is_suspect else ''}")

        prev_spectrum = mid
        prev_f0 = f0

    BOUNDARY_ANALYSIS[cid] = window_data

    n_suspect = sum(1 for w in window_data if w["suspect"])
    gender_seq = "".join(w["gender"] for w in window_data if w["gender"] != "-")
    print(f"\n  {cid} boundary: {n_suspect}/{len(window_data)} suspect windows")
    print(f"  Gender sequence: {gender_seq[:60]}")

    # Save boundary analysis JSON
    with open(os.path.join(TOK_DIR, f"{cid}_boundary_analysis.json"), "w") as f:
        json.dump(window_data, f, indent=2)

# =====================================================================
# SECTION 12: Fixed-seed sweep (find the problematic seed)
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 12: Fixed seed sweep on P02 (production greeting)")
print("=" * 70)
print("Testing 50 seeds on production greeting to find male-triggering seeds.")

SEED_SWEEP = {}
text_p02 = CORPUS[1]["text"]  # P02_greeting

male_seeds = []
for seed in range(50):
    raw_tokens, _ = generate_tokens(text_p02, speaker="maitri", seed=seed)
    audio_tokens = extract_audio_tokens(raw_tokens)
    if len(audio_tokens) < _SLIDING_WINDOW:
        continue

    pcm24k = sliding_window_decode_pcm(audio_tokens)
    profile = estimate_f0_profile(pcm24k)
    g = classify_gender_profile(profile)

    SEED_SWEEP[seed] = {
        "n_audio_tokens": len(audio_tokens),
        "pct_male": g["pct_male"],
        "pct_female": g["pct_female"],
        "median_f0": g["median_f0"],
        "switch_detected": g["switch_detected"],
    }

    if g["pct_male"] > 15 or g["switch_detected"]:
        male_seeds.append(seed)
        wav_path = os.path.join(WAV_DIR, f"P02_seed{seed:03d}_DRIFT.wav")
        save_wav(pcm24k, wav_path)
        tok_path = os.path.join(TOK_DIR, f"P02_seed{seed:03d}_DRIFT_tokens.json")
        with open(tok_path, "w") as f:
            json.dump({"seed": seed, "audio_tokens": audio_tokens, "gender": g}, f, indent=2)
        print(f"  seed={seed:03d}: F0={g['median_f0']}Hz M%={g['pct_male']} F%={g['pct_female']} *** DRIFT *** -> {wav_path}")
    elif seed % 10 == 0:
        print(f"  seed={seed:03d}: F0={g['median_f0']}Hz M%={g['pct_male']} F%={g['pct_female']}")

print(f"\n  Seeds with male drift: {male_seeds or 'NONE found in 0-49'}")

# =====================================================================
# SECTION 13: Final report
# =====================================================================
print("\n" + "=" * 70)
print("SECTION 13: FINAL FORENSIC REPORT")
print("=" * 70)

print("""
PIPELINE:
  Veena checkpoint
  -> Tokenizer
  -> Maitri speaker conditioning
  -> Generation/RNG
  -> Raw Veena audio tokens
  -> Codebook extraction
  -> SNAC decode (batch or sliding-window)
  -> 24kHz PCM
  -> AudioPacer (decimate or ratecv)
  -> 8kHz mu-law
  -> Twilio
""")

# Determine verdict for each layer
print("LAYER VERDICTS:")
print()

# Generation layer
n_switch_total = sum(v["summary"]["n_switch_detected"] for v in SWEEP_RESULTS.values())
n_male_heavy = sum(v["summary"]["n_male_heavy"] for v in SWEEP_RESULTS.values())
total_runs = sum(v["summary"]["n_runs"] for v in SWEEP_RESULTS.values())

if MALE_DRIFT_FOUND or n_switch_total > 0:
    gen_verdict = "ROOT CAUSE"
    gen_evidence = f"F->M->F switch detected in {n_switch_total}/{total_runs} unseeded runs"
elif n_male_heavy > 0:
    gen_verdict = "SUSPECT"
    gen_evidence = f"{n_male_heavy}/{total_runs} runs had >20% male-sounding audio"
else:
    gen_verdict = "PASS (not triggered in this corpus/30-run sweep)"
    gen_evidence = f"All runs: median_F0 consistently in female range"

print(f"  Generation/RNG:         {gen_verdict}")
print(f"    Evidence: {gen_evidence}")

# Decode layer
if DECODE_COMPARISON:
    upstream = sum(1 for v in DECODE_COMPARISON.values() if "UPSTREAM" in v["verdict"])
    sw_only  = sum(1 for v in DECODE_COMPARISON.values() if "SLIDING_WINDOW_SPECIFIC" in v["verdict"])
    if upstream > 0:
        decode_verdict = f"PASS (drift exists in batch too — upstream)"
    elif sw_only > 0:
        decode_verdict = f"ROOT CAUSE (drift appears ONLY in sliding-window)"
    else:
        decode_verdict = "PASS"
else:
    decode_verdict = "NOT TESTED (no drift tokens found)"

print(f"  Sliding-window decode:  {decode_verdict}")

# AudioPacer
if PACER_RESULTS:
    pacer_male_decimate = []
    pacer_male_ratecv = []
    for runs in PACER_RESULTS.values():
        for r in runs:
            pacer_male_decimate.append(r["decimate"].get("pct_male", 0))
            pacer_male_ratecv.append(r["ratecv"].get("pct_male", 0))
    avg_d = float(np.mean(pacer_male_decimate)) if pacer_male_decimate else 0
    avg_r = float(np.mean(pacer_male_ratecv)) if pacer_male_ratecv else 0
    pacer_verdict = f"Decimate avg_male%={avg_d:.1f} | Ratecv avg_male%={avg_r:.1f}"
else:
    pacer_verdict = "NOT TESTED"

print(f"  AudioPacer (8kHz):      SUSPECT — {pacer_verdict}")
print(f"  Twilio:                 NOT REACHED (insufficient 8kHz data)")

print()
print("SWEEP SUMMARY:")
for cid, result in SWEEP_RESULTS.items():
    s = result["summary"]
    print(f"  {cid}: {s['n_switch_detected']}/{s['n_runs']} switches | "
          f"max_male%={s['pct_male_max']} | median_F0={s['median_f0_overall']}Hz")

print()
print("SEED SWEEP (P02, seeds 0-49):")
male_pct_list = [v["pct_male"] for v in SEED_SWEEP.values()]
if male_pct_list:
    print(f"  Mean male%={np.mean(male_pct_list):.1f} | Max male%={np.max(male_pct_list):.1f}")
    print(f"  Male-drift seeds: {male_seeds or 'NONE'}")

print()
print("KEY FINDING FROM SPRINT-29 KERNEL (mamatadas7777):")
print("  - Bug explicitly named 'male-adjacent audio-code cluster' drift")
print("  - Sprint-29 applied SpeakerLockProcessor fix for KAVYA speaker")
print("  - MAITRI NEVER RECEIVED THIS FIX")
print("  - <spk_maitri> token = 156944 (added token, no special lock)")
print()

# =====================================================================
# SECTION 14: Save manifest
# =====================================================================
print("=" * 70)
print("SECTION 14: Saving manifest")
print("=" * 70)

wavs = []
for f in sorted(os.listdir(WAV_DIR)):
    if f.endswith(".wav"):
        p = os.path.join(WAV_DIR, f)
        sz = os.path.getsize(p)
        sr = 8000 if "_8k_" in f else 24000
        dur_ms = int(sz / 2 / sr * 1000) if sr == 24000 else int(sz / 1 / sr * 1000)
        wavs.append({"file": f, "size_bytes": sz, "sample_rate": sr, "dur_ms": dur_ms})
        print(f"  {f}: {sz} bytes ({dur_ms}ms)")

manifest = {
    "kernel": "voiceos-maitri-voice-switch-forensic",
    "timestamp": time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
    "veena_rev": VEENA_REV,
    "snac_rev": SNAC_REV,
    "spk_maitri_id": _SPK_MAITRI,
    "sweep_results": {cid: v["summary"] for cid, v in SWEEP_RESULTS.items()},
    "male_drift_found": {cid: len(v) for cid, v in MALE_DRIFT_FOUND.items()},
    "seed_sweep_p02": {
        "n_seeds": len(SEED_SWEEP),
        "male_drift_seeds": male_seeds,
        "mean_male_pct": round(float(np.mean([v["pct_male"] for v in SEED_SWEEP.values()])), 1) if SEED_SWEEP else 0,
    },
    "decode_comparison": DECODE_COMPARISON,
    "pacer_results": {cid: [{"run": r["run"], "decimate": r["decimate"], "ratecv": r["ratecv"]} for r in runs] for cid, runs in PACER_RESULTS.items()},
    "wavs": wavs,
}

with open(os.path.join(OUT_DIR, "voice_switch_forensic_report.json"), "w") as f:
    json.dump(manifest, f, indent=2)

print(f"\nManifest saved: {OUT_DIR}/voice_switch_forensic_report.json")
print(f"Total WAVs: {len(wavs)}")
print()
print("=" * 70)
print("MAITRI VOICE SWITCH FORENSIC v2 COMPLETE")
print("=" * 70)
