#!/usr/bin/env python3
"""Three-way comparison: T4 current | L4 current | L4 historical (Protocol A + B).

Reads:
  T4  : ~/a6000_forensic/part_a_t4/t4_bf16_x4b_results.json
  L4c : ~/a6000_forensic/part_c_a6000/l4_forensic_results.json  (current code)
  L4h : ~/a6000_forensic/part_c_a6000/historical_l4_results.json  (historical code)

Produces:
  - env delta table
  - per-text class + audio_ids first-divergence for each L4 protocol vs T4
  - speaker embed fingerprint deltas
  - determinism table (T4 seeded / L4c seeded / L4h noseed / L4h seed42)
"""
import json
import os
import sys
from typing import Any, Dict, List


T4_PATH = os.environ.get(
    "T4_PATH",
    os.path.expanduser("~/a6000_forensic/part_a_t4/t4_bf16_x4b_results.json"),
)
L4C_PATH = os.environ.get(
    "L4C_PATH",
    os.path.expanduser("~/a6000_forensic/part_c_a6000/l4_forensic_results.json"),
)
L4H_PATH = os.environ.get(
    "L4H_PATH",
    os.path.expanduser("~/a6000_forensic/part_c_a6000/historical_l4_results.json"),
)


def load(p):
    if not os.path.exists(p):
        print(f"MISSING {p}", file=sys.stderr)
        return None
    return json.load(open(p))


AUDIO_BASE = 128266
CODEBOOK_SIZE = 4096
TOKENS_PER_FRAME = 7


def to_codebook(ids):
    """Convert either raw tokens (>=AUDIO_BASE) or codebook vals to codebook vals."""
    out = []
    for i, tid in enumerate(ids):
        if tid >= AUDIO_BASE:
            cb_pos = i % TOKENS_PER_FRAME
            val = tid - AUDIO_BASE - cb_pos * CODEBOOK_SIZE
            out.append(max(0, min(val, CODEBOOK_SIZE - 1)))
        else:
            out.append(int(tid))
    return out


def first_div(a, b):
    a = to_codebook(a)
    b = to_codebook(b)
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def summarise(t4, l4c, l4h):
    print("=" * 88)
    print("THREE-WAY: T4 current | L4 current | L4 historical")
    print("=" * 88)

    # Env delta
    envs = {
        "T4_current": t4["test_a_bf16"]["env"],
        "L4_current": l4c["runs"]["L4_BF16_SDPA_current"]["env"],
        "L4_hist_A": l4h["runs"]["L4_HIST_noseed"]["env"],
        "L4_hist_B": l4h["runs"]["L4_HIST_seed42"]["env"],
    }
    print("\n=== ENV FINGERPRINTS ===")
    keys = ["gpu_name", "gpu_cap", "torch", "cuda", "cudnn", "transformers",
            "bf16_supported", "tf32_matmul_allow", "matmul_precision",
            "attn_impl_resolved", "model_commit_hash"]
    hdr = f"{'field':25s} " + " ".join(f"{n:20s}" for n in envs)
    print(hdr)
    for k in keys:
        row = [f"{str(e.get(k)):20s}" for e in envs.values()]
        print(f"  {k:23s} " + " ".join(row))

    # Speaker embed fingerprint
    print("\n=== SPEAKER EMBEDDING (cos_to_kavya) — should be identical across runs ===")
    for tag, cos in [
        ("T4_current", t4["test_a_bf16"]["spk_embed_analysis"]["cos_to_kavya"]),
        ("L4_current", l4c["runs"]["L4_BF16_SDPA_current"]["spk_embed_analysis"]["cos_to_kavya"]),
        ("L4_hist   ", l4h.get("spk_embed_analysis", {}).get("cos_to_kavya", {})),
    ]:
        maxo = max((v for k, v in cos.items() if k != "kavya" and v is not None),
                   default=0.0)
        print(f"  {tag}: max_other={maxo:.6f}  raw={cos}")

    # Per-text classifications
    print("\n=== PER-TEXT CLASSIFICATIONS ===")
    t4r = {r["idx"]: r for r in t4["test_a_bf16"]["records"]}
    l4cr = {r["idx"]: r for r in l4c["runs"]["L4_BF16_SDPA_current"]["records"]}
    l4hAr = {r["idx"]: r for r in l4h["runs"]["L4_HIST_noseed"]["records"]}
    l4hBr = {r["idx"]: r for r in l4h["runs"]["L4_HIST_seed42"]["records"]}
    hdr = f"{'idx':>3} {'buk':<9} {'text':<48}  {'T4':<13} {'L4c':<13} {'L4hA':<13} {'L4hB':<13}"
    print(hdr)
    print("-" * len(hdr))
    counts = {"T4": {}, "L4c": {}, "L4hA": {}, "L4hB": {}}
    for i in sorted(t4r):
        row = [t4r[i]["class"], l4cr[i]["class"], l4hAr[i]["class"], l4hBr[i]["class"]]
        for tag, cls in zip(["T4", "L4c", "L4hA", "L4hB"], row):
            counts[tag][cls] = counts[tag].get(cls, 0) + 1
        text = t4r[i]["text"][:46]
        print(f"{i:>3} {t4r[i]['bucket']:<9} {text:<48}  "
              f"{row[0]:<13} {row[1]:<13} {row[2]:<13} {row[3]:<13}")
    print("\n=== SUMMARY class-counts ===")
    for tag, cc in counts.items():
        print(f"  {tag}: {cc}")

    # Token-divergence
    print("\n=== TOKEN DIVERGENCE (T4 baseline vs each run — first-divergence audio-token position) ===")
    for tag, recs in [("L4c", l4cr), ("L4hA", l4hAr), ("L4hB", l4hBr)]:
        print(f"\n--- {tag} vs T4 ---")
        matches = 0
        for i in sorted(t4r):
            t_ids = t4r[i].get("audio_ids_full") or []
            r_ids = recs.get(i, {}).get("audio_ids_full") or []
            d = first_div(t_ids, r_ids)
            same_len = (len(t_ids) == len(r_ids))
            match = "MATCH" if d == len(t_ids) and same_len else "DIVERGE"
            if match == "MATCH":
                matches += 1
            print(f"  [{tag}][{i:2d}] div_at={d:3d} frame={d//7} pos={d%7} "
                  f"len(T4)={len(t_ids)} len({tag})={len(r_ids)} {match}")
        print(f"  {tag} matches: {matches}/{len(t4r)}")

    # Determinism
    print("\n=== DETERMINISM (unique per critical idx) ===")
    for tag, run_key in [("L4c", "L4_BF16_SDPA_current"),
                         ("L4hA", "L4_HIST_noseed"),
                         ("L4hB", "L4_HIST_seed42")]:
        src = l4c if tag == "L4c" else l4h
        for d in src["runs"][run_key]["determinism"]:
            classes = [r["class"] for r in d["reps"]]
            print(f"  [{tag}][idx={d['idx']}] unique_sha={d['unique_sha16']} "
                  f"unique_first21={d['unique_first21_audio']} classes={classes}")


def main():
    t4 = load(T4_PATH)
    l4c = load(L4C_PATH)
    l4h = load(L4H_PATH)
    if not (t4 and l4c and l4h):
        sys.exit(1)
    summarise(t4, l4c, l4h)


if __name__ == "__main__":
    main()
