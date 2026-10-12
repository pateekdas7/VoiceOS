#!/usr/bin/env python3
"""
L4 forensic script — Veena kavya drift investigation.

Runs the X4-A/X4-B forensic corpus on an RTX L4 (SM 8.6 Ampere) to give
an INDEPENDENT NVIDIA comparison point to the T4 (SM 7.5 Turing) baseline.

L4 is NOT an L4. Do not label these results as L4.

Test matrix (same seed=42, same sampler, same corpus, same tokenizer):
  RUN 1  L4 BF16     current code (SNAC patched)   SDPA attention
  RUN 2  L4 FP16     current code (SNAC patched)   SDPA attention
  RUN 3  L4 BF16     current code (SNAC patched)   EAGER attention
  RUN 4  L4 BF16     L4-reconstructed code         SDPA attention  (no SNAC patches)
  RUN 5  L4 FP32     current code (SNAC patched)   SDPA attention  (only if time permits)

For every text × precision:
  - Full generated sequence (int64 token ids)
  - First 21 audio-token positions: top-5 ids, top-5 logits, top-5 probs, top1-top2 margin, chosen rank
  - Full audio-code stream (list of ints in codebook)
  - SNAC-decoded PCM16LE audio + SHA-256[:16] fingerprint
  - Autocorrelation-based F0 classification (female-kavya / ambiguous / male-drift)
  - Determinism: 3 reps × critical indices (2, 3, 15) so we can prove
    per-run byte-identical output when GPU RNG is properly seeded.

Speaker embedding fingerprint (X4-A recreation) captured once per model load:
  - Norm of every speaker embedding
  - Cosine-to-kavya matrix

Environment snapshot per run:
  - torch/cuda/cudnn/transformers/snac versions
  - GPU capability, BF16 support flag
  - TF32 flags, matmul precision, deterministic-algorithms flag
  - Model param dtype/device counts + specific layer dtypes
  - Attention implementation actually resolved
  - Model config _commit_hash (proves revision pin worked)

Hard rules honoured:
  - No production/CPU/Kaggle/.env/git/Twilio side effects.
  - No speaker-token repetition, no temperature workaround, no SNAC blacklist,
    no rejection sampling, no phrase substitution, no warm-up text/audio.
  - Diagnostic only.

Output: /root/l4_forensic_results.json (or ./l4_forensic_results.json)
"""

import argparse
import gc
import hashlib
import json
import os
import subprocess
import sys
import time
import types
from typing import Any, Dict, List, Optional, Tuple

RESULTS_PATH = os.environ.get("L4_RESULTS", "/root/l4_forensic_results.json")
MODEL_ID = "maya-research/Veena"
# Pin to the exact Veena commit fingerprinted in X4-A/X4-B — same weights the T4
# baseline used, so any behavioural delta cannot be blamed on a weight change.
MODEL_REVISION = "8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f"
SNAC_ID = "hubertsiuzdak/snac_24khz"
SEED = 42

# ---- Veena token constants ----
START_OF_HUMAN = 128259
END_OF_HUMAN = 128260
START_OF_AI = 128261
END_OF_AI = 128262
START_OF_SPEECH = 128257
END_OF_SPEECH = 128258
AUDIO_BASE = 128266
CODEBOOK_SIZE = 4096
TOKENS_PER_FRAME = 7
SNAC_MIN = AUDIO_BASE
SNAC_MAX = AUDIO_BASE + TOKENS_PER_FRAME * CODEBOOK_SIZE - 1
SR = 24000

# ---- Corpus (identical to X4-A / X4-B) ----
CORPUS: List[Tuple[str, str]] = [
    ("drift",    "Aapke bank se transfer complete ho gaya hai."),
    ("drift",    "Sir kya aap UPI se payment karna prefer karenge?"),
    ("drift",    "Total outstanding 24,568 rupees hai as of aaj."),
    ("drift",    "Principal amount 15,000 rupees baaki hai."),
    ("stable",   "Namaste sir, main Kavya bol rahi hoon Rajat Finance se."),
    ("stable",   "Namaste madam, main Kavya bol rahi hoon."),
    ("stable",   "Namaste sir aap kaise hain aaj?"),
    ("stable",   "Dhanyavaad sir, aapke response ka intezaar rahega."),
    ("stable",   "Dhanyavaad, aapki payment successful ho gayi hai."),
    ("stable",   "Sir, aapke account par 12,500 rupees ka outstanding hai."),
    ("stable",   "Aap kaise hain?"),
    ("stable",   "Kripa karke wait karein."),
    ("english",  "Good morning, this is a test message."),
    ("english",  "Your account balance is one thousand five hundred rupees."),
    ("num_heavy","9,876,543 rupees ka total amount pending hai."),
    ("min_pair", "Total outstanding hai as of aaj."),
    ("min_pair", "Total outstanding amount check kar rahi hoon."),
    ("min_pair", "Aapke bank se successful transaction huwa hai."),
    ("min_pair", "Bank transfer ho chuka hai sir, dhyaan dijiye."),
]
DET_IDX = [2, 3, 15]  # two known male-drift texts + one female minimal-pair — same as X4-B

SPEAKERS = [
    "kavya", "apsara", "agastya", "vinaya", "maitri",
    "charu", "ishana", "kyra", "mohini", "varun", "soumya",
]


# =============================================================================
# Environment bootstrap — install ONLY what's needed for forensic; no vLLM/whisper
# =============================================================================

def _pip_install(pkg: str) -> None:
    print(f"  pip install {pkg}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])


def bootstrap() -> None:
    # Prefer to match the current T4 Kaggle stack so results are comparable.
    # If L4 host already has newer/older versions we DO NOT force downgrade
    # blindly — but we DO install missing packages.
    # Match X4-B T4 BF16 baseline env exactly (transformers 5.0.0, snac 1.0.0)
    # so any behavioural delta is attributable to hardware, not stack drift.
    need = {
        "transformers": "5.0.0",
        "huggingface_hub": None,
        "accelerate": None,
        "snac": "1.0.0",
        "numpy": None,
        "safetensors": None,
    }
    for pkg, ver in need.items():
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            _pip_install(f"{pkg}=={ver}" if ver else pkg)


bootstrap()

import numpy as np                # noqa: E402
import torch                      # noqa: E402
import transformers               # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402


# =============================================================================
# Env snapshot
# =============================================================================

def env_snapshot(tag: str) -> Dict[str, Any]:
    return {
        "tag": tag,
        "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "transformers": transformers.__version__,
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_cap": list(torch.cuda.get_device_capability(0)),
        "gpu_count": torch.cuda.device_count(),
        "gpu_total_mem_gb": torch.cuda.get_device_properties(0).total_memory / 1e9,
        "bf16_supported": torch.cuda.is_bf16_supported(),
        "fp16_supported": True,
        "tf32_matmul_allow": torch.backends.cuda.matmul.allow_tf32,
        "tf32_cudnn_allow": torch.backends.cudnn.allow_tf32,
        "matmul_precision": torch.get_float32_matmul_precision(),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "cudnn_benchmark": torch.backends.cudnn.benchmark,
        "cudnn_deterministic": torch.backends.cudnn.deterministic,
    }


# =============================================================================
# SNAC loader — two flavours: "patched" (current T4 code) and "unpatched" (L4)
# =============================================================================

def load_snac_patched(device: str = "cuda") -> Any:
    """Mirror current Sprint-29 patched T4 SNAC init exactly."""
    from snac import SNAC
    from huggingface_hub import hf_hub_download
    import snac.layers as snac_layers

    cfg_path = hf_hub_download(repo_id=SNAC_ID, filename="config.json")
    wts_path = hf_hub_download(repo_id=SNAC_ID, filename="pytorch_model.bin")
    with open(cfg_path) as f:
        cfg = json.load(f)
    model = SNAC(**cfg)

    def _strip_attn(seq):
        return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != "LocalMHA"])

    model.encoder.block = _strip_attn(model.encoder.block)
    model.decoder.model = _strip_attn(model.decoder.model)
    state = torch.load(wts_path, map_location="cpu", weights_only=False)
    model.load_state_dict(state, strict=False)
    model.eval()
    model = model.to(device)

    def _snac_decode_compat(self, codes):
        z_q = 0
        for quantizer, code in zip(self.quantizer.quantizers, codes):
            z_q_i = quantizer.decode_code(code)
            z_q_i = quantizer.out_proj(z_q_i)
            if quantizer.stride > 1:
                z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)
            z_q = z_q + z_q_i
        return self.decoder(z_q)

    model.decode = types.MethodType(_snac_decode_compat, model)

    # Snake Python fallback (matches T4 patch; on L4 with NVRTC it slightly
    # slows SNAC but produces the same math as the CUDA kernel, so results
    # remain comparable to T4).
    def _snake_plain(x, alpha):
        shape = x.shape
        x = x.reshape(shape[0], shape[1], -1)
        x = x + (alpha + 1e-9).reciprocal() * torch.sin(alpha * x).pow(2)
        x = x.reshape(shape)
        return x

    snac_layers.snake = _snake_plain
    return model


def load_snac_unpatched(device: str = "cuda") -> Any:
    """L4-era plain load: SNAC.from_pretrained(...).to(cuda).eval() — no patches."""
    from snac import SNAC
    m = SNAC.from_pretrained(SNAC_ID).to(device)
    m.eval()
    return m


# =============================================================================
# F0 + SNAC-decode helpers (identical to X4-A/X4-B)
# =============================================================================

def f0_frame(frame, sr=SR, fmin=70, fmax=400):
    frame = frame.astype(np.float32) - frame.mean()
    if np.sqrt((frame * frame).mean()) < 300:
        return 0.0
    corr = np.correlate(frame, frame, mode="full")
    corr = corr[len(corr) // 2:]
    corr = corr / (corr[0] + 1e-9)
    lag_min = sr // fmax
    lag_max = sr // fmin
    seg = corr[lag_min:lag_max]
    if len(seg) == 0:
        return 0.0
    peak = int(np.argmax(seg)) + lag_min
    if corr[peak] < 0.30:
        return 0.0
    return sr / peak


def analyze(pcm_i16: np.ndarray) -> Dict[str, Any]:
    n = len(pcm_i16)
    WIN = 480
    HOP = 240
    if n < WIN:
        return {"class": "silent", "median_f0": None, "p05_f0": None, "f0": []}
    nf = 1 + (n - WIN) // HOP
    x = pcm_i16.astype(np.float32)
    f0s = []
    f0_traj = []
    for i in range(nf):
        f = f0_frame(x[i * HOP:i * HOP + WIN])
        f0_traj.append(round(f, 1))
        if f > 0:
            f0s.append(f)
    if not f0s:
        return {"class": "silent", "median_f0": None, "p05_f0": None, "f0": f0_traj}
    a = np.array(f0s)
    med = float(np.median(a))
    p05 = float(np.percentile(a, 5))
    cls = "female-kavya" if (med >= 180 and p05 >= 140) else (
        "male-drift" if (med < 165 or p05 < 120) else "ambiguous"
    )
    return {"class": cls, "median_f0": med, "p05_f0": p05, "f0": f0_traj}


def decode_snac_audio(snac_model: Any, audio_ids_int: List[int], device: str) -> np.ndarray:
    if len(audio_ids_int) < 7:
        return np.zeros(0, dtype=np.int16)
    n_frames = len(audio_ids_int) // 7
    audio_ids_int = audio_ids_int[: n_frames * 7]
    codes_l0, codes_l1, codes_l2 = [], [], []
    for f in range(n_frames):
        b = f * 7
        codes_l0.append(audio_ids_int[b + 0])
        codes_l1.append(audio_ids_int[b + 1]); codes_l1.append(audio_ids_int[b + 4])
        codes_l2.append(audio_ids_int[b + 2]); codes_l2.append(audio_ids_int[b + 3])
        codes_l2.append(audio_ids_int[b + 5]); codes_l2.append(audio_ids_int[b + 6])
    with torch.no_grad():
        c0 = torch.tensor([codes_l0], device=device, dtype=torch.long)
        c1 = torch.tensor([codes_l1], device=device, dtype=torch.long)
        c2 = torch.tensor([codes_l2], device=device, dtype=torch.long)
        wav = snac_model.decode([c0, c1, c2]).squeeze().float().cpu().numpy()
    pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)
    return pcm


def audio_ids_from_generated(seq_ids, prompt_len):
    tail = seq_ids[prompt_len:]
    aud, pos = [], 0
    for tid in tail:
        if SNAC_MIN <= tid <= SNAC_MAX:
            p = pos % TOKENS_PER_FRAME
            v = tid - AUDIO_BASE - p * CODEBOOK_SIZE
            aud.append(max(0, min(v, CODEBOOK_SIZE - 1)))
            pos += 1
    return aud


def build_input(tokenizer, text: str, device: str = "cuda", speaker: str = "kavya"):
    prompt = f"<spk_{speaker}> {text}"
    pids = tokenizer.encode(prompt, add_special_tokens=False)
    ids = [START_OF_HUMAN, *pids, END_OF_HUMAN, START_OF_AI, START_OF_SPEECH]
    return torch.tensor([ids], device=device), ids


# =============================================================================
# Core capture (with per-position top-5 + margin + chosen rank)
# =============================================================================

def capture_generation(model, tokenizer, text: str, seed: int = SEED,
                       device: str = "cuda") -> Dict[str, Any]:
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    input_ids, ids_list = build_input(tokenizer, text, device=device)
    prompt_len = input_ids.shape[1]
    max_new = min(int(len(text) * 1.3) * TOKENS_PER_FRAME + 21, 700)

    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else END_OF_SPEECH
    with torch.no_grad():
        out = model.generate(
            input_ids,
            max_new_tokens=max_new,
            do_sample=True,
            temperature=0.4,
            top_p=0.9,
            repetition_penalty=1.05,
            pad_token_id=pad,
            eos_token_id=[END_OF_SPEECH, END_OF_AI],
            output_scores=True,
            return_dict_in_generate=True,
        )
    seq = out.sequences[0].tolist()
    gen_tail = seq[prompt_len:]
    scores = out.scores
    per_pos = []
    K = 5
    audio_count_running = 0
    for i, s in enumerate(scores):
        logits = s[0].float()
        probs = torch.softmax(logits, dim=-1)
        topv, topi = torch.topk(logits, K)
        chosen = int(gen_tail[i]) if i < len(gen_tail) else -1
        top1_logit = float(topv[0].item())
        top2_logit = float(topv[1].item())
        margin = top1_logit - top2_logit
        chosen_rank = -1
        for r, tid in enumerate(topi.tolist()):
            if tid == chosen:
                chosen_rank = r
                break
        entry = {
            "pos": i,
            "chosen": chosen,
            "chosen_rank_in_top5": chosen_rank,
            "chosen_prob": float(probs[chosen].item()) if 0 <= chosen < probs.shape[0] else None,
            "top5_ids": [int(x) for x in topi.tolist()],
            "top5_logits": [float(x) for x in topv.tolist()],
            "top5_probs": [float(probs[int(t)].item()) for t in topi.tolist()],
            "top1_top2_margin": margin,
            "is_audio": (SNAC_MIN <= chosen <= SNAC_MAX),
            "codebook_pos": None,
            "codebook_val": None,
        }
        if entry["is_audio"]:
            cb_pos = audio_count_running % TOKENS_PER_FRAME
            cb_val = chosen - AUDIO_BASE - cb_pos * CODEBOOK_SIZE
            entry["codebook_pos"] = cb_pos
            entry["codebook_val"] = max(0, min(cb_val, CODEBOOK_SIZE - 1))
            audio_count_running += 1
        per_pos.append(entry)
    return {"seq": seq, "prompt_len": prompt_len, "per_pos": per_pos}


# =============================================================================
# Speaker embed diagnostic (X4-A recreation)
# =============================================================================

def spk_embed_analysis(model, tokenizer) -> Dict[str, Any]:
    spk_ids = {s: tokenizer.encode(f"<spk_{s}>", add_special_tokens=False)
               for s in SPEAKERS}
    emb = model.get_input_embeddings().weight.detach()
    out = {"ids": spk_ids, "embed_dtype": str(emb.dtype),
           "norms": {}, "cos_to_kavya": {}}
    for s in SPEAKERS:
        if len(spk_ids[s]) != 1:
            out["norms"][s] = None
            continue
        v = emb[spk_ids[s][0]].float()
        out["norms"][s] = float(v.norm().item())
    kv = emb[spk_ids["kavya"][0]].float()
    kv_n = kv / (kv.norm() + 1e-9)
    for s in SPEAKERS:
        if len(spk_ids[s]) != 1:
            continue
        v = emb[spk_ids[s][0]].float()
        v_n = v / (v.norm() + 1e-9)
        out["cos_to_kavya"][s] = float((kv_n * v_n).sum().item())
    # Also compute full 11x11 cosine matrix for hidden clusters
    mtx = {}
    for a in SPEAKERS:
        if len(spk_ids[a]) != 1:
            continue
        va = emb[spk_ids[a][0]].float()
        va_n = va / (va.norm() + 1e-9)
        row = {}
        for b in SPEAKERS:
            if len(spk_ids[b]) != 1:
                continue
            vb = emb[spk_ids[b][0]].float()
            vb_n = vb / (vb.norm() + 1e-9)
            row[b] = float((va_n * vb_n).sum().item())
        mtx[a] = row
    out["cos_matrix"] = mtx
    return out


# =============================================================================
# Full run for one configuration
# =============================================================================

def run_config(tag: str, dtype: Any, attn_impl: str,
               snac_flavour: str, device: str = "cuda",
               tokenizer=None, snac_model=None,
               ) -> Dict[str, Any]:
    """Load Veena at (dtype, attn_impl), reuse tokenizer + SNAC across calls."""
    print(f"\n{'='*70}\n=== {tag}: dtype={dtype} attn={attn_impl} snac={snac_flavour}\n{'='*70}")
    # Precision knobs: leave TF32 default (matches production); highest matmul
    torch.set_float32_matmul_precision("highest")

    env = env_snapshot(tag)
    env["dtype_arg"] = str(dtype)
    env["attn_impl_arg"] = attn_impl
    env["snac_flavour"] = snac_flavour

    t0 = time.time()
    kwargs = {
        "torch_dtype": dtype,
        "device_map": device,
        "revision": MODEL_REVISION,
        "attn_implementation": attn_impl,
    }
    print(f"from_pretrained kwargs: {kwargs}")
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, **kwargs)
    model.eval()
    env["load_seconds"] = time.time() - t0
    env["model_config_dtype"] = str(model.dtype)
    env["model_num_params"] = sum(p.numel() for p in model.parameters())
    env["model_commit_hash"] = getattr(model.config, "_commit_hash", None)
    env["attn_impl_resolved"] = str(getattr(model.config, "_attn_implementation", None))

    dtypes: Dict[str, int] = {}
    for _, p in model.named_parameters():
        d = str(p.dtype)
        dtypes[d] = dtypes.get(d, 0) + 1
    env["param_dtype_counts"] = dtypes

    dev_counts: Dict[str, int] = {}
    for _, p in model.named_parameters():
        d = str(p.device)
        dev_counts[d] = dev_counts.get(d, 0) + 1
    env["param_device_counts"] = dev_counts

    sample_layers = {}
    for name in ["model.embed_tokens.weight", "model.norm.weight", "lm_head.weight"]:
        for n, p in model.named_parameters():
            if n == name:
                sample_layers[n] = {"dtype": str(p.dtype), "device": str(p.device),
                                    "shape": list(p.shape)}
                break
    env["sample_layer_dtypes"] = sample_layers
    print(f"load={env['load_seconds']:.1f}s dtype={model.dtype} "
          f"params={env['model_num_params']:,} attn={env['attn_impl_resolved']} "
          f"commit={env['model_commit_hash']}")

    spk_a = spk_embed_analysis(model, tokenizer)
    print(f"embed_dtype={spk_a['embed_dtype']} "
          f"cos_to_kavya_max_other="
          f"{max(v for k, v in spk_a['cos_to_kavya'].items() if k != 'kavya'):.4f}")

    records: List[Dict[str, Any]] = []
    for idx, (bucket, text) in enumerate(CORPUS):
        tt = time.time()
        cap = capture_generation(model, tokenizer, text, seed=SEED, device=device)
        latency = time.time() - tt
        aud_vals = audio_ids_from_generated(cap["seq"], cap["prompt_len"])
        pcm = decode_snac_audio(snac_model, aud_vals, device=device) \
            if len(aud_vals) >= 21 else np.zeros(0, dtype=np.int16)
        ana = analyze(pcm)
        first_21 = [p for p in cap["per_pos"] if p["is_audio"]][:21]
        rec = {
            "idx": idx, "bucket": bucket, "text": text,
            "prompt_len": cap["prompt_len"],
            "n_gen": len(cap["per_pos"]),
            "n_audio": sum(1 for p in cap["per_pos"] if p["is_audio"]),
            "first_21_audio_positions": first_21,
            "audio_ids_full": aud_vals,
            "audio_sha16": hashlib.sha256(bytes(pcm)).hexdigest()[:16],
            "audio_seconds": len(pcm) / SR,
            "class": ana["class"],
            "median_f0": ana["median_f0"],
            "p05_f0": ana["p05_f0"],
            "latency_s": latency,
        }
        records.append(rec)
        first5 = [p["chosen"] for p in first_21][:5]
        print(f"[{tag}][{idx:2d}][{bucket:9s}] "
              f"cls={ana['class']:12s} med={ana['median_f0']} "
              f"p05={ana['p05_f0']} sha={rec['audio_sha16']} "
              f"lat={latency:.1f}s first5={first5}")

    # Determinism
    det = []
    for di in DET_IDX:
        text = CORPUS[di][1]
        reps = []
        for r in range(3):
            cap = capture_generation(model, tokenizer, text, seed=SEED, device=device)
            aud_vals = audio_ids_from_generated(cap["seq"], cap["prompt_len"])
            pcm = decode_snac_audio(snac_model, aud_vals, device=device) \
                if len(aud_vals) >= 21 else np.zeros(0, dtype=np.int16)
            ana = analyze(pcm)
            f21 = [p["chosen"] for p in cap["per_pos"] if p["is_audio"]][:21]
            reps.append({
                "rep": r,
                "audio_sha16": hashlib.sha256(bytes(pcm)).hexdigest()[:16],
                "first21_audio_ids": f21,
                "class": ana["class"],
                "median_f0": ana["median_f0"],
                "p05_f0": ana["p05_f0"],
            })
        det.append({
            "idx": di, "text": text,
            "unique_sha16": len({r["audio_sha16"] for r in reps}),
            "unique_first21_audio": len({tuple(r["first21_audio_ids"]) for r in reps}),
            "reps": reps,
        })
        print(f"[{tag}][det][{di}] "
              f"unique_sha={det[-1]['unique_sha16']} "
              f"unique_first21={det[-1]['unique_first21_audio']} "
              f"classes={[r['class'] for r in reps]}")

    del model
    gc.collect()
    torch.cuda.empty_cache()

    return {"env": env, "spk_embed_analysis": spk_a,
            "records": records, "determinism": det}


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fp32", action="store_true",
                        help="Skip L4 FP32 run (already known FP32 doesn't help on T4).")
    parser.add_argument("--skip-fp16", action="store_true", default=False)
    parser.add_argument("--skip-eager", action="store_true", default=False)
    parser.add_argument("--skip-l4-code", action="store_true", default=False,
                        help="Skip L4-reconstructed code path (unpatched SNAC).")
    parser.add_argument("--output", default=RESULTS_PATH)
    args = parser.parse_args()

    print(f"=== L4 FORENSIC START {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"host: {os.uname().nodename}")
    subprocess.run(["nvidia-smi"], check=False)

    print("\n=== Loading tokenizer (shared) ...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    print(f"tokenizer vocab={tokenizer.vocab_size} pad={tokenizer.pad_token_id}")

    print("\n=== Loading SNAC (patched flavour — matches current T4 code) ...")
    snac_patched = load_snac_patched(device="cuda")

    print("\n=== Loading SNAC (unpatched flavour — matches L4 code) ...")
    try:
        snac_unpatched = load_snac_unpatched(device="cuda")
        snac_unpatched_ok = True
    except Exception as e:
        print(f"WARN: unpatched SNAC load failed on L4: {e}")
        snac_unpatched = None
        snac_unpatched_ok = False

    results: Dict[str, Any] = {
        "run_meta": {
            "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": os.uname().nodename,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "snac_id": SNAC_ID,
            "seed": SEED,
            "corpus": CORPUS,
            "det_idx": DET_IDX,
        },
        "runs": {},
    }

    # RUN 1 — L4 BF16, current code (SNAC patched), SDPA
    try:
        results["runs"]["L4_BF16_SDPA_current"] = run_config(
            "L4_BF16_SDPA_current", torch.bfloat16, "sdpa", "patched",
            tokenizer=tokenizer, snac_model=snac_patched,
        )
    except Exception as e:
        import traceback
        results["runs"]["L4_BF16_SDPA_current"] = {"error": str(e),
                                                       "traceback": traceback.format_exc()}
        print(f"RUN 1 FAILED: {e}")

    # RUN 2 — L4 FP16, current code (SNAC patched), SDPA
    if not args.skip_fp16:
        try:
            results["runs"]["L4_FP16_SDPA_current"] = run_config(
                "L4_FP16_SDPA_current", torch.float16, "sdpa", "patched",
                tokenizer=tokenizer, snac_model=snac_patched,
            )
        except Exception as e:
            import traceback
            results["runs"]["L4_FP16_SDPA_current"] = {"error": str(e),
                                                           "traceback": traceback.format_exc()}
            print(f"RUN 2 FAILED: {e}")

    # RUN 3 — L4 BF16, current code, EAGER attention
    if not args.skip_eager:
        try:
            results["runs"]["L4_BF16_EAGER_current"] = run_config(
                "L4_BF16_EAGER_current", torch.bfloat16, "eager", "patched",
                tokenizer=tokenizer, snac_model=snac_patched,
            )
        except Exception as e:
            import traceback
            results["runs"]["L4_BF16_EAGER_current"] = {"error": str(e),
                                                            "traceback": traceback.format_exc()}
            print(f"RUN 3 FAILED: {e}")

    # RUN 4 — L4 BF16, L4-reconstructed code (SNAC unpatched), SDPA
    if not args.skip_l4_code and snac_unpatched_ok:
        try:
            results["runs"]["L4_BF16_SDPA_L4code"] = run_config(
                "L4_BF16_SDPA_L4code", torch.bfloat16, "sdpa", "unpatched",
                tokenizer=tokenizer, snac_model=snac_unpatched,
            )
        except Exception as e:
            import traceback
            results["runs"]["L4_BF16_SDPA_L4code"] = {"error": str(e),
                                                          "traceback": traceback.format_exc()}
            print(f"RUN 4 FAILED: {e}")

    # RUN 5 — L4 FP32, current code, SDPA (nice to have)
    if not args.skip_fp32:
        try:
            results["runs"]["L4_FP32_SDPA_current"] = run_config(
                "L4_FP32_SDPA_current", torch.float32, "sdpa", "patched",
                tokenizer=tokenizer, snac_model=snac_patched,
            )
        except Exception as e:
            import traceback
            results["runs"]["L4_FP32_SDPA_current"] = {"error": str(e),
                                                           "traceback": traceback.format_exc()}
            print(f"RUN 5 FAILED: {e}")

    results["run_meta"]["end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with open(args.output, "w") as f:
        json.dump(results, f, indent=1,
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print(f"\nWROTE {args.output}, size={os.path.getsize(args.output)} bytes")
    print(f"=== L4 FORENSIC DONE {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")


if __name__ == "__main__":
    main()
