#!/usr/bin/env python3
"""V2 validation harness — V1 vs V2 across ~100 production-realistic texts.

Per row: 2 seeds × 2 wrappers = 4 generations. UPI-normalized and Deva-diagnostic
overlays are also emitted where mapped (V2 only, 1 seed each to stay in budget).

Total: ~103 × 4 + 7 + 8 = 427 generations. ~40 min on L4 BF16.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import types
from typing import Any, Dict, List

def _pip(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

need = {"transformers": "5.0.0", "snac": "1.1.0", "accelerate": None, "numpy": None}
for pkg, ver in need.items():
    try: __import__(pkg)
    except ImportError: _pip(f"{pkg}=={ver}" if ver else pkg)

import numpy as np
import torch
import transformers
from transformers import AutoModelForCausalLM, AutoTokenizer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_v2 import CORPUS, UPI_NORMALIZED_MAP, DEVA_DIAGNOSTIC_MAP

MODEL_ID = "maya-research/Veena"
MODEL_REVISION = "8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f"
SNAC_ID = "hubertsiuzdak/snac_24khz"
SEEDS = [42, 43]  # 2 seeds to fit 45-min GPU budget
SEED_OVERLAY = 42
SPEAKER = "kavya"

START_OF_HUMAN, END_OF_HUMAN = 128259, 128260
START_OF_AI, END_OF_AI = 128261, 128262
START_OF_SPEECH, END_OF_SPEECH = 128257, 128258
AUDIO_BASE, CODEBOOK_SIZE, TOKENS_PER_FRAME = 128266, 4096, 7
SNAC_MIN = AUDIO_BASE
SNAC_MAX = AUDIO_BASE + TOKENS_PER_FRAME * CODEBOOK_SIZE - 1
SR = 24000

WRAPPERS = {
    "V1": "<spk_kavya> {text}",
    "V2": "<spk_kavya> <spk_kavya> {text}",
}


def load_snac(device="cuda"):
    from snac import SNAC
    from huggingface_hub import hf_hub_download
    import snac.layers as snac_layers
    cfg_path = hf_hub_download(repo_id=SNAC_ID, filename="config.json")
    wts_path = hf_hub_download(repo_id=SNAC_ID, filename="pytorch_model.bin")
    with open(cfg_path) as f: cfg = json.load(f)
    model = SNAC(**cfg)
    def _strip(seq):
        return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != "LocalMHA"])
    model.encoder.block = _strip(model.encoder.block)
    model.decoder.model = _strip(model.decoder.model)
    state = torch.load(wts_path, map_location="cpu", weights_only=False)
    model.load_state_dict(state, strict=False)
    model.eval().to(device)
    def _dec(self, codes):
        z_q = 0
        for q, code in zip(self.quantizer.quantizers, codes):
            z = q.decode_code(code); z = q.out_proj(z)
            if q.stride > 1: z = z.repeat_interleave(q.stride, dim=-1)
            z_q = z_q + z
        return self.decoder(z_q)
    model.decode = types.MethodType(_dec, model)
    def _snake(x, a):
        s = x.shape; x = x.reshape(s[0], s[1], -1)
        x = x + (a + 1e-9).reciprocal() * torch.sin(a * x).pow(2)
        return x.reshape(s)
    snac_layers.snake = _snake
    return model


def f0_frame(frame, sr=SR, fmin=70, fmax=400):
    frame = frame.astype(np.float32) - frame.mean()
    if np.sqrt((frame * frame).mean()) < 300: return 0.0
    corr = np.correlate(frame, frame, mode="full")
    corr = corr[len(corr)//2:]
    corr = corr / (corr[0] + 1e-9)
    lag_min, lag_max = sr // fmax, sr // fmin
    seg = corr[lag_min:lag_max]
    if len(seg) == 0: return 0.0
    peak = int(np.argmax(seg)) + lag_min
    if corr[peak] < 0.30: return 0.0
    return sr / peak


def analyze(pcm):
    n = len(pcm); WIN, HOP = 480, 240
    if n < WIN: return {"class": "silent", "median_f0": None, "p05_f0": None, "first_male_ms": None}
    nf = 1 + (n - WIN) // HOP
    x = pcm.astype(np.float32)
    f0s, first_male_ms = [], None
    for i in range(nf):
        f = f0_frame(x[i*HOP:i*HOP + WIN])
        if f > 0:
            f0s.append(f)
            if first_male_ms is None and f < 140:
                first_male_ms = int(i * HOP * 1000 / SR)
    if not f0s: return {"class": "silent", "median_f0": None, "p05_f0": None, "first_male_ms": None}
    a = np.array(f0s); med = float(np.median(a)); p05 = float(np.percentile(a, 5))
    cls = ("female-kavya" if (med >= 180 and p05 >= 140)
           else "male-drift" if (med < 165 or p05 < 120) else "ambiguous")
    return {"class": cls, "median_f0": med, "p05_f0": p05, "first_male_ms": first_male_ms}


def decode_snac(snac_model, audio_ids, device):
    if len(audio_ids) < 7: return np.zeros(0, dtype=np.int16)
    n_frames = len(audio_ids) // 7
    audio_ids = audio_ids[:n_frames * 7]
    c0, c1, c2 = [], [], []
    for f in range(n_frames):
        b = f * 7
        c0.append(audio_ids[b+0])
        c1.append(audio_ids[b+1]); c1.append(audio_ids[b+4])
        c2.append(audio_ids[b+2]); c2.append(audio_ids[b+3]); c2.append(audio_ids[b+5]); c2.append(audio_ids[b+6])
    with torch.no_grad():
        t0 = torch.tensor([c0], device=device, dtype=torch.long)
        t1 = torch.tensor([c1], device=device, dtype=torch.long)
        t2 = torch.tensor([c2], device=device, dtype=torch.long)
        wav = snac_model.decode([t0, t1, t2]).squeeze().float().cpu().numpy()
    return (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)


def audio_ids_of(seq, prompt_len):
    tail = seq[prompt_len:]
    aud, pos = [], 0
    for tid in tail:
        if SNAC_MIN <= tid <= SNAC_MAX:
            p = pos % TOKENS_PER_FRAME
            v = tid - AUDIO_BASE - p * CODEBOOK_SIZE
            aud.append(max(0, min(v, CODEBOOK_SIZE - 1)))
            pos += 1
    return aud


def generate_row(model, tokenizer, snac_model, prompt_text, seed, device):
    torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    pids = tokenizer.encode(prompt_text, add_special_tokens=False)
    ids = [START_OF_HUMAN, *pids, END_OF_HUMAN, START_OF_AI, START_OF_SPEECH]
    input_ids = torch.tensor([ids], device=device)
    prompt_len = input_ids.shape[1]
    max_new = min(int(len(prompt_text) * 1.6) * TOKENS_PER_FRAME + 21, 900)
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else END_OF_SPEECH
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(
            input_ids, max_new_tokens=max_new, do_sample=True,
            temperature=0.4, top_p=0.9, repetition_penalty=1.05,
            pad_token_id=pad, eos_token_id=[END_OF_SPEECH, END_OF_AI],
            output_scores=True, return_dict_in_generate=True)
    latency = time.time() - t0
    seq = out.sequences[0].tolist()
    scores = out.scores
    gen_tail = seq[prompt_len:]
    first21 = []
    audio_count = 0
    for i, s in enumerate(scores[:21]):
        logits = s[0].float()
        topv, topi = torch.topk(logits, 3)
        chosen = int(gen_tail[i]) if i < len(gen_tail) else -1
        first21.append({
            "pos": i, "chosen": chosen,
            "top3_ids": [int(x) for x in topi.tolist()],
            "top1_top2_margin": float(topv[0]) - float(topv[1]),
            "is_audio": SNAC_MIN <= chosen <= SNAC_MAX,
        })
    aud = audio_ids_of(seq, prompt_len)
    pcm = decode_snac(snac_model, aud, device) if len(aud) >= 21 else np.zeros(0, dtype=np.int16)
    ana = analyze(pcm)
    return {
        "prompt_text": prompt_text,
        "prompt_len": prompt_len,
        "n_audio": len(aud),
        "audio_ids_first50": aud[:50],
        "audio_sha16": hashlib.sha256(bytes(pcm)).hexdigest()[:16],
        "audio_seconds": len(pcm) / SR,
        "first_21_positions": first21,
        "class": ana["class"], "median_f0": ana["median_f0"],
        "p05_f0": ana["p05_f0"], "first_male_ms": ana["first_male_ms"],
        "latency_s": latency,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="/home/ubuntu/v2_validation_results.json")
    args = ap.parse_args()

    print(f"=== V2 VALIDATION START {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used", "--format=csv,noheader"], check=False)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    print("Loading SNAC (patched) ...")
    snac_model = load_snac("cuda")
    print("Loading Veena BF16 SDPA ...")
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, torch_dtype=torch.bfloat16, device_map="cuda",
        revision=MODEL_REVISION, attn_implementation="sdpa",
    )
    model.eval()
    print(f"commit={getattr(model.config, '_commit_hash', None)}")

    total_expected = len(CORPUS) * len(WRAPPERS) * len(SEEDS) + len(UPI_NORMALIZED_MAP) + len(DEVA_DIAGNOSTIC_MAP)
    print(f"N corpus={len(CORPUS)} wrappers={list(WRAPPERS)} seeds={SEEDS}")
    print(f"Total generations expected: {total_expected}")

    rows = []
    t_start = time.time()
    idx = 0
    # Main: V1 and V2 for every corpus row × 2 seeds
    for cat, key, text in CORPUS:
        for wrapper_name, wrapper_tpl in WRAPPERS.items():
            prompt = wrapper_tpl.format(text=text)
            for seed in SEEDS:
                idx += 1
                rec = generate_row(model, tokenizer, snac_model, prompt, seed, "cuda")
                rec.update({"category": cat, "key": key, "text": text,
                            "wrapper": wrapper_name, "seed": seed})
                rows.append(rec)
                eta = (time.time() - t_start) / idx * (total_expected - idx)
                print(f"[{idx:3d}/{total_expected}][{cat:3s}][{key:22s}][{wrapper_name}][s{seed}] "
                      f"cls={rec['class']:12s} med={rec['median_f0']} "
                      f"lat={rec['latency_s']:.1f}s ETA={eta/60:.1f}m")

    # UPI overlay (V2 only, seed=42)
    for key, upi_text in UPI_NORMALIZED_MAP.items():
        idx += 1
        prompt = WRAPPERS["V2"].format(text=upi_text)
        rec = generate_row(model, tokenizer, snac_model, prompt, SEED_OVERLAY, "cuda")
        rec.update({"category": "UPI_NORM", "key": key, "text": upi_text,
                    "wrapper": "V2+UPI", "seed": SEED_OVERLAY})
        rows.append(rec)
        print(f"[{idx:3d}/{total_expected}][UPI][{key:22s}][V2+UPI][s{SEED_OVERLAY}] cls={rec['class']:12s}")

    # Devanagari diagnostic (V2 only, seed=42)
    for key, deva_text in DEVA_DIAGNOSTIC_MAP.items():
        idx += 1
        prompt = WRAPPERS["V2"].format(text=deva_text)
        rec = generate_row(model, tokenizer, snac_model, prompt, SEED_OVERLAY, "cuda")
        rec.update({"category": "V2_DEVA", "key": key, "text": deva_text,
                    "wrapper": "V2+Deva", "seed": SEED_OVERLAY})
        rows.append(rec)
        print(f"[{idx:3d}/{total_expected}][DEV][{key:22s}][V2+Deva][s{SEED_OVERLAY}] cls={rec['class']:12s}")

    out = {
        "run_meta": {
            "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_sec": time.time() - t_start,
            "host": os.uname().nodename,
            "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
            "snac_id": SNAC_ID, "seeds": SEEDS, "seed_overlay": SEED_OVERLAY,
            "wrappers": WRAPPERS,
        },
        "env": {
            "torch": torch.__version__, "cuda": torch.version.cuda,
            "transformers": transformers.__version__,
            "gpu_name": torch.cuda.get_device_name(0),
            "gpu_cap": list(torch.cuda.get_device_capability(0)),
            "bf16_supported": torch.cuda.is_bf16_supported(),
        },
        "rows": rows,
    }
    with open(args.output, "w") as f:
        json.dump(out, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print(f"\nWROTE {args.output} size={os.path.getsize(args.output)} bytes")
    print(f"=== V2 VALIDATION DONE {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")


if __name__ == "__main__":
    main()
