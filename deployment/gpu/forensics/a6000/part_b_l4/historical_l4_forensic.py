#!/usr/bin/env python3
"""
HISTORICAL L4 FORENSIC — Runs the Sprint-028 (commit 1ece78b) TTS implementation
on an actual rented NVIDIA L4, using the closest-defensible historical stack.

Faithful reproduction of the historical code path:
  - AutoTokenizer / AutoModelForCausalLM with NO revision pin (HEAD of Veena)
  - torch_dtype=torch.bfloat16, device_map="cuda"
  - SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda")  — UNPATCHED
  - do_sample=True, temperature=0.4, top_p=0.9, repetition_penalty=1.05
  - pad_token_id = tokenizer.pad_token_id or 128258
  - eos_token_id = [128258, 128262]
  - Prompt: f"<spk_{speaker}> {text}" + [128259, *ids, 128260, 128261, 128257]
  - NO manual_seed anywhere (historical had none)
  - Warm-up call: "hello" / "kavya"

Runs TWO protocols on the same corpus:
  Protocol A — historical faithful: NO per-text seed. corpus once + 3 reps on DET_IDX.
  Protocol B — seeded per-text (seed=42) for token-divergence parity vs current L4.

Also captures:
  - env fingerprint (GPU, driver, CUDA, cuDNN, torch, transformers, snac, model rev)
  - speaker embedding cosine matrix
  - per-position top-5 logits + margins + chosen rank + codebook decoding
  - full audio_ids for first-token-divergence audit
  - determinism unique_sha over 3 reps per DET_IDX

Writes /home/ubuntu/historical_l4_results.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple


MODEL_ID = "maya-research/Veena"
# NOTE: historical L4 code had NO revision pin. For our forensic we still record
# the resolved commit_hash after load, but we DO NOT pass revision= to remain
# faithful to the historical behaviour.
SNAC_ID = "hubertsiuzdak/snac_24khz"
SEED = 42  # only used for Protocol B

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
DET_IDX = [2, 3, 15]

SPEAKERS = [
    "kavya", "apsara", "agastya", "vinaya", "maitri",
    "charu", "ishana", "kyra", "mohini", "varun", "soumya",
]


# ---------------------------------------------------------------------------
# Environment bootstrap.
# Historical L4 pre-dated Sprint-029's transformers==5.12.1 pin. Exact
# transformers version at L4 Run F is UNKNOWN. We use transformers==5.0.0
# (same as current-code L4 run) so that any behavioural delta is attributable
# to the *code path* (unpatched SNAC, no per-text seed) rather than to a
# transformers stack change.
# For SNAC we try snac==0.1.0 first (documented as the "initial" version used
# on L4 before commit 080eac6). If that build is missing or incompatible with
# the current hubertsiuzdak/snac_24khz checkpoint shapes, we fall back to
# snac==1.0.0 (documented as post-080eac6). Which version historical L4
# actually ran is UNKNOWN per PART B — we record which one we ended up using.
# ---------------------------------------------------------------------------

def _pip(pkg: str) -> None:
    print(f"  pip install {pkg}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])


def bootstrap() -> Dict[str, Any]:
    stack_notes: Dict[str, Any] = {}
    need = {
        "transformers": "5.0.0",
        "huggingface_hub": None,
        "accelerate": None,
        "numpy": None,
        "safetensors": None,
    }
    for pkg, ver in need.items():
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            _pip(f"{pkg}=={ver}" if ver else pkg)

    # Try snac==0.1.0 (historical-earliest). If import fails or install fails,
    # fall back to 1.0.0.
    snac_ver_used = None
    try:
        import snac  # noqa: F401
        snac_ver_used = getattr(snac, "__version__", "unknown")
        stack_notes["snac_preinstalled"] = snac_ver_used
    except ImportError:
        for candidate in ("0.1.0", "1.0.0"):
            try:
                _pip(f"snac=={candidate}")
                import snac  # noqa: F811
                snac_ver_used = getattr(snac, "__version__", candidate)
                stack_notes["snac_install_choice"] = candidate
                break
            except Exception as e:
                stack_notes.setdefault("snac_install_errors", []).append(
                    f"{candidate}: {e}"
                )
    stack_notes["snac_ver_used"] = snac_ver_used
    return stack_notes


STACK_NOTES = bootstrap()

import numpy as np                # noqa: E402
import torch                      # noqa: E402
import transformers               # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402


def env_snapshot(tag: str, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    snap = {
        "tag": tag,
        "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "transformers": transformers.__version__,
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_cap": list(torch.cuda.get_device_capability(0)),
        "gpu_count": torch.cuda.device_count(),
        "gpu_total_mem_gb": torch.cuda.get_device_properties(0).total_memory / 1e9,
        "bf16_supported": torch.cuda.is_bf16_supported(),
        "tf32_matmul_allow": torch.backends.cuda.matmul.allow_tf32,
        "tf32_cudnn_allow": torch.backends.cudnn.allow_tf32,
        "matmul_precision": torch.get_float32_matmul_precision(),
        "stack_notes": STACK_NOTES,
    }
    if extra:
        snap.update(extra)
    return snap


# ---------------------------------------------------------------------------
# Historical model / SNAC loader — matches commit 1ece78b _load_model() exactly.
# ---------------------------------------------------------------------------

def load_historical(device: str = "cuda") -> Tuple[Any, Any, Any, str]:
    """Load Veena + SNAC exactly as historical L4 server did.

    Returns (model, tokenizer, snac_model, resolved_commit_hash).
    """
    print(f"[LOAD] transformers={transformers.__version__} torch={torch.__version__}")
    t0 = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.bfloat16,
        device_map=device,
    )
    model.eval()

    # Try to recover the resolved commit hash so we can compare vs current run.
    commit_hash = None
    try:
        commit_hash = getattr(model.config, "_commit_hash", None)
    except Exception:
        pass

    print(f"  Veena loaded in {(time.monotonic()-t0):.1f}s commit={commit_hash}")

    # SNAC unpatched — this is the KEY historical difference from current code.
    from snac import SNAC  # noqa: E402
    t1 = time.monotonic()
    try:
        snac_model = SNAC.from_pretrained(SNAC_ID).to(device)
        snac_model.eval()
        print(f"  SNAC loaded UNPATCHED in {(time.monotonic()-t1):.1f}s")
        snac_ok = True
    except Exception as e:
        print(f"  SNAC UNPATCHED LOAD FAILED: {e}")
        snac_model = None
        snac_ok = False

    return model, tokenizer, snac_model, commit_hash or "unknown", snac_ok


def build_input(tokenizer, text: str, speaker: str = "kavya",
                device: str = "cuda") -> Tuple[torch.Tensor, List[int]]:
    prompt = f"<spk_{speaker}> {text}"
    prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
    ids = [START_OF_HUMAN, *prompt_ids, END_OF_HUMAN, START_OF_AI, START_OF_SPEECH]
    return torch.tensor([ids], device=device), ids


# ---------------------------------------------------------------------------
# Generation capture.
# use_seed=True  → torch.manual_seed(seed) before generate() [Protocol B]
# use_seed=False → NO seed, RNG state as-inherited [Protocol A / historical]
# ---------------------------------------------------------------------------

def capture_generation(model, tokenizer, text: str, use_seed: bool,
                       seed: int = SEED, device: str = "cuda") -> Dict[str, Any]:
    if use_seed:
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    input_ids, ids_list = build_input(tokenizer, text, device=device)
    prompt_len = input_ids.shape[1]
    max_new = min(int(len(text) * 1.3) * TOKENS_PER_FRAME + 21, 700)
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else END_OF_SPEECH

    t0 = time.monotonic()
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
    lat = time.monotonic() - t0

    seq = out.sequences[0].tolist()
    gen_tail = seq[prompt_len:]
    scores = out.scores
    per_pos = []
    K = 5
    audio_count_running = 0
    audio_ids_full: List[int] = []
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
            "chosen_prob": (float(probs[chosen].item())
                            if 0 <= chosen < probs.shape[0] else None),
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
            audio_ids_full.append(chosen)
        per_pos.append(entry)
    return {
        "text": text,
        "seq": seq,
        "prompt_len": prompt_len,
        "audio_ids_full": audio_ids_full,
        "per_pos": per_pos,
        "latency_s": lat,
        "seeded": use_seed,
    }


# ---------------------------------------------------------------------------
# SNAC decode (unpatched historical style) + F0 classification
# ---------------------------------------------------------------------------

def snac_decode(snac_model, audio_ids: List[int], device: str = "cuda") -> np.ndarray | None:
    """Decode full audio_ids stream through UNPATCHED SNAC to PCM float32."""
    if snac_model is None or not audio_ids:
        return None
    # trim to whole super-frames of 7 tokens
    n_frames = len(audio_ids) // TOKENS_PER_FRAME
    if n_frames < 1:
        return None
    tokens = audio_ids[: n_frames * TOKENS_PER_FRAME]
    # Convert to codebook values with position offsets.
    codes_c0, codes_c1, codes_c2 = [], [], []
    for f in range(n_frames):
        base = f * TOKENS_PER_FRAME
        for p in range(TOKENS_PER_FRAME):
            val = tokens[base + p] - AUDIO_BASE - p * CODEBOOK_SIZE
            val = max(0, min(val, CODEBOOK_SIZE - 1))
            if p == 0:
                codes_c0.append(val)
            elif p in (1, 4):
                codes_c1.append(val)
            else:
                codes_c2.append(val)
    dev = next(snac_model.parameters()).device
    codes = [
        torch.tensor([codes_c0], device=dev, dtype=torch.long),
        torch.tensor([codes_c1], device=dev, dtype=torch.long),
        torch.tensor([codes_c2], device=dev, dtype=torch.long),
    ]
    try:
        with torch.no_grad():
            audio_hat = snac_model.decode(codes)
        return audio_hat[0, 0].float().cpu().numpy()
    except Exception as e:
        print(f"    snac decode failed: {e}")
        return None


def estimate_f0_yin(pcm: np.ndarray, sr: int = SR,
                    frame_ms: int = 25, hop_ms: int = 10,
                    fmin: float = 60.0, fmax: float = 400.0) -> np.ndarray:
    """Cheap YIN-like F0 estimator (median-of-frames used only for classification)."""
    if pcm is None or len(pcm) < sr // 20:
        return np.array([])
    frame = int(sr * frame_ms / 1000)
    hop = int(sr * hop_ms / 1000)
    tmin, tmax = int(sr / fmax), int(sr / fmin)
    f0s = []
    for start in range(0, len(pcm) - frame, hop):
        seg = pcm[start:start + frame].astype(np.float64)
        seg -= seg.mean()
        if np.abs(seg).max() < 1e-3:
            continue
        # autocorrelation
        r = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
        r = r[tmin:tmax + 1]
        if len(r) < 2:
            continue
        peak = int(np.argmax(r)) + tmin
        if peak < 1:
            continue
        f0s.append(sr / peak)
    return np.array(f0s)


def classify_voice(f0s: np.ndarray) -> Tuple[str, float, float]:
    if len(f0s) < 5:
        return ("unknown", 0.0, 0.0)
    med = float(np.median(f0s))
    p05 = float(np.percentile(f0s, 5))
    # Female Kavya centres around 200Hz; male drift around 100-130Hz.
    if med >= 170 and p05 >= 140:
        cls = "female-kavya"
    elif med <= 150 or p05 <= 110:
        cls = "male-drift"
    else:
        cls = "ambiguous"
    return (cls, med, p05)


def sha16(ids: List[int]) -> str:
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Speaker-embedding fingerprint — matches earlier runs.
# ---------------------------------------------------------------------------

def spk_embed_analysis(model, tokenizer) -> Dict[str, Any]:
    spk_ids = {s: tokenizer.encode(f"<spk_{s}>", add_special_tokens=False)
               for s in SPEAKERS}
    emb = model.get_input_embeddings().weight.detach()
    out: Dict[str, Any] = {
        "ids": spk_ids,
        "embed_dtype": str(emb.dtype),
        "norms": {},
        "cos_to_kavya": {},
    }
    kv = None
    if len(spk_ids["kavya"]) == 1:
        kv = emb[spk_ids["kavya"][0]].float()
    for s in SPEAKERS:
        if len(spk_ids[s]) != 1:
            out["norms"][s] = None
            out["cos_to_kavya"][s] = None
            continue
        v = emb[spk_ids[s][0]].float()
        out["norms"][s] = float(v.norm().item())
        if kv is not None:
            cos = float(torch.nn.functional.cosine_similarity(v, kv, dim=0).item())
            out["cos_to_kavya"][s] = cos
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_corpus(model, tokenizer, snac_model, protocol_tag: str,
               use_seed: bool) -> List[Dict[str, Any]]:
    records = []
    for idx, (bucket, text) in enumerate(CORPUS):
        rec = capture_generation(model, tokenizer, text, use_seed=use_seed)
        pcm = snac_decode(snac_model, rec["audio_ids_full"])
        f0s = estimate_f0_yin(pcm) if pcm is not None else np.array([])
        cls, med, p05 = classify_voice(f0s)
        rec.update({
            "idx": idx,
            "bucket": bucket,
            "class": cls,
            "f0_med": med,
            "f0_p05": p05,
            "audio_len_samples": (len(pcm) if pcm is not None else 0),
            "audio_sha16": (
                hashlib.sha256(pcm.astype(np.float32).tobytes()).hexdigest()[:16]
                if pcm is not None else None
            ),
            "audio_ids_sha16": sha16(rec["audio_ids_full"]),
            "audio_ids_first21": rec["audio_ids_full"][:21],
        })
        print(f"[{protocol_tag}][{idx:2d}][{bucket:<9s}] cls={cls:12s} "
              f"med={med:.1f} p05={p05:.1f} sha={rec['audio_sha16']} "
              f"lat={rec['latency_s']:.1f}s first5={rec['audio_ids_full'][:5]}")
        # Strip per_pos before saving to keep JSON small — keep only first 40 pos.
        rec["per_pos"] = rec["per_pos"][:40]
        records.append(rec)
    return records


def run_determinism(model, tokenizer, snac_model, protocol_tag: str,
                    use_seed: bool, reps: int = 3) -> List[Dict[str, Any]]:
    out = []
    for idx in DET_IDX:
        _, text = CORPUS[idx]
        reps_out = []
        for r in range(reps):
            rec = capture_generation(model, tokenizer, text, use_seed=use_seed)
            pcm = snac_decode(snac_model, rec["audio_ids_full"])
            f0s = estimate_f0_yin(pcm) if pcm is not None else np.array([])
            cls, med, p05 = classify_voice(f0s)
            reps_out.append({
                "rep": r,
                "class": cls,
                "f0_med": med,
                "f0_p05": p05,
                "audio_sha16": (
                    hashlib.sha256(pcm.astype(np.float32).tobytes()).hexdigest()[:16]
                    if pcm is not None else None
                ),
                "audio_ids_sha16": sha16(rec["audio_ids_full"]),
                "first21": rec["audio_ids_full"][:21],
            })
        u_sha = len({r["audio_sha16"] for r in reps_out})
        u_first = len({tuple(r["first21"]) for r in reps_out})
        classes = [r["class"] for r in reps_out]
        print(f"[{protocol_tag}][det][{idx}] unique_sha={u_sha} "
              f"unique_first21={u_first} classes={classes}")
        out.append({"idx": idx, "unique_sha16": u_sha,
                    "unique_first21_audio": u_first, "reps": reps_out})
    return out


def main() -> None:
    output = os.environ.get("OUTPUT", "/home/ubuntu/historical_l4_results.json")
    print("=" * 78)
    print("HISTORICAL L4 FORENSIC — commit 1ece78b implementation on rented L4")
    print("=" * 78)

    device = "cuda"
    torch.set_float32_matmul_precision("highest")  # neutral vs Protocol B parity

    load = load_historical(device)
    model, tokenizer, snac_model, commit_hash, snac_ok = load

    env = env_snapshot("HISTORICAL_L4", extra={
        "model_commit_hash": commit_hash,
        "snac_id": SNAC_ID,
        "snac_loaded_unpatched": snac_ok,
    })
    print(f"[ENV] {json.dumps({k: env[k] for k in ['gpu_name','gpu_cap','torch','cuda','cudnn','transformers','stack_notes','model_commit_hash','snac_loaded_unpatched']}, indent=2)}")

    # ── Historical warm-up call (same as tts_server_1ece78b.py) ───────────────
    print("[WARMUP] running historical warm-up call 'hello' / 'kavya' ...")
    try:
        _ = capture_generation(model, tokenizer, "hello", use_seed=False)
    except Exception as e:
        print(f"  warm-up failed: {e}")

    # ── Speaker embedding fingerprint ────────────────────────────────────────
    spk = spk_embed_analysis(model, tokenizer)
    print(f"[SPK] cos_to_kavya_max_other={max(v for k,v in spk['cos_to_kavya'].items() if k!='kavya' and v is not None):.4f}")

    # ── Protocol A: HISTORICAL FAITHFUL — no seed ────────────────────────────
    print("\n=== PROTOCOL A — historical faithful (no seed anywhere) ===")
    recs_A = run_corpus(model, tokenizer, snac_model, "L4_HIST_noseed", use_seed=False)
    det_A = run_determinism(model, tokenizer, snac_model, "L4_HIST_noseed",
                            use_seed=False)

    # ── Protocol B: seeded per-text (parity with current-code L4) ────────────
    print("\n=== PROTOCOL B — seeded per-text (parity vs current L4) ===")
    recs_B = run_corpus(model, tokenizer, snac_model, "L4_HIST_seed42", use_seed=True)
    det_B = run_determinism(model, tokenizer, snac_model, "L4_HIST_seed42",
                            use_seed=True)

    result = {
        "meta": {
            "session": "HISTORICAL_L4_FORENSIC",
            "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "commit 1ece78b Veena server.py behaviour on rented L4",
        },
        "env": env,
        "spk_embed_analysis": spk,
        "runs": {
            "L4_HIST_noseed": {
                "protocol": "historical faithful — no manual_seed anywhere; "
                            "warm-up 'hello' at boot",
                "env": env,
                "records": recs_A,
                "determinism": det_A,
            },
            "L4_HIST_seed42": {
                "protocol": "same code path but torch.manual_seed(42) + "
                            "torch.cuda.manual_seed_all(42) before every generate()",
                "env": env,
                "records": recs_B,
                "determinism": det_B,
            },
        },
    }
    with open(output, "w") as f:
        json.dump(result, f, default=str, indent=1)
    print(f"\nWROTE {output}, size={os.path.getsize(output)} bytes")
    print(f"=== HISTORICAL L4 FORENSIC DONE {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")


if __name__ == "__main__":
    main()
