#!/usr/bin/env python3
"""Post-decode using snac 1.1.0's actual decoder API path."""
import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Dict, List

# Ensure snac 1.1.0 (known to load current checkpoint)
try:
    import snac
    if snac.__version__ != "1.1.0":
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q",
                               "snac==1.1.0", "--force-reinstall", "--no-deps"])
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "snac==1.1.0"])

import numpy as np
import torch
from snac import SNAC
print(f"snac_version={snac.__version__}")

AUDIO_BASE = 128266
CODEBOOK_SIZE = 4096
TOKENS_PER_FRAME = 7
SR = 24000


def load_snac(device="cuda"):
    m = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to(device)
    m.eval()
    return m


def codes_from_ids(audio_ids: List[int]):
    n_frames = len(audio_ids) // TOKENS_PER_FRAME
    if n_frames < 1:
        return None
    tokens = audio_ids[: n_frames * TOKENS_PER_FRAME]
    c0, c1, c2 = [], [], []
    for f in range(n_frames):
        base = f * TOKENS_PER_FRAME
        for p in range(TOKENS_PER_FRAME):
            raw = tokens[base + p]
            if raw >= AUDIO_BASE:
                val = raw - AUDIO_BASE - p * CODEBOOK_SIZE
            else:
                val = raw
            val = max(0, min(val, CODEBOOK_SIZE - 1))
            if p == 0:
                c0.append(val)
            elif p in (1, 4):
                c1.append(val)
            else:
                c2.append(val)
    return c0, c1, c2


def decode(snac_model, audio_ids, device="cuda"):
    r = codes_from_ids(audio_ids)
    if r is None:
        return None
    c0, c1, c2 = r
    dev = next(snac_model.parameters()).device
    codes = [
        torch.tensor([c0], device=dev, dtype=torch.long),
        torch.tensor([c1], device=dev, dtype=torch.long),
        torch.tensor([c2], device=dev, dtype=torch.long),
    ]
    # snac 1.1.0: quantizer forward takes latent z or codes list; try both.
    with torch.no_grad():
        # Path 1: quantizer sub-modules have `codebook` embeddings
        # Reconstruct z by summing embedded codes upsampled to match temporal rates.
        # For snac 24kHz: c0 at rate 1, c1 at rate 2, c2 at rate 4 (per super-frame token count).
        # The temporal length of z is 4 * n_frames (= len(c2)).
        try:
            qs = snac_model.quantizer.quantizers
            z_len = len(c2)
            z_dim = qs[0].codebook.weight.shape[1]
            z = torch.zeros((1, z_dim, z_len), device=dev)
            # c0: 1 code per frame → repeat 4x
            emb0 = qs[0].codebook(codes[0])       # (1, 1, dim)
            z += emb0.transpose(1, 2).repeat_interleave(4, dim=2)
            # c1: 2 codes per frame → repeat 2x
            emb1 = qs[1].codebook(codes[1])       # (1, 2, dim)
            z += emb1.transpose(1, 2).repeat_interleave(2, dim=2)
            # c2: 4 codes per frame → no upsample
            emb2 = qs[2].codebook(codes[2])       # (1, 4, dim)
            z += emb2.transpose(1, 2)
            aud = snac_model.decoder(z)
            return aud[0, 0].float().cpu().numpy()
        except Exception as e:
            print(f"    decode path 1 failed: {e}")
    return None


def estimate_f0(pcm, sr=SR, frame_ms=25, hop_ms=10, fmin=60.0, fmax=400.0):
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
        r = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
        r = r[tmin:tmax + 1]
        if len(r) < 2:
            continue
        peak = int(np.argmax(r)) + tmin
        if peak < 1:
            continue
        f0s.append(sr / peak)
    return np.array(f0s)


def classify(f0s):
    if len(f0s) < 5:
        return ("unknown", 0.0, 0.0)
    med = float(np.median(f0s))
    p05 = float(np.percentile(f0s, 5))
    if med >= 170 and p05 >= 140:
        return ("female-kavya", med, p05)
    if med <= 150 or p05 <= 110:
        return ("male-drift", med, p05)
    return ("ambiguous", med, p05)


def main():
    inp = os.environ.get("INPUT", "/home/ubuntu/historical_l4_results.json")
    out = os.environ.get("OUTPUT", "/home/ubuntu/historical_l4_results_with_f0.json")
    print(f"Loading {inp} ...")
    data = json.load(open(inp))
    print("Loading snac 1.1.0 ...")
    m = load_snac("cuda")
    print("Ready.")
    # Sanity test one decode
    test_ids = data["runs"]["L4_HIST_seed42"]["records"][0].get("audio_ids_full", [])[:70]
    pcm = decode(m, test_ids)
    print(f"Sanity decode: len={None if pcm is None else len(pcm)}")

    for run_name, run in data["runs"].items():
        print(f"=== {run_name} ({len(run['records'])} records) ===")
        for rec in run["records"]:
            pcm = decode(m, rec.get("audio_ids_full") or [])
            f0s = estimate_f0(pcm) if pcm is not None else np.array([])
            cls, med, p05 = classify(f0s)
            rec["class"] = cls
            rec["f0_med"] = med
            rec["f0_p05"] = p05
            if pcm is not None:
                rec["audio_len_samples"] = int(len(pcm))
                rec["audio_sha16"] = hashlib.sha256(
                    pcm.astype(np.float32).tobytes()).hexdigest()[:16]
            print(f"  [{rec['idx']:2d}][{rec['bucket']:<9s}] cls={cls:12s} "
                  f"med={med:.1f} p05={p05:.1f} sha={rec.get('audio_sha16')}")
    with open(out, "w") as f:
        json.dump(data, f, default=str, indent=1)
    print(f"WROTE {out}, size={os.path.getsize(out)} bytes")


if __name__ == "__main__":
    main()
