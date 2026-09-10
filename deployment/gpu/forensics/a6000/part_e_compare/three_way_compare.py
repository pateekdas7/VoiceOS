#!/usr/bin/env python3
"""
Three-way comparison:  T4 (X4-B baseline)  vs  A6000 (this session)  vs  L4 (Git reconstruction)

Reads:
  - /root/x4b_results.json                             (T4 BF16 baseline from X4-B)
  - /root/a6000_forensic_results.json                  (A6000 multi-precision runs)
  - part_b_l4/L4_RECONSTRUCTION.md                     (textual L4 facts — no numerical audio data)

Produces a comparison table + per-text first-token-divergence audit.
Historical L4 has NO archived per-text data; comparisons involving L4 are limited
to code-path and config-level facts.
"""

import hashlib
import json
import os
import sys
from collections import defaultdict


T4_PATH = os.environ.get("T4_PATH", "/root/x4b_results.json")
A6000_PATH = os.environ.get("A6000_PATH", "/root/a6000_forensic_results.json")


def load_json(path: str):
    if not os.path.exists(path):
        print(f"MISSING: {path}", file=sys.stderr)
        return None
    with open(path) as f:
        return json.load(f)


def first_divergence(seq_a, seq_b) -> int:
    """Return position i where seq_a[i] != seq_b[i], or min(len(a), len(b))."""
    for i, (a, b) in enumerate(zip(seq_a, seq_b)):
        if a != b:
            return i
    return min(len(seq_a), len(seq_b))


def main():
    t4 = load_json(T4_PATH)
    a6 = load_json(A6000_PATH)
    if t4 is None or a6 is None:
        print("Cannot compare without both files.")
        sys.exit(1)

    print("=" * 78)
    print("THREE-WAY COMPARISON: T4 (X4-B BF16) vs A6000 (this session) vs L4 (Git)")
    print("=" * 78)

    # Env delta
    t4_env = t4["test_a_bf16"]["env"]
    for run_name, run in a6["runs"].items():
        if "env" not in run:
            continue
        a_env = run["env"]
        print(f"\n--- ENV {run_name} vs T4_BF16 ---")
        for k in ["gpu_name", "gpu_cap", "torch", "cuda", "cudnn", "transformers",
                 "bf16_supported", "tf32_matmul_allow", "matmul_precision",
                 "attn_impl_resolved", "model_commit_hash"]:
            print(f"  {k:25s}  T4={t4_env.get(k)!s:30s}  A6000={a_env.get(k)!s}")

    # Per-text same-seed comparison
    t4_recs = {r["idx"]: r for r in t4["test_a_bf16"]["records"]}
    print("\n=== PER-TEXT COMPARISON (same seed=42) ===")
    header = f"{'idx':>3} {'bucket':<9} {'text':<50}  T4_cls -> A6000_cls_by_run"
    print(header)
    print("-" * len(header))
    for idx, t4r in sorted(t4_recs.items()):
        cls_by_run = []
        for run_name, run in a6["runs"].items():
            if "records" not in run:
                continue
            for ar in run["records"]:
                if ar["idx"] == idx:
                    cls_by_run.append(f"{run_name}:{ar['class']}")
                    break
        text = t4r["text"][:48]
        print(f"{idx:>3} {t4r['bucket']:<9} {text:<50}  "
              f"{t4r['class']:12s} -> " + ", ".join(cls_by_run))

    # First-token-divergence: T4 audio_ids vs each A6000 run audio_ids
    print("\n=== FIRST-TOKEN DIVERGENCE (T4 vs each A6000 run) ===")
    for run_name, run in a6["runs"].items():
        if "records" not in run:
            continue
        a_recs = {r["idx"]: r for r in run["records"]}
        for idx, t4r in sorted(t4_recs.items()):
            ar = a_recs.get(idx)
            if not ar:
                continue
            t4_ids = t4r.get("audio_ids_full") or []
            a_ids = ar.get("audio_ids_full") or []
            div = first_divergence(t4_ids, a_ids)
            same_frame = div // 7
            same_pos = div % 7
            match = "MATCH" if div == min(len(t4_ids), len(a_ids)) and \
                len(t4_ids) == len(a_ids) else "DIVERGE"
            print(f"  [{run_name}][{idx:2d}] div_at_audio_token={div} "
                  f"(frame={same_frame} pos={same_pos}) "
                  f"len(T4)={len(t4_ids)} len(A6000)={len(a_ids)} {match}")

    # Cos-to-kavya delta (X4-A speaker embedding degeneracy check)
    print("\n=== SPEAKER-EMBEDDING FINGERPRINT (kavya cos-to-others) ===")
    t4_cos = t4["test_a_bf16"]["spk_embed_analysis"]["cos_to_kavya"]
    print(f"  T4_BF16       : {json.dumps(t4_cos, indent=2)}")
    for run_name, run in a6["runs"].items():
        if "spk_embed_analysis" in run:
            a_cos = run["spk_embed_analysis"]["cos_to_kavya"]
            print(f"  {run_name:30s}: {json.dumps(a_cos, indent=2)}")

    # Determinism check
    print("\n=== DETERMINISM (unique audio SHA over 3 reps per critical idx) ===")
    for run_name, run in a6["runs"].items():
        if "determinism" not in run:
            continue
        for d in run["determinism"]:
            print(f"  [{run_name}][idx={d['idx']}] unique_sha={d['unique_sha16']} "
                  f"unique_first21={d['unique_first21_audio']} "
                  f"classes={[r['class'] for r in d['reps']]}")


if __name__ == "__main__":
    main()
