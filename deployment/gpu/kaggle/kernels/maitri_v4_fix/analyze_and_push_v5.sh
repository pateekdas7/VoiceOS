#!/usr/bin/env bash
# Called after v4 completes. Downloads output JSON, finds optimal lock_tokens,
# pushes v5 with a hard token-blacklist fix if locking alone didn't work.
set -e
cd /data/data/com.termux/files/home/maitri_v4_fix

echo "=== Downloading v4 output ==="
kaggle kernels output prateek777777/voiceos-maitri-voice-switch-fix-v4 -p ./v4_output 2>&1 || true

RESULTS_FILE="./v4_output/v4_speaker_lock_results.json"

if [ ! -f "$RESULTS_FILE" ]; then
    echo "RESULTS FILE NOT FOUND — checking logs for inline summary"
    kaggle kernels logs prateek777777/voiceos-maitri-voice-switch-fix-v4 2>&1 | grep -E "SUMMARY|VERDICT|switches|lock=" | tail -40
    exit 1
fi

echo "=== v4 Results ==="
python3 - <<'PYEOF'
import json, sys

with open("./v4_output/v4_speaker_lock_results.json") as f:
    results = json.load(f)

V3_BASELINE = {"P01": 3, "P02": 22}  # known; others TBD

print("\n=== DRIFT COMPARISON TABLE ===")
texts = ["P01", "P02", "P03", "P04", "P05", "P06"]
lock_vals = sorted(int(k) for k in results.keys())

header = f"{'Text':<6} | {'v3(broken)':>10}" + "".join(f" | {'lock='+str(n):>10}" for n in lock_vals)
print(header)
print("-" * len(header))

optimal = None
for text in texts:
    baseline = V3_BASELINE.get(text, "?")
    row = f"{text:<6} | {str(baseline)+'/30':>10}"
    for n in lock_vals:
        val = results.get(str(n), {}).get(text, {})
        sw = val.get("n_switch", "?")
        row += f" | {str(sw)+'/30':>10}"
    print(row)

print()
for n in lock_vals:
    total = sum(results[str(n)].get(t, {}).get("n_switch", 999) for t in texts)
    all_clean = all(results[str(n)].get(t, {}).get("n_switch", 1) == 0 for t in texts)
    status = "ALL CLEAN ✓" if all_clean else f"total_drift={total}"
    print(f"  lock={n}: {status}")
    if all_clean and optimal is None:
        optimal = n

if optimal:
    print(f"\nVERDICT: lock_tokens={optimal} ELIMINATES ALL DRIFT — ready for production")
    with open("/tmp/v4_optimal", "w") as f:
        f.write(str(optimal))
else:
    # Find the lock level with least drift to guide next step
    best_n = min(lock_vals, key=lambda n: sum(
        results[str(n)].get(t, {}).get("n_switch", 999) for t in texts
    ))
    total_best = sum(results[str(best_n)].get(t, {}).get("n_switch", 0) for t in texts)
    print(f"\nNO CLEAN SOLUTION — best is lock={best_n} with {total_best} total drifts")
    print("NEXT: v5 will add hard token-blacklist on top of lock")
    with open("/tmp/v4_optimal", "w") as f:
        f.write(f"NONE_best={best_n}")
PYEOF

OPTIMAL=$(cat /tmp/v4_optimal 2>/dev/null || echo "UNKNOWN")
echo ""
echo "=== OPTIMAL: $OPTIMAL ==="

if echo "$OPTIMAL" | grep -q "^NONE"; then
    echo "Drift remains — triggering v5 build with hard blacklist"
    # v5 build happens in Python below
    python3 /data/data/com.termux/files/home/maitri_v4_fix/build_v5.py
    cd /data/data/com.termux/files/home/maitri_v5_fix
    python3 build_ipynb.py
    kaggle kernels push -p . --accelerator NvidiaTeslaT4
    echo "V5_PUSHED"
else
    echo "Fix validated at lock_tokens=$OPTIMAL — no v5 needed"
    echo "NEXT STEP: Apply SpeakerLockProcessor(lock_tokens=$OPTIMAL, boot_temp=0.05) to production server.py"
fi
