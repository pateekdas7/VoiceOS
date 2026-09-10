#!/usr/bin/env python3
"""V2 validation analysis: regression matrix + category grades + overlays."""
import json, sys
from collections import defaultdict, Counter

RESULTS = "/data/data/com.termux/files/home/a6000_forensic/part_g_v2validation/v2_validation_results.json"

d = json.load(open(RESULTS))
rows = d["rows"]

# Split by wrapper family
def norm_wrapper(w):
    if w in ("V1", "V2"):
        return w
    if w.startswith("V2+UPI"):
        return "V2+UPI"
    if w.startswith("V2+Deva"):
        return "V2+Deva"
    return w

by_key_wrap_seed = {}
for r in rows:
    by_key_wrap_seed[(r["key"], r["wrapper"], r["seed"])] = r

# For V1/V2 comparison: match on key across 2 seeds
keys = sorted({r["key"] for r in rows if r["wrapper"] in ("V1", "V2")})

def cls_of(key, wrapper, seed):
    r = by_key_wrap_seed.get((key, wrapper, seed))
    return r["class"] if r else None

def is_female(c): return c == "female-kavya"
def is_male(c): return c == "male-drift"

# Per-key aggregation: seed-set outcomes
per_key = []
for k in keys:
    v1s = [cls_of(k, "V1", s) for s in (42, 43)]
    v2s = [cls_of(k, "V2", s) for s in (42, 43)]
    if None in v1s or None in v2s:
        continue
    def code(cs):
        f = sum(1 for c in cs if is_female(c))
        m = sum(1 for c in cs if is_male(c))
        a = sum(1 for c in cs if c == "ambiguous")
        return f, m, a
    v1f, v1m, v1a = code(v1s)
    v2f, v2m, v2a = code(v2s)
    # category from any row
    cat = by_key_wrap_seed[(k, "V1", 42)]["category"]
    text = by_key_wrap_seed[(k, "V1", 42)]["text"]
    per_key.append(dict(key=k, cat=cat, text=text,
                        v1=v1s, v2=v2s,
                        v1f=v1f, v1m=v1m, v1a=v1a,
                        v2f=v2f, v2m=v2m, v2a=v2a))

# Aggregate by category
cat_stats = defaultdict(lambda: dict(n=0, v1F=0, v1M=0, v1A=0, v2F=0, v2M=0, v2A=0,
                                     fixed=0, regressed=0, both_male=0, both_female=0,
                                     partial_fix=0, partial_regress=0))
for r in per_key:
    s = cat_stats[r["cat"]]
    s["n"] += 1
    s["v1F"] += r["v1f"]; s["v1M"] += r["v1m"]; s["v1A"] += r["v1a"]
    s["v2F"] += r["v2f"]; s["v2M"] += r["v2m"]; s["v2A"] += r["v2a"]
    # per-seed diff
    for s42, s43 in [((r["v1"][0], r["v2"][0]), (r["v1"][1], r["v2"][1]))]:
        pass
    for i in range(2):
        v1c, v2c = r["v1"][i], r["v2"][i]
        if is_male(v1c) and is_female(v2c):
            s["fixed"] += 1
        elif is_female(v1c) and is_male(v2c):
            s["regressed"] += 1
        elif is_male(v1c) and is_male(v2c):
            s["both_male"] += 1
        elif is_female(v1c) and is_female(v2c):
            s["both_female"] += 1

# Overall stats
tot = dict(n=0, v1F=0, v1M=0, v1A=0, v2F=0, v2M=0, v2A=0,
           fixed=0, regressed=0, both_male=0, both_female=0)
for s in cat_stats.values():
    for k in tot: tot[k] += s.get(k, 0)

print("=" * 80)
print("V1 vs V2 REGRESSION MATRIX (per-seed pairs, N =", tot["n"]*2, ")")
print("=" * 80)
print(f"Total unique texts: {tot['n']}")
print()
print(f"V1 outcomes: F={tot['v1F']} M={tot['v1M']} A={tot['v1A']}  (male-rate={100*tot['v1M']/(2*tot['n']):.1f}%)")
print(f"V2 outcomes: F={tot['v2F']} M={tot['v2M']} A={tot['v2A']}  (male-rate={100*tot['v2M']/(2*tot['n']):.1f}%)")
print()
print(f"V1→V2 transitions (seed pairs):")
print(f"  FIXED   (M→F): {tot['fixed']}")
print(f"  REGRESS (F→M): {tot['regressed']}")
print(f"  Both M→M:      {tot['both_male']}")
print(f"  Both F→F:      {tot['both_female']}")
print()
print("=" * 80)
print("PER-CATEGORY")
print("=" * 80)
print(f"{'CAT':<5}{'N':>4}  {'V1F':>3}/{'V1M':>3}/{'V1A':>3}  {'V2F':>3}/{'V2M':>3}/{'V2A':>3}  {'FIX':>4}{'REG':>4}  V1_male%  V2_male%")
for cat in sorted(cat_stats.keys()):
    s = cat_stats[cat]
    v1r = 100*s["v1M"]/(2*s["n"])
    v2r = 100*s["v2M"]/(2*s["n"])
    print(f"{cat:<5}{s['n']:>4}  {s['v1F']:>3}/{s['v1M']:>3}/{s['v1A']:>3}  {s['v2F']:>3}/{s['v2M']:>3}/{s['v2A']:>3}  {s['fixed']:>4}{s['regressed']:>4}  {v1r:>7.1f}%  {v2r:>7.1f}%")

print()
print("=" * 80)
print("V2 REMAINING FAILURES (V2 male-drift at seed 42 OR seed 43)")
print("=" * 80)
for r in per_key:
    if r["v2m"] > 0:
        print(f"  [{r['cat']}] {r['key']:<25} V1={r['v1']} V2={r['v2']}  '{r['text'][:60]}'")

print()
print("=" * 80)
print("V2 REGRESSIONS vs V1 (V1 both-female → V2 any-male)")
print("=" * 80)
regs = 0
for r in per_key:
    if r["v1m"] == 0 and r["v2m"] > 0:
        regs += 1
        print(f"  [{r['cat']}] {r['key']:<25} V1={r['v1']} V2={r['v2']}  '{r['text'][:60]}'")
print(f"Total pure regressions: {regs}")

print()
print("=" * 80)
print("V1→V2 FIXES (V1 any-male → V2 no-male)")
print("=" * 80)
fixes = 0
for r in per_key:
    if r["v1m"] > 0 and r["v2m"] == 0:
        fixes += 1
        print(f"  [{r['cat']}] {r['key']:<25} V1={r['v1']} V2={r['v2']}  '{r['text'][:60]}'")
print(f"Total full fixes: {fixes}")

print()
print("=" * 80)
print("UPI OVERLAY (V2 vs V2+UPI at seed 42)")
print("=" * 80)
upi_rows = [r for r in rows if r["wrapper"] == "V2+UPI"]
for u in upi_rows:
    # match by category+key stem; UPI keys are same as base key
    base_key = u["key"]
    v2_row = by_key_wrap_seed.get((base_key, "V2", 42))
    v1_row = by_key_wrap_seed.get((base_key, "V1", 42))
    print(f"  {base_key:<25}  V1={v1_row['class'] if v1_row else '-'}  V2={v2_row['class'] if v2_row else '-'}  V2+UPI={u['class']}")

print()
print("=" * 80)
print("DEVA OVERLAY (diagnostic — separate synthetic keys)")
print("=" * 80)
deva_rows = [r for r in rows if r["wrapper"] == "V2+Deva"]
for u in deva_rows:
    print(f"  {u['key']:<25}  V2+Deva={u['class']}  med_f0={u['median_f0']:.1f}  text='{u['text'][:70]}'")
