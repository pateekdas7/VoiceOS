#!/usr/bin/env python3
"""Post-decode script: read historical_l4_results.json, decode each record's
audio_ids_full to PCM using snac==1.0.0 (LocalMHA-stripped + snake fallback),
run F0 estimation, and produce a new file with per-record class label.

Runs on the L4 host (has GPU + snac install).
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List


def _pip(pkg: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])


# Force snac==1.0.0 fresh (uninstall whatever was there).
subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "snac"])
_pip("snac==1.0.0")

import numpy as np           # noqa: E402
import torch                 # noqa: E402
import snac as snac_pkg      # noqa: E402
from snac import SNAC        # noqa: E402
print(f"snac_version={snac_pkg.__version__}")

AUDIO_BASE = 128266
CODEBOOK_SIZE = 4096
TOKENS_PER_FRAME = 7
SR = 24000


# ---- SNAC patched loader (matches current T4/L4 path) ----------------------
# Strip LocalMHA + snake fallback + attn_window_size=None so the checkpoint
# loads on snac==1.0.0.
def _patched_snac_1_0_0():
    from snac.attention import LocalMHA
    _orig_init = LocalMHA.__init__

    def _patch_init(self, *args, **kw):
        kw["window_size"] = None
        _orig_init(self, *args, **kw)
    LocalMHA.__init__ = _patch_init
    return SNAC


def load_snac_patched(device: str = "cuda"):
    _patched_snac_1_0_0()
    m = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to(device)
    m.eval()
    return m


def decode_audio_ids(snac_model, audio_ids: List[int], device: str = "cuda"):
    n_frames = len(audio_ids) // TOKENS_PER_FRAME
    if n_frames < 1:
        return None
    tokens = audio_ids[: n_frames * TOKENS_PER_FRAME]
    c0, c1, c2 = [], [], []
    for f in range(n_frames):
        base = f * TOKENS_PER_FRAME
        for p in range(TOKENS_PER_FRAME):
            val = tokens[base + p] - AUDIO_BASE - p * CODEBOOK_SIZE
            val = max(0, min(val, CODEBOOK_SIZE - 1))
            if p == 0:
                c0.append(val)
            elif p in (1, 4):
                c1.append(val)
            else:
                c2.append(val)
    dev = next(snac_model.parameters()).device
    codes = [
        torch.tensor([c0], device=dev, dtype=torch.long),
        torch.tensor([c1], device=dev, dtype=torch.long),
        torch.tensor([c2], device=dev, dtype=torch.long),
    ]
    with torch.no_grad():
        aud = snac_model.decode(codes)
    return aud[0, 0].float().cpu().numpy()


def estimate_f0(pcm: np.ndarray, sr: int = SR,
                frame_ms: int = 25, hop_ms: int = 10,
                fmin: float = 60.0, fmax: float = 400.0) -> np.ndarray:
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


def classify_voice(f0s):
    if len(f0s) < 5:
        return ("unknown", 0.0, 0.0)
    med = float(np.median(f0s))
    p05 = float(np.percentile(f0s, 5))
    if med >= 170 and p05 >= 140:
        cls = "female-kavya"
    elif med <= 150 or p05 <= 110:
        cls = "male-drift"
    else:
        cls = "ambiguous"
    return (cls, med, p05)


def main():
    inp = os.environ.get("INPUT", "/home/ubuntu/historical_l4_results.json")
    out = os.environ.get("OUTPUT", "/home/ubuntu/historical_l4_results_with_f0.json")
    print(f"Loading {inp} ...")
    data = json.load(open(inp))

    print("Loading patched SNAC 1.0.0 ...")
    snac_model = load_snac_patched("cuda")
    print("SNAC ready.")

    for run_name in list(data["runs"].keys()):
        run = data["runs"][run_name]
        print(f"=== {run_name} ({len(run['records'])} records) ===")
        for rec in run["records"]:
            pcm = decode_audio_ids(snac_model, rec.get("audio_ids_full") or [])
            f0s = estimate_f0(pcm) if pcm is not None else np.array([])
            cls, med, p05 = classify_voice(f0s)
            rec["class"] = cls
            rec["f0_med"] = med
            rec["f0_p05"] = p05
            if pcm is not None:
                rec["audio_len_samples"] = int(len(pcm))
                rec["audio_sha16"] = hashlib.sha256(
                    pcm.astype(np.float32).tobytes()
                ).hexdigest()[:16]
            print(f"  [{rec['idx']:2d}][{rec['bucket']:<9s}] cls={cls:12s} med={med:.1f} p05={p05:.1f} sha={rec.get('audio_sha16')}")

        # determinism reps
        for d in run.get("determinism", []):
            for r in d["reps"]:
                first21 = r.get("first21") or []
                if not first21:
                    continue
                # We stored only first21 for reps; that's not enough for full audio.
                # Try — 21 tokens = 3 super-frames, enough for classification when text is short.
                # But historically we only kept first21 to save space. Skip full decode for reps.
                pass
    with open(out, "w") as f:
        json.dump(data, f, default=str, indent=1)
    print(f"WROTE {out}, size={os.path.getsize(out)} bytes")


if __name__ == "__main__":
    main()
