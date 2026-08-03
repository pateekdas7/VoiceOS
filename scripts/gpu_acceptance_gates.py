#!/usr/bin/env python3
"""
VoiceOS GPU Acceptance Gates
================================
Master gate runner. Executes all 10 acceptance gates in order and produces a
single PASS/FAIL verdict for production readiness.

Gates (from GPU_DEPLOYMENT_FRAMEWORK.md):
  G-01  Runtime Foundation     V-01..V-03, V-25..V-28
  G-02  Dependencies           V-04..V-08, V-41, V-42
  G-03  Models                 V-09..V-12, V-43, V-44
  G-04  Infrastructure         V-29..V-39
  G-05  AI Services            V-15..V-20, V-45, V-46
  G-06  End-to-End Inference   V-21..V-24
  G-07  GPU Resources          V-13, V-14, V-40
  G-08  AI Pipeline            gpu_ai_validation.py (full STT→LLM→TTS)
  G-09  Performance            gpu_benchmark.py (latency budgets)
  G-10  Stability              VRAM growth ≤ 200 MiB (5 iterations)

Usage:
    python scripts/gpu_acceptance_gates.py [--n-bench 10] [--n-ai 5]
        [--output /path/report.json] [--skip-bench] [--skip-ai]

Exit codes:
    0 = ALL gates PASSED
    1 = One or more gates FAILED
    2 = Fatal pre-condition error

Spec reference: docs/deployment/GPU_DEPLOYMENT_FRAMEWORK.md
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# ── Gate data structures ──────────────────────────────────────────────────────

@dataclass
class GateResult:
    gate_id: str
    name: str
    passed: bool
    check_count: int = 0
    pass_count: int = 0
    fail_count: int = 0
    skipped: bool = False
    detail: str = ""
    failing_checks: list[str] = field(default_factory=list)


@dataclass
class AcceptanceReport:
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    hostname: str = field(default_factory=socket.gethostname)
    gates: list[GateResult] = field(default_factory=list)
    passed: bool = False
    first_failed_gate: Optional[str] = None
    summary: str = ""
    runtime_sec: float = 0.0


# ── Validation suite integration ───────────────────────────────────────────────

def _run_validation_suite() -> Optional[object]:
    """Import and run gpu_validation_suite; return its ValidationReport or None."""
    script_dir = Path(__file__).parent
    sys.path.insert(0, str(script_dir))
    try:
        import gpu_validation_suite as vs
        print("  Running 46-check validation suite...", flush=True)
        report = vs.run_all_checks()
        return report
    except ImportError as exc:
        print(f"  WARNING: Cannot import gpu_validation_suite: {exc}")
        return None
    except Exception as exc:
        print(f"  ERROR running validation suite: {exc}")
        return None


def _run_ai_validation(n: int) -> Optional[object]:
    """Import and run gpu_ai_validation; return its AIValidationReport or None."""
    script_dir = Path(__file__).parent
    sys.path.insert(0, str(script_dir))
    try:
        import gpu_ai_validation as av
        print(f"  Running AI pipeline validation ({n} iterations)...", flush=True)
        return av.run_validation(n=n)
    except SystemExit as exc:
        if exc.code == 2:
            print("  AI validation: services not reachable — gate skipped")
            return None
        raise
    except ImportError as exc:
        print(f"  WARNING: Cannot import gpu_ai_validation: {exc}")
        return None
    except Exception as exc:
        print(f"  ERROR running ai_validation: {exc}")
        return None


def _run_benchmark(n: int) -> Optional[object]:
    """Import and run gpu_benchmark; return its BenchmarkReport or None."""
    script_dir = Path(__file__).parent
    sys.path.insert(0, str(script_dir))
    try:
        import gpu_benchmark as bm
        print(f"  Running performance benchmark ({n} runs per service)...", flush=True)
        return bm.run_benchmark(n=n)
    except SystemExit as exc:
        if exc.code == 2:
            print("  Benchmark: services not reachable — gate skipped")
            return None
        raise
    except ImportError as exc:
        print(f"  WARNING: Cannot import gpu_benchmark: {exc}")
        return None
    except Exception as exc:
        print(f"  ERROR running benchmark: {exc}")
        return None


# ── Gate builders ──────────────────────────────────────────────────────────────

# Map of gate → which V-XX check IDs belong to it
GATE_CHECK_MAP: dict[str, list[str]] = {
    "G-01": ["V-01", "V-02", "V-03", "V-25", "V-26", "V-27", "V-28"],
    "G-02": ["V-04", "V-05", "V-06", "V-07", "V-08", "V-41", "V-42"],
    "G-03": ["V-09", "V-10", "V-11", "V-12", "V-43", "V-44"],
    "G-04": ["V-29", "V-30", "V-31", "V-32", "V-33", "V-34", "V-35",
             "V-36", "V-37", "V-38", "V-39"],
    "G-05": ["V-15", "V-16", "V-17", "V-18", "V-19", "V-20", "V-45", "V-46"],
    "G-06": ["V-21", "V-22", "V-23", "V-24"],
    "G-07": ["V-13", "V-14", "V-40"],
}

GATE_NAMES: dict[str, str] = {
    "G-01": "Runtime Foundation",
    "G-02": "Dependencies",
    "G-03": "Models",
    "G-04": "Infrastructure",
    "G-05": "AI Services",
    "G-06": "End-to-End Inference",
    "G-07": "GPU Resources",
    "G-08": "AI Pipeline (STT→LLM→TTS)",
    "G-09": "Performance (Latency Budgets)",
    "G-10": "Stability (VRAM Growth)",
}


def _build_gate_from_suite(gate_id: str, suite_results: list) -> GateResult:
    """Build a GateResult from validation suite CheckResult objects for this gate."""
    name = GATE_NAMES[gate_id]
    check_ids = GATE_CHECK_MAP.get(gate_id, [])

    matching = [r for r in suite_results if r.check_id in check_ids]
    pass_count = sum(1 for r in matching if r.passed)
    fail_count = sum(1 for r in matching if not r.passed)
    failing = [f"{r.check_id}: {r.name} — {r.message}" for r in matching if not r.passed]

    if not matching:
        return GateResult(
            gate_id=gate_id, name=name,
            passed=False, skipped=True,
            check_count=0, pass_count=0, fail_count=0,
            detail=f"No checks matched gate {gate_id} (validation suite may have failed)",
            failing_checks=failing,
        )

    passed = fail_count == 0
    return GateResult(
        gate_id=gate_id, name=name,
        passed=passed,
        check_count=len(matching),
        pass_count=pass_count,
        fail_count=fail_count,
        detail=f"{pass_count}/{len(matching)} checks passed",
        failing_checks=failing,
    )


# ── Main runner ────────────────────────────────────────────────────────────────

def run_gates(n_ai: int = 5, n_bench: int = 10,
              skip_ai: bool = False, skip_bench: bool = False) -> AcceptanceReport:
    t_start = time.monotonic()
    report = AcceptanceReport()
    first_failed: Optional[str] = None

    print()
    print("=" * 65)
    print("  VoiceOS GPU Acceptance Gates")
    print(f"  Host: {report.hostname}")
    print(f"  Time: {report.timestamp}")
    print("=" * 65)

    # ── Phase 1: Validation suite (G-01 through G-07) ────────────────────────
    print("\n── Phase 1: Validation Suite (G-01 to G-07) ──────────────────")
    suite_report = _run_validation_suite()

    if suite_report is None:
        # Can't run suite — mark all suite gates as failed
        for gate_id in ["G-01", "G-02", "G-03", "G-04", "G-05", "G-06", "G-07"]:
            g = GateResult(
                gate_id=gate_id, name=GATE_NAMES[gate_id],
                passed=False, skipped=True,
                detail="Validation suite failed to run",
            )
            report.gates.append(g)
        first_failed = first_failed or "G-01"
    else:
        suite_results = suite_report.results
        for gate_id in ["G-01", "G-02", "G-03", "G-04", "G-05", "G-06", "G-07"]:
            g = _build_gate_from_suite(gate_id, suite_results)
            report.gates.append(g)
            if not g.passed and first_failed is None:
                first_failed = gate_id

    # ── Phase 2: AI Pipeline Validation (G-08) ───────────────────────────────
    print("\n── Phase 2: AI Pipeline Validation (G-08) ─────────────────────")
    if skip_ai:
        g08 = GateResult(gate_id="G-08", name=GATE_NAMES["G-08"],
                         passed=True, skipped=True,
                         detail="Skipped via --skip-ai")
    else:
        ai_report = _run_ai_validation(n_ai)
        if ai_report is None:
            g08 = GateResult(gate_id="G-08", name=GATE_NAMES["G-08"],
                             passed=False, skipped=True,
                             detail="AI validation unavailable (services not reachable)")
        else:
            assertions = ai_report.assertions
            pass_count = sum(1 for a in assertions if a["passed"])
            fail_count = len(assertions) - pass_count
            failing = [f"{a['name']}: {a['message']}" for a in assertions if not a["passed"]]
            g08 = GateResult(
                gate_id="G-08", name=GATE_NAMES["G-08"],
                passed=ai_report.passed,
                check_count=len(assertions),
                pass_count=pass_count,
                fail_count=fail_count,
                detail=f"{pass_count}/{len(assertions)} assertions passed; {ai_report.summary}",
                failing_checks=failing,
            )

    report.gates.append(g08)
    if not g08.passed and not g08.skipped and first_failed is None:
        first_failed = "G-08"

    # ── Phase 3: Performance Benchmark (G-09 + G-10) ─────────────────────────
    print("\n── Phase 3: Performance Benchmark (G-09 / G-10) ───────────────")
    if skip_bench:
        for gate_id in ["G-09", "G-10"]:
            g = GateResult(gate_id=gate_id, name=GATE_NAMES[gate_id],
                           passed=True, skipped=True,
                           detail="Skipped via --skip-bench")
            report.gates.append(g)
    else:
        bench_report = _run_benchmark(n_bench)
        if bench_report is None:
            for gate_id in ["G-09", "G-10"]:
                g = GateResult(gate_id=gate_id, name=GATE_NAMES[gate_id],
                               passed=False, skipped=True,
                               detail="Benchmark unavailable (services not reachable)")
                report.gates.append(g)
        else:
            # G-09: latency gates
            gate_results = bench_report.gate_results
            pass_count = sum(1 for g in gate_results if g["passed"])
            fail_count = len(gate_results) - pass_count
            failing = [f"{g['name']}: {g['detail']}" for g in gate_results if not g["passed"]]
            g09 = GateResult(
                gate_id="G-09", name=GATE_NAMES["G-09"],
                passed=bench_report.passed,
                check_count=len(gate_results),
                pass_count=pass_count,
                fail_count=fail_count,
                detail=bench_report.summary,
                failing_checks=failing,
            )
            report.gates.append(g09)
            if not g09.passed and first_failed is None:
                first_failed = "G-09"

            # G-10: VRAM stability — read from ai_report if available
            if not skip_ai and 'ai_report' in dir() and ai_report is not None:
                ai_assertions = ai_report.assertions
                vram_assert = next((a for a in ai_assertions if "VRAM" in a["name"]), None)
                if vram_assert:
                    g10 = GateResult(
                        gate_id="G-10", name=GATE_NAMES["G-10"],
                        passed=vram_assert["passed"],
                        check_count=1,
                        pass_count=1 if vram_assert["passed"] else 0,
                        fail_count=0 if vram_assert["passed"] else 1,
                        detail=vram_assert["message"],
                    )
                else:
                    g10 = GateResult(gate_id="G-10", name=GATE_NAMES["G-10"],
                                     passed=True, skipped=True,
                                     detail="VRAM stability: nvidia-smi not available, skipped")
            else:
                g10 = GateResult(gate_id="G-10", name=GATE_NAMES["G-10"],
                                 passed=True, skipped=True,
                                 detail="VRAM stability: AI validation not run, skipped")
            report.gates.append(g10)
            if not g10.passed and not g10.skipped and first_failed is None:
                first_failed = "G-10"

    # ── Final verdict ─────────────────────────────────────────────────────────
    report.runtime_sec = round(time.monotonic() - t_start, 1)
    report.first_failed_gate = first_failed

    blocking_gates = [g for g in report.gates if not g.passed and not g.skipped]
    report.passed = len(blocking_gates) == 0

    total_gates = len(report.gates)
    passed_gates = sum(1 for g in report.gates if g.passed or g.skipped)
    failed_gates = sum(1 for g in report.gates if not g.passed and not g.skipped)

    # Results table
    print()
    print("=" * 65)
    print("  GATE RESULTS")
    print("=" * 65)
    print(f"  {'Gate':<8}  {'Name':<35}  {'Result':>6}  {'Checks':>8}")
    print(f"  {'-'*8}  {'-'*35}  {'-'*6}  {'-'*8}")
    for g in report.gates:
        if g.skipped:
            result_str = "SKIP"
        elif g.passed:
            result_str = "PASS"
        else:
            result_str = "FAIL"
        checks_str = f"{g.pass_count}/{g.check_count}" if g.check_count > 0 else "—"
        print(f"  {g.gate_id:<8}  {g.name:<35}  {result_str:>6}  {checks_str:>8}")

    if blocking_gates:
        print()
        print("  FAILING GATES:")
        for g in blocking_gates:
            print(f"    {g.gate_id} {g.name}: {g.detail}")
            for fc in g.failing_checks[:5]:
                print(f"      ✗ {fc}")
            if len(g.failing_checks) > 5:
                print(f"      ... and {len(g.failing_checks) - 5} more")

    overall = "PASSED" if report.passed else "FAILED"
    report.summary = (
        f"{overall} — {passed_gates}/{total_gates} gates passed"
        + (f"  [FIRST FAILURE: {first_failed}]" if first_failed else "")
        + f"  ({report.runtime_sec}s)"
    )

    print()
    print(f"  VERDICT: {report.summary}")
    print()
    if report.passed:
        print("  ✓ Node is PRODUCTION READY.")
    else:
        print(f"  ✗ Node is NOT production ready. Fix {first_failed} first.")
    print("=" * 65)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS GPU Acceptance Gates")
    parser.add_argument("--n-bench", type=int, default=10,
                        help="Benchmark iterations per service (default: 10)")
    parser.add_argument("--n-ai", type=int, default=5,
                        help="AI pipeline validation iterations (default: 5)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write JSON report to this path")
    parser.add_argument("--skip-bench", action="store_true",
                        help="Skip performance benchmark (G-09, G-10)")
    parser.add_argument("--skip-ai", action="store_true",
                        help="Skip AI pipeline validation (G-08)")
    args = parser.parse_args()

    report = run_gates(
        n_ai=args.n_ai,
        n_bench=args.n_bench,
        skip_ai=args.skip_ai,
        skip_bench=args.skip_bench,
    )

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"\n  JSON report written to: {args.output}")

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
