#!/usr/bin/env python3
"""Four-approach A/B forensic harness for Veena speaker drift.

Runs the full corpus (BASELINE + DEVANAGARI + SPELLING + ACRONYM + PROMPT)
across N seeds under a single model load, capturing per-record:
  - audio_ids_full (list of codebook vals)
  - first_21_audio_positions with top-5 ids/logits/probs, top1-top2 margin
  - F0 classification (female-kavya / ambiguous / male-drift / silent)
  - audio SHA-256[:16]
  - latency
  - tokenizer sequence for the prompt (so we can inspect if <spk_kavya> is a
    single special-token id in every wrapper variant)

Tokenizer diagnostic emitted once per prompt-wrapper: shows exact token IDs so
we can prove whether <spk_kavya><spk_kavya> = two special-token ids, whether
[kavya] tokenizes as ordinary BPE, and how V4 (no space) fuses.

Hard rules honoured (no production side effects, no rejection sampling, no
temperature/greedy change, no SNAC blacklist, no phrase substitution).

Output: /home/ubuntu/four_approach_results.json
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
from typing import Any, Dict, List, Tuple

# Bootstrap (idempotent) — matches the L4 baseline stack
def _pip(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

need = {"transformers": "5.0.0", "snac": "1.1.0", "accelerate": None, "numpy": None}
for pkg, ver in need.items():
    try:
        __import__(pkg)
    except ImportError:
        _pip(f"{pkg}=={ver}" if ver else pkg)

import numpy as np                                              # noqa: E402
import torch                                                    # noqa: E402
import transformers                                             # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer    # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus import BASELINE, DEVANAGARI, SPELLING, ACRONYM, PROMPT     # noqa: E402
from corpus import PROMPT_PAYLOADS, PROMPT_WRAPPERS                    # noqa: E402


MODEL_ID = "maya-research/Veena"
MODEL_REVISION = "8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f"
SNAC_ID = "hubertsiuzdak/snac_24khz"
SEEDS = [42, 43, 44]  # 3 seeds per row for stochastic spread
DET_SEED = 42
DET_REPS = 3          # for determinism check on a subset

START_OF_HUMAN, END_OF_HUMAN = 128259, 128260
START_OF_AI, END_OF_AI = 128261, 128262
START_OF_SPEECH, END_OF_SPEECH = 128257, 128258
AUDIO_BASE, CODEBOOK_SIZE, TOKENS_PER_FRAME = 128266, 4096, 7
SNAC_MIN = AUDIO_BASE
SNAC_MAX = AUDIO_BASE + TOKENS_PER_FRAME * CODEBOOK_SIZE - 1
SR = 24000
SPEAKER = "kavya"

# Subset of rows to run at multiple seeds and determinism reps to keep runtime
# tractable. Every drift/stable/UPI-family row gets multi-seed; simple English
# and numeric rows only get seed=42.
def multi_seed_wanted(approach, sub, key):
    if approach == "BASELINE":
        return True  # all 19 baseline rows get 3 seeds
    if approach == "DEVANAGARI":
        return True
    if approach == "SPELLING":
        return True
    if approach == "ACRONYM":
        return True
    if approach == "PROMPT":
        return True
    return False

DET_KEYS = ["B01", "B02", "B07",           # drift + Dhanyavaad
            "D01_mixed", "D07_mixed",      # Devanagari counterparts
            "S_DV_02",                     # dhanyawad
            "A_UPI_spelled",               # yoo pee aaye
            "P_UPI_V2", "P_DV_V2"]         # prompt V2 reinforcement


# ---------------------------------------------------------------------------
# SNAC patched loader — identical logic to part_c_a6000/l4_forensic.py
# ---------------------------------------------------------------------------
def load_snac(device="cuda"):
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

    def _snake_plain(x, alpha):
        shape = x.shape
        x = x.reshape(shape[0], shape[1], -1)
        x = x + (alpha + 1e-9).reciprocal() * torch.sin(alpha * x).pow(2)
        return x.reshape(shape)
    snac_layers.snake = _snake_plain
    return model


# ---------------------------------------------------------------------------
# F0 helpers
# ---------------------------------------------------------------------------
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


def analyze(pcm_i16):
    n = len(pcm_i16)
    WIN, HOP = 480, 240
    if n < WIN:
        return {"class": "silent", "median_f0": None, "p05_f0": None,
                "first_male_ms": None, "f0_len": 0}
    nf = 1 + (n - WIN) // HOP
    x = pcm_i16.astype(np.float32)
    f0s = []
    first_male_ms = None
    for i in range(nf):
        f = f0_frame(x[i * HOP:i * HOP + WIN])
        if f > 0:
            f0s.append(f)
            if first_male_ms is None and f < 140:
                first_male_ms = int(i * HOP * 1000 / SR)
    if not f0s:
        return {"class": "silent", "median_f0": None, "p05_f0": None,
                "first_male_ms": None, "f0_len": 0}
    a = np.array(f0s)
    med = float(np.median(a))
    p05 = float(np.percentile(a, 5))
    cls = ("female-kavya" if (med >= 180 and p05 >= 140)
           else "male-drift" if (med < 165 or p05 < 120)
           else "ambiguous")
    return {"class": cls, "median_f0": med, "p05_f0": p05,
            "first_male_ms": first_male_ms, "f0_len": len(f0s)}


def decode_snac_audio(snac_model, audio_ids_int, device):
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


def build_prompt_ids(tokenizer, approach, text, device):
    """Approach==PROMPT rows already contain their own <spk_kavya> wrappers.
    Everything else gets the default V1 prefix."""
    if approach == "PROMPT":
        prompt_text = text
    else:
        prompt_text = f"<spk_{SPEAKER}> {text}"
    pids = tokenizer.encode(prompt_text, add_special_tokens=False)
    ids = [START_OF_HUMAN, *pids, END_OF_HUMAN, START_OF_AI, START_OF_SPEECH]
    return torch.tensor([ids], device=device), ids, pids, prompt_text


def capture(model, tokenizer, approach, text, seed, device):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    input_ids, ids_list, prompt_tokens_only, prompt_text = build_prompt_ids(
        tokenizer, approach, text, device)
    prompt_len = input_ids.shape[1]
    # A little more headroom for full Devanagari (numbers spelled out)
    max_new = min(int(len(text) * 1.6) * TOKENS_PER_FRAME + 21, 900)
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
    scores = out.scores
    gen_tail = seq[prompt_len:]
    K = 5
    per_pos = []
    audio_count_running = 0
    for i, s in enumerate(scores[:21]):   # only capture per-pos for first 21 (audio interior)
        # extend cheaply: first 21 audio-token positions, not first 21 gen positions
        logits = s[0].float()
        probs = torch.softmax(logits, dim=-1)
        topv, topi = torch.topk(logits, K)
        chosen = int(gen_tail[i]) if i < len(gen_tail) else -1
        top1, top2 = float(topv[0]), float(topv[1])
        chosen_rank = -1
        for r, tid in enumerate(topi.tolist()):
            if tid == chosen:
                chosen_rank = r
                break
        entry = {
            "pos": i,
            "chosen": chosen,
            "chosen_rank_in_top5": chosen_rank,
            "top5_ids": [int(x) for x in topi.tolist()],
            "top5_logits": [float(x) for x in topv.tolist()],
            "top1_top2_margin": top1 - top2,
            "is_audio": SNAC_MIN <= chosen <= SNAC_MAX,
        }
        if entry["is_audio"]:
            cb_pos = audio_count_running % TOKENS_PER_FRAME
            cb_val = chosen - AUDIO_BASE - cb_pos * CODEBOOK_SIZE
            entry["codebook_pos"] = cb_pos
            entry["codebook_val"] = max(0, min(cb_val, CODEBOOK_SIZE - 1))
            audio_count_running += 1
        per_pos.append(entry)
    return {"seq": seq, "prompt_len": prompt_len, "per_pos": per_pos,
            "prompt_text": prompt_text, "prompt_token_ids": prompt_tokens_only}


def env_snapshot():
    return {
        "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "transformers": transformers.__version__,
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_cap": list(torch.cuda.get_device_capability(0)),
        "bf16_supported": torch.cuda.is_bf16_supported(),
        "tf32_matmul_allow": torch.backends.cuda.matmul.allow_tf32,
        "matmul_precision": torch.get_float32_matmul_precision(),
    }


def tokenizer_diagnostic(tokenizer):
    """Emit tokenization info to prove [kavya] vs <spk_kavya>, wrappers, etc."""
    probes = [
        "<spk_kavya>",
        "<spk_kavya> <spk_kavya>",
        "<spk_kavya>test",
        "<spk_kavya> test",
        "test <spk_kavya>",
        "[kavya]",
        "[kavya] test",
        "<spk_kavya> Dhanyavaad sir.",
        "<spk_kavya> धन्यवाद सर।",
        "<spk_apsara>",
    ]
    out = {}
    for p in probes:
        ids = tokenizer.encode(p, add_special_tokens=False)
        decoded = [tokenizer.decode([i]) for i in ids]
        out[p] = {"ids": ids, "n": len(ids), "decoded_per_id": decoded}
    # Confirm spk_kavya is a single special token id
    ids = tokenizer.encode("<spk_kavya>", add_special_tokens=False)
    out["_spk_kavya_id"] = ids[0] if len(ids) == 1 else None
    out["_spk_kavya_is_single_token"] = (len(ids) == 1)
    return out


def run_row(model, tokenizer, snac_model, approach, sub, key, text, notes,
            seed, device):
    tt = time.time()
    cap = capture(model, tokenizer, approach, text, seed, device)
    latency = time.time() - tt
    aud_vals = audio_ids_from_generated(cap["seq"], cap["prompt_len"])
    pcm = (decode_snac_audio(snac_model, aud_vals, device)
           if len(aud_vals) >= 21 else np.zeros(0, dtype=np.int16))
    ana = analyze(pcm)
    return {
        "approach": approach, "sub": sub, "key": key, "text": text, "notes": notes,
        "seed": seed,
        "prompt_text": cap["prompt_text"],
        "prompt_token_ids": cap["prompt_token_ids"],
        "prompt_len": cap["prompt_len"],
        "n_gen": len(cap["seq"]) - cap["prompt_len"],
        "n_audio": len(aud_vals),
        "first_21_audio_positions": cap["per_pos"],
        "audio_ids_full": aud_vals,
        "audio_sha16": hashlib.sha256(bytes(pcm)).hexdigest()[:16],
        "audio_seconds": len(pcm) / SR,
        "class": ana["class"],
        "median_f0": ana["median_f0"],
        "p05_f0": ana["p05_f0"],
        "first_male_ms": ana["first_male_ms"],
        "f0_len": ana["f0_len"],
        "latency_s": latency,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/home/ubuntu/four_approach_results.json")
    parser.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    parser.add_argument("--approaches", nargs="+",
                        default=["BASELINE", "DEVANAGARI", "SPELLING", "ACRONYM", "PROMPT"])
    parser.add_argument("--skip-det", action="store_true")
    args = parser.parse_args()

    print(f"=== FOUR-APPROACH FORENSIC START {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                    "--format=csv,noheader"], check=False)

    print("Loading tokenizer ...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    print("Loading SNAC (patched flavour) ...")
    snac_model = load_snac("cuda")
    print("Loading Veena BF16 SDPA ...")
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
        revision=MODEL_REVISION,
        attn_implementation="sdpa",
    )
    model.eval()
    print(f"loaded commit={getattr(model.config, '_commit_hash', None)}")

    # Speaker embedding sanity — same value as prior L4 runs (proves same model)
    tok_diag = tokenizer_diagnostic(tokenizer)
    print(f"<spk_kavya> single-token={tok_diag['_spk_kavya_is_single_token']} "
          f"id={tok_diag['_spk_kavya_id']}")

    all_rows = []
    for approach in args.approaches:
        table = {"BASELINE": BASELINE, "DEVANAGARI": DEVANAGARI,
                 "SPELLING": SPELLING, "ACRONYM": ACRONYM, "PROMPT": PROMPT}[approach]
        print(f"\n{'='*70}\n=== {approach}: {len(table)} rows × {len(args.seeds)} seeds\n{'='*70}")
        for row in table:
            appr, sub, key, text, notes = row
            for seed in args.seeds:
                rec = run_row(model, tokenizer, snac_model, appr, sub, key,
                              text, notes, seed, "cuda")
                all_rows.append(rec)
                print(f"[{appr[:4]:<4}][{key:<14}][seed={seed}] "
                      f"cls={rec['class']:12s} "
                      f"med={rec['median_f0']} p05={rec['p05_f0']} "
                      f"lat={rec['latency_s']:.1f}s "
                      f"txt={text[:40]!r}")

    # Determinism reps — same seed, N reps, for selected keys
    det_rows = []
    if not args.skip_det:
        print(f"\n{'='*70}\n=== DETERMINISM: {len(DET_KEYS)} keys × {DET_REPS} reps @ seed={DET_SEED}\n{'='*70}")
        # Build key→(approach,sub,text,notes) map
        row_lookup = {r[2]: r for r in (BASELINE + DEVANAGARI + SPELLING + ACRONYM + PROMPT)}
        for key in DET_KEYS:
            if key not in row_lookup:
                print(f"[det] MISSING key {key}")
                continue
            appr, sub, k2, text, notes = row_lookup[key]
            reps = []
            for r in range(DET_REPS):
                rec = run_row(model, tokenizer, snac_model, appr, sub, k2,
                              text, notes, DET_SEED, "cuda")
                reps.append(rec)
                print(f"[det][{key}][rep{r}] sha={rec['audio_sha16']} "
                      f"cls={rec['class']} med={rec['median_f0']}")
            det_rows.append({
                "key": key, "approach": appr, "sub": sub, "text": text,
                "unique_sha16": len({r["audio_sha16"] for r in reps}),
                "unique_first21_audio": len({tuple(
                    p["chosen"] for p in r["first_21_audio_positions"] if p["is_audio"])
                    for r in reps}),
                "classes": [r["class"] for r in reps],
                "median_f0s": [r["median_f0"] for r in reps],
                "reps": reps,
            })

    out = {
        "run_meta": {
            "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": os.uname().nodename,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "snac_id": SNAC_ID,
            "seeds": args.seeds,
            "det_seed": DET_SEED,
            "det_reps": DET_REPS,
            "approaches": args.approaches,
        },
        "env": env_snapshot(),
        "tokenizer_diagnostic": tok_diag,
        "rows": all_rows,
        "determinism": det_rows,
    }
    with open(args.output, "w") as f:
        json.dump(out, f, indent=1,
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print(f"\nWROTE {args.output} size={os.path.getsize(args.output)} bytes")
    print(f"=== FOUR-APPROACH FORENSIC DONE {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")


if __name__ == "__main__":
    main()
