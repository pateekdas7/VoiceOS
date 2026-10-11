#!/usr/bin/env python3
"""Analyze four_approach_results.json → regression matrix + score."""
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Tuple

INP = os.environ.get("INP", "/data/data/com.termux/files/home/a6000_forensic/part_f_fourapproach/four_approach_results.json")
OUT = os.environ.get("OUT", "/data/data/com.termux/files/home/a6000_forensic/part_f_fourapproach/analysis.json")

data = json.load(open(INP))
rows = data["rows"]
det = data.get("determinism", [])
tok = data.get("tokenizer_diagnostic", {})

# Group by key → list of records across seeds
by_key: Dict[str, List[Dict]] = defaultdict(list)
for r in rows:
    by_key[r["key"]].append(r)

def dominant(records):
    """Majority class across seeds; ties → 'ambiguous'."""
    counts = defaultdict(int)
    for r in records:
        counts[r["class"]] += 1
    if not counts:
        return None, {}
    top = max(counts, key=counts.get)
    return top, dict(counts)

def any_male(records):
    return sum(1 for r in records if r["class"] == "male-drift")

def all_female(records):
    return all(r["class"] == "female-kavya" for r in records)

# ---- Baseline per-key summary ----
baseline_keys = [k for k in by_key if k.startswith("B")]
baseline_state = {}
for k in baseline_keys:
    recs = by_key[k]
    dom, counts = dominant(recs)
    baseline_state[k] = {
        "text": recs[0]["text"],
        "sub": recs[0]["sub"],
        "seeds_male": any_male(recs),
        "n_seeds": len(recs),
        "dominant": dom,
        "counts": counts,
        "med_by_seed": {r["seed"]: r["median_f0"] for r in recs},
        "p05_by_seed": {r["seed"]: r["p05_f0"] for r in recs},
    }

# Overall baseline drift stats
n_bl = len(baseline_state)
bl_dominant_male = sum(1 for v in baseline_state.values() if v["dominant"] == "male-drift")
bl_any_male     = sum(1 for v in baseline_state.values() if v["seeds_male"] > 0)
bl_seeds_male_total = sum(v["seeds_male"] for v in baseline_state.values())
bl_seeds_total = sum(v["n_seeds"] for v in baseline_state.values())

# ---- Pair each variant to its baseline counterpart ----
# Mapping — variant_key → baseline_key
BL_MAP = {
    # DEVANAGARI (Dxx maps to Bxx)
    **{f"D00_mixed": "B00", "D00_full": "B00",
       "D01_mixed": "B01", "D01_full": "B01",
       "D02_mixed": "B02", "D02_full": "B02",
       "D03_mixed": "B03", "D03_full": "B03",
       "D04_mixed": "B04", "D04_full": "B04",
       "D07_mixed": "B07", "D07_full": "B07",
       "D08_mixed": "B08", "D08_full": "B08",
       "D09_mixed": "B09", "D09_full": "B09",
       "D11_mixed": "B11", "D11_full": "B11",
       "D14_mixed": "B14", "D14_full": "B14"},
    # SPELLING (dhanyavaad → B07 as its baseline)
    "S_DV_01": "B07", "S_DV_02": "B07", "S_DV_03": "B07",
    "S_DV_04": "B07", "S_DV_05": "B07", "S_DV_06": "B07",
    # namaste → B06
    "S_NM_01": "B06", "S_NM_02": "B06", "S_NM_03": "B06", "S_NM_04": "B06",
    # kripya → B11
    "S_KP_01": "B11", "S_KP_02": "B11", "S_KP_03": "B11",
    # ACRONYM — UPI variants map to B01
    "A_UPI_raw": "B01", "A_UPI_spelled": "B01", "A_UPI_deva": "B01",
    # OTP/SMS/EMI have no baseline — treated as new, mapped to self-raw
    "A_OTP_raw": "A_OTP_raw", "A_OTP_spelled": "A_OTP_raw", "A_OTP_deva": "A_OTP_raw",
    "A_SMS_raw": "A_SMS_raw", "A_SMS_spelled": "A_SMS_raw", "A_SMS_deva": "A_SMS_raw",
    "A_EMI_raw": "A_EMI_raw", "A_EMI_spelled": "A_EMI_raw", "A_EMI_deva": "A_EMI_raw",
    # PROMPT — payload maps to its baseline B0x
    "P_UPI_V1": "B01", "P_UPI_V2": "B01", "P_UPI_V3": "B01", "P_UPI_V4": "B01",
    "P_OUT24k_V1": "B02", "P_OUT24k_V2": "B02", "P_OUT24k_V3": "B02", "P_OUT24k_V4": "B02",
    "P_NAM_V1": "B06", "P_NAM_V2": "B06", "P_NAM_V3": "B06", "P_NAM_V4": "B06",
    "P_DV_V1": "B07", "P_DV_V2": "B07", "P_DV_V3": "B07", "P_DV_V4": "B07",
    "P_KRIPA_V1": "B11", "P_KRIPA_V2": "B11", "P_KRIPA_V3": "B11", "P_KRIPA_V4": "B11",
}

# ---- Per-approach net effect ----
def approach_summary(prefix_predicate, approach_name):
    """Aggregate over rows in this approach."""
    rows_here = [(k, v) for k, v in by_key.items() if prefix_predicate(k)]
    total_seeds = 0
    total_male = 0
    imp_male_to_female = 0   # baseline male, variant female
    reg_female_to_male = 0   # baseline female, variant male
    same_female = 0
    same_male = 0
    same_ambig = 0
    per_key = {}
    stable_to_male_examples = []
    male_to_female_examples = []
    for k, recs in rows_here:
        bl = BL_MAP.get(k)
        bl_recs = by_key.get(bl, []) if bl else []
        bl_class_by_seed = {r["seed"]: r["class"] for r in bl_recs}
        for r in recs:
            total_seeds += 1
            if r["class"] == "male-drift":
                total_male += 1
            bl_cls = bl_class_by_seed.get(r["seed"])
            if bl_cls is None:
                continue
            if bl_cls == "male-drift" and r["class"] == "female-kavya":
                imp_male_to_female += 1
                male_to_female_examples.append((k, r["seed"], r["text"][:50]))
            elif bl_cls == "female-kavya" and r["class"] == "male-drift":
                reg_female_to_male += 1
                stable_to_male_examples.append((k, r["seed"], r["text"][:50]))
            elif bl_cls == "female-kavya" and r["class"] == "female-kavya":
                same_female += 1
            elif bl_cls == "male-drift" and r["class"] == "male-drift":
                same_male += 1
            elif bl_cls == "ambiguous" and r["class"] == "ambiguous":
                same_ambig += 1
        dom, counts = dominant(recs)
        per_key[k] = {
            "baseline_key": bl,
            "baseline_dominant": dominant(bl_recs)[0] if bl_recs else None,
            "variant_dominant": dom,
            "variant_counts": counts,
            "text": recs[0]["text"],
        }
    latencies = [r["latency_s"] for _, recs in rows_here for r in recs]
    return {
        "name": approach_name,
        "n_rows": len(rows_here),
        "n_seeds_total": total_seeds,
        "n_male_seeds": total_male,
        "male_rate": total_male / max(1, total_seeds),
        "improvements_male_to_female": imp_male_to_female,
        "regressions_female_to_male": reg_female_to_male,
        "same_female": same_female,
        "same_male": same_male,
        "net_male_change": total_male - bl_seeds_male_total * (total_seeds / max(1, bl_seeds_total)),
        "per_key": per_key,
        "stable_to_male_examples": stable_to_male_examples[:10],
        "male_to_female_examples": male_to_female_examples[:10],
        "median_latency_s": sorted(latencies)[len(latencies)//2] if latencies else None,
    }

devanagari = approach_summary(lambda k: k.startswith("D0") or k.startswith("D1"), "DEVANAGARI")
spelling   = approach_summary(lambda k: k.startswith("S_"), "SPELLING")
acronym    = approach_summary(lambda k: k.startswith("A_"), "ACRONYM")
prompt     = approach_summary(lambda k: k.startswith("P_"), "PROMPT")

# ---- Regression matrix (per baseline text vs approach) ----
matrix = {}
for bk, bstate in baseline_state.items():
    row = {"baseline_dominant": bstate["dominant"], "text": bstate["text"], "sub": bstate["sub"]}
    for approach_prefix, ap_name in [("D", "DEVA_mixed"), ("D", "DEVA_full"),
                                     ("S", "SPELLING"), ("A", "ACRONYM"), ("P", "PROMPT")]:
        pass
    matrix[bk] = row

# ---- Grading ----
def grade(a):
    """PASS iff regressions_female_to_male == 0 AND male_rate < 5% AND at least
    one male→female improvement OR baseline had zero drift to reduce.
    CONDITIONAL iff regressions <=2 AND male_rate < 15%.
    FAIL otherwise."""
    if a["regressions_female_to_male"] == 0 and a["male_rate"] < 0.05 and a["improvements_male_to_female"] > 0:
        return "PASS"
    if a["regressions_female_to_male"] <= 2 and a["male_rate"] < 0.15:
        return "CONDITIONAL"
    return "FAIL"

grades = {
    "DEVANAGARI": grade(devanagari),
    "SPELLING":   grade(spelling),
    "ACRONYM":    grade(acronym),
    "PROMPT":     grade(prompt),
}

# ---- Split DEVANAGARI by mixed vs full ----
deva_mixed = approach_summary(lambda k: k.startswith("D") and k.endswith("mixed"), "DEVA_MIXED")
deva_full  = approach_summary(lambda k: k.startswith("D") and k.endswith("full"),  "DEVA_FULL")
grades["DEVANAGARI_MIXED"] = grade(deva_mixed)
grades["DEVANAGARI_FULL"]  = grade(deva_full)

# ---- Print concise report ----
print("="*80)
print("FOUR-APPROACH FORENSIC — analysis summary")
print("="*80)
print(f"\nBASELINE: {n_bl} texts × 3 seeds = {bl_seeds_total} generations")
print(f"  Male-drift dominant texts: {bl_dominant_male}/{n_bl}")
print(f"  Texts with any male drift: {bl_any_male}/{n_bl}")
print(f"  Male seed rate: {bl_seeds_male_total}/{bl_seeds_total} = {bl_seeds_male_total/bl_seeds_total:.1%}")

for a in [devanagari, deva_mixed, deva_full, spelling, acronym, prompt]:
    print(f"\n=== {a['name']} ===")
    print(f"  rows={a['n_rows']} seeds={a['n_seeds_total']} male_rate={a['male_rate']:.1%}")
    print(f"  improvements male→female: {a['improvements_male_to_female']}")
    print(f"  REGRESSIONS  female→male: {a['regressions_female_to_male']}")
    print(f"  same-female (no change): {a['same_female']}")
    print(f"  same-male   (no change): {a['same_male']}")
    print(f"  median latency: {a['median_latency_s']:.2f}s")
    if a["stable_to_male_examples"]:
        print(f"  REGRESSION examples:")
        for ex in a["stable_to_male_examples"][:5]:
            print(f"    {ex}")
    if a["male_to_female_examples"]:
        print(f"  IMPROVEMENT examples:")
        for ex in a["male_to_female_examples"][:5]:
            print(f"    {ex}")

print("\n=== GRADES ===")
for k, v in grades.items():
    print(f"  {k:20s} = {v}")

# ---- Determinism summary ----
print("\n=== DETERMINISM ===")
for d in det:
    print(f"  {d['key']:16s} unique_sha={d['unique_sha16']} classes={d['classes']}")

# ---- Tokenizer diagnostic key facts ----
print("\n=== TOKENIZER DIAGNOSTIC ===")
print(f"  <spk_kavya> is single token: {tok.get('_spk_kavya_is_single_token')}")
print(f"  <spk_kavya> id: {tok.get('_spk_kavya_id')}")
for probe in ["<spk_kavya> <spk_kavya>", "[kavya]", "<spk_kavya>test", "<spk_apsara>"]:
    if probe in tok:
        d = tok[probe]
        print(f"  {probe!r:38s} → n_ids={d['n']} ids={d['ids']}")

# ---- Write full analysis JSON ----
analysis_out = {
    "baseline_state": baseline_state,
    "approach_summaries": {
        "DEVANAGARI": devanagari,
        "DEVANAGARI_MIXED": deva_mixed,
        "DEVANAGARI_FULL": deva_full,
        "SPELLING": spelling,
        "ACRONYM": acronym,
        "PROMPT": prompt,
    },
    "grades": grades,
    "baseline_stats": {
        "n_texts": n_bl,
        "n_seeds_total": bl_seeds_total,
        "n_dominant_male": bl_dominant_male,
        "n_any_male": bl_any_male,
        "n_seeds_male": bl_seeds_male_total,
        "male_seed_rate": bl_seeds_male_total / bl_seeds_total,
    },
    "determinism": [{"key": d["key"], "unique_sha16": d["unique_sha16"],
                     "unique_first21_audio": d["unique_first21_audio"],
                     "classes": d["classes"], "median_f0s": d["median_f0s"]}
                    for d in det],
    "tokenizer_diagnostic_summary": {
        "spk_kavya_id": tok.get("_spk_kavya_id"),
        "spk_kavya_single_token": tok.get("_spk_kavya_is_single_token"),
        "probes": {p: {"n": tok[p]["n"], "ids": tok[p]["ids"]}
                   for p in tok if not p.startswith("_")},
    },
}
with open(OUT, "w") as f:
    json.dump(analysis_out, f, indent=1, default=str)
print(f"\nWROTE {OUT}")
