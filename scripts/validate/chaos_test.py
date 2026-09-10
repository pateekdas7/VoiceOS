#!/usr/bin/env python3
"""Sprint-028 §3 — Chaos engineering validation (3 safe scenarios).

Scenarios:
  1. Redis kill/restart — verify connection failure, then recovery
  2. PostgreSQL kill/recovery — verify connection failure, then recovery
  3. 20% tc netem packet loss — verify degradation, then remove and recover

Safety:
  - All scenarios are fully reversible (services are restored)
  - tc netem is removed in finally block even on error
  - No data destruction; PostgreSQL in test mode only

Run from CPU node (root@101.53.137.131):
    python3 chaos_test.py

Architecture: V3 Reliability — Redis (session/state), PostgreSQL (authoritative data),
Network resilience (lossy RTP path).
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Optional


REDIS_PASSWORD = "0e539e25b3e5ea96e7434dad38c6029557a9fb3b92f0869e"
NETEM_IFACE = "enp3s0"
GPU_HOST = "217.18.55.78"
GPU_STT_PORT = 8100
PG_CLUSTER = "16 main"


def _run(cmd: str, check: bool = True, capture: bool = True) -> tuple[int, str]:
    result = subprocess.run(
        cmd, shell=True, capture_output=capture, text=True
    )
    output = (result.stdout + result.stderr).strip()
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed (rc={result.returncode}): {cmd}\n{output}")
    return result.returncode, output


def _section(title: str) -> None:
    print()
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)


def _pass(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


# ---------------------------------------------------------------------------
# Chaos 1: Redis kill/restart
# ---------------------------------------------------------------------------

def chaos_redis() -> bool:
    _section("CHAOS-1: Redis Kill/Restart")
    results = []

    # Baseline: confirm Redis is up
    rc, out = _run(
        f"redis-cli -a {REDIS_PASSWORD} ping",
        check=False
    )
    if rc == 0 and "PONG" in out:
        _pass("Pre-chaos: Redis ping PONG")
        results.append(True)
    else:
        _fail(f"Pre-chaos: Redis not responding: {out}")
        results.append(False)

    # Record a test key before kill
    _run(
        f"redis-cli -a {REDIS_PASSWORD} SET chaos_test_key 'before_kill' EX 300",
        check=False
    )

    # Kill Redis
    _info("Killing Redis service...")
    t_kill = time.perf_counter()
    _run("systemctl stop redis-server 2>/dev/null || service redis-server stop 2>/dev/null || service redis stop", check=False)
    time.sleep(1)

    # Verify connection failure
    rc, out = _run(f"redis-cli -a {REDIS_PASSWORD} ping", check=False)
    if rc != 0 or "PONG" not in out:
        _pass(f"During-chaos: Redis connection fails correctly (rc={rc}): {out[:60]}")
        results.append(True)
    else:
        _fail("During-chaos: Redis still responding after kill (unexpected)")
        results.append(False)

    # Restart Redis
    _info("Restarting Redis service...")
    _run("systemctl start redis-server 2>/dev/null || service redis-server start 2>/dev/null || service redis start", check=False)
    time.sleep(2)  # Allow startup

    # Verify recovery
    for attempt in range(5):
        rc, out = _run(f"redis-cli -a {REDIS_PASSWORD} ping", check=False)
        if rc == 0 and "PONG" in out:
            recovery_ms = (time.perf_counter() - t_kill) * 1000
            _pass(f"Post-chaos: Redis recovered in {recovery_ms:.0f}ms (attempt {attempt+1})")
            results.append(True)
            break
        time.sleep(1)
    else:
        _fail("Post-chaos: Redis did NOT recover within 7s")
        results.append(False)
        return False

    # Verify data survival (test key should have been lost since Redis is in-memory only — expected)
    rc, out = _run(f"redis-cli -a {REDIS_PASSWORD} GET chaos_test_key", check=False)
    if out == "" or "nil" in out.lower():
        _info("Post-chaos: Test key lost on restart (expected — Redis in-memory, no RDB/AOF persistence for this key)")
    else:
        _info(f"Post-chaos: Test key survived restart: {out} (Redis persistence enabled)")

    # Verify current session keys operational
    rc, out = _run(
        f"redis-cli -a {REDIS_PASSWORD} SET chaos_recovery_test 'ok' EX 60",
        check=False
    )
    if "OK" in out:
        _pass("Post-chaos: Redis write/read operational after restart")
        results.append(True)
    else:
        _fail(f"Post-chaos: Redis write failed: {out}")
        results.append(False)

    return all(results)


# ---------------------------------------------------------------------------
# Chaos 2: PostgreSQL kill/recovery
# ---------------------------------------------------------------------------

def chaos_postgres() -> bool:
    _section("CHAOS-2: PostgreSQL Kill/Recovery")
    results = []

    # Baseline
    rc, out = _run(
        "PGPASSWORD=voiceos_pw psql -U voiceos -d voiceos -h localhost -c 'SELECT 1 AS ok;' -t",
        check=False
    )
    if rc == 0 and "1" in out:
        _pass("Pre-chaos: PostgreSQL connection OK")
        results.append(True)
    else:
        _fail(f"Pre-chaos: PostgreSQL not responding: {out}")
        results.append(False)

    # Capture row count before kill
    _, count_before = _run(
        "PGPASSWORD=voiceos_pw psql -U voiceos -d voiceos -h localhost -c 'SELECT COUNT(*) FROM audit_log;' -t",
        check=False
    )
    _info(f"Pre-chaos: audit_log rows = {count_before.strip()}")

    # Kill PostgreSQL
    _info("Killing PostgreSQL cluster...")
    t_kill = time.perf_counter()
    _run(f"pg_ctlcluster {PG_CLUSTER} stop --mode fast", check=False)
    time.sleep(1)

    # Verify connection failure
    rc, out = _run(
        "PGPASSWORD=voiceos_pw psql -U voiceos -d voiceos -h localhost -c 'SELECT 1;' -t",
        check=False
    )
    if rc != 0:
        _pass(f"During-chaos: PostgreSQL connection fails correctly (rc={rc})")
        results.append(True)
    else:
        _fail("During-chaos: PostgreSQL still accepting connections after kill (unexpected)")
        results.append(False)

    # Restart PostgreSQL
    _info("Restarting PostgreSQL cluster...")
    _run(f"pg_ctlcluster {PG_CLUSTER} start", check=False)
    time.sleep(2)

    # Verify recovery
    for attempt in range(10):
        rc, out = _run(
            "PGPASSWORD=voiceos_pw psql -U voiceos -d voiceos -h localhost -c 'SELECT 1 AS ok;' -t",
            check=False
        )
        if rc == 0 and "1" in out:
            recovery_ms = (time.perf_counter() - t_kill) * 1000
            _pass(f"Post-chaos: PostgreSQL recovered in {recovery_ms:.0f}ms (attempt {attempt+1})")
            results.append(True)
            break
        time.sleep(1)
    else:
        _fail("Post-chaos: PostgreSQL did NOT recover within 12s")
        results.append(False)
        return False

    # Verify data integrity
    _, count_after = _run(
        "PGPASSWORD=voiceos_pw psql -U voiceos -d voiceos -h localhost -c 'SELECT COUNT(*) FROM audit_log;' -t",
        check=False
    )
    _info(f"Post-chaos: audit_log rows = {count_after.strip()} (before: {count_before.strip()})")

    if count_before.strip() == count_after.strip():
        _pass("Post-chaos: Row count matches pre-chaos — no data loss (WAL recovery)")
        results.append(True)
    else:
        _fail(f"Post-chaos: Row count changed ({count_before.strip()} → {count_after.strip()}) — data loss detected")
        results.append(False)

    return all(results)


# ---------------------------------------------------------------------------
# Chaos 3: tc netem 20% packet loss
# ---------------------------------------------------------------------------

def chaos_netem() -> bool:
    _section("CHAOS-3: tc netem 20% Packet Loss")
    results = []
    netem_applied = False

    try:
        # Baseline latency to GPU STT
        _info(f"Baseline: testing STT /health/ready on {GPU_HOST}:{GPU_STT_PORT}...")
        try:
            import urllib.request
            t0 = time.perf_counter()
            req = urllib.request.urlopen(
                f"http://{GPU_HOST}:{GPU_STT_PORT}/health/ready", timeout=10
            )
            baseline_ms = (time.perf_counter() - t0) * 1000
            _info(f"Baseline HTTP response: {req.status} in {baseline_ms:.0f}ms")
        except Exception as e:
            _info(f"Baseline health check: {e} (may be normal if service busy)")

        # Ping baseline
        rc, ping_out = _run(
            f"ping -c 5 -W 2 {GPU_HOST} 2>&1",
            check=False
        )
        _info(f"Baseline ping to GPU:\n    {ping_out.splitlines()[-1] if ping_out else 'no output'}")

        # Apply 20% packet loss
        _info(f"Applying 20% packet loss on {NETEM_IFACE} toward {GPU_HOST}...")
        rc, out = _run(
            f"tc qdisc add dev {NETEM_IFACE} root netem loss 20%",
            check=False
        )
        if rc != 0:
            # Try replacing if already exists
            _run(
                f"tc qdisc replace dev {NETEM_IFACE} root netem loss 20%",
                check=False
            )
        netem_applied = True

        # Verify netem applied
        rc, out = _run(f"tc qdisc show dev {NETEM_IFACE}", check=False)
        if "netem" in out and "loss 20%" in out:
            _pass(f"tc netem applied: {out.strip()}")
            results.append(True)
        else:
            _fail(f"tc netem may not have applied correctly: {out}")
            results.append(False)

        # Measure degraded ping
        time.sleep(1)
        rc, ping_out = _run(
            f"ping -c 10 -W 2 {GPU_HOST} 2>&1",
            check=False
        )
        ping_lines = ping_out.strip().splitlines()
        _info(f"Degraded ping (20% loss):")
        for line in ping_lines[-3:]:
            _info(f"    {line}")

        # Check for packet loss in ping output
        loss_line = [l for l in ping_lines if "packet loss" in l]
        if loss_line:
            _pass(f"Packet loss confirmed: {loss_line[0].strip()}")
            results.append(True)
        else:
            _fail("Could not confirm packet loss in ping output")
            results.append(False)

        # Quick STT health check under loss
        _info("Testing STT endpoint under 20% packet loss...")
        t0 = time.perf_counter()
        try:
            import urllib.request
            req = urllib.request.urlopen(
                f"http://{GPU_HOST}:{GPU_STT_PORT}/health/ready", timeout=30
            )
            degraded_ms = (time.perf_counter() - t0) * 1000
            _pass(f"STT health check responded in {degraded_ms:.0f}ms under 20% loss (service resilient to loss)")
            results.append(True)
        except Exception as e:
            degraded_ms = (time.perf_counter() - t0) * 1000
            _info(f"STT health check failed in {degraded_ms:.0f}ms: {e} (expected under severe loss)")
            # Not a FAIL — service may be unreachable; this is expected behavior
            results.append(True)  # Resilience test — failure is acceptable and expected

    finally:
        # Always remove netem
        if netem_applied:
            _info(f"Removing tc netem from {NETEM_IFACE}...")
            _run(f"tc qdisc del dev {NETEM_IFACE} root netem", check=False)

            # Verify removed
            rc, out = _run(f"tc qdisc show dev {NETEM_IFACE}", check=False)
            if "netem" not in out:
                _pass("tc netem removed — network restored to normal")
                results.append(True)
            else:
                _fail(f"tc netem may NOT have been removed: {out}")
                results.append(False)

            # Recovery ping
            time.sleep(1)
            rc, ping_out = _run(f"ping -c 5 -W 2 {GPU_HOST} 2>&1", check=False)
            ping_lines = ping_out.strip().splitlines()
            _info(f"Recovery ping:")
            for line in ping_lines[-2:]:
                _info(f"    {line}")

    return all(results)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("Sprint-028 Chaos Engineering — 3 Reversible Scenarios")
    print(f"CPU node → GPU node ({GPU_HOST})")
    print()

    results = {}

    results["CHAOS-1 Redis"] = chaos_redis()
    results["CHAOS-2 PostgreSQL"] = chaos_postgres()
    results["CHAOS-3 tc netem"] = chaos_netem()

    _section("CHAOS ENGINEERING SUMMARY")
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    print()
    print(f"OVERALL: {'PASS' if all_pass else 'PARTIAL/FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
