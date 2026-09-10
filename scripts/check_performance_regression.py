#!/usr/bin/env python3
"""check_performance_regression.py -- CI-blocking RegressionDetector gate (Sprint-028, V3 Ch19).

Wired into ``.github/workflows/ci.yml`` as a blocking stage (same
"structural validator substitutes for a real infra check" precedent as
``check_pii_logs.py``/``check_secrets.py``/``validate_observability_configs.py``):
compares a "current" per-stage p95 measurement against the registered
:data:`~src.libs.performance_engineering.benchmarks.STAGE_BUDGETS_MS`
baseline and fails (exit 1) if any stage regressed by more than
:data:`~src.libs.performance_engineering.regression_gate.REGRESSION_THRESHOLD`
(10%).

CI has no live staging pipeline to measure real per-stage latency from, so
this gate's default "current" measurement is a deterministic fixture at
95% of budget (Sprint-028.md's own Phase 1 "Stage timing: Fixed-duration
fixtures" mock backend) -- comfortably passing, proving the gate is wired
and green. ``--current`` lets a real deployment (Phase 2 -- CPU node CI
against ContinuousProfiler-collected staging samples) pipe in real
measurements instead. ``--simulate-regression`` inflates one stage 15%
over its own baseline, for a controlled demonstration that the gate does
in fact fail correctly (Sprint-028.md AC: "RegressionDetector CI gate ...
fails correctly on simulated regression") -- exercised by
``tests/unit/libs/test_performance_engineering.py``, not as a
permanently-red CI job.

Usage:
    python scripts/check_performance_regression.py
    python scripts/check_performance_regression.py --current path/to/measured.json
    python scripts/check_performance_regression.py --simulate-regression

Exit codes:
    0 -- no stage regressed by more than the threshold
    1 -- one or more stages regressed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.libs.performance_engineering.benchmarks import STAGE_BUDGETS_MS, BenchmarkReport, StageBenchmarkResult
from src.libs.performance_engineering.regression_gate import RegressionDetector, RegressionReport

DEFAULT_CURRENT_FRACTION_OF_BUDGET = 0.95
"""Default synthetic 'current' p95 for each stage, as a fraction of its budget -- comfortably passing."""

SIMULATED_REGRESSION_FRACTION_OVER_BUDGET = 1.15
"""--simulate-regression sets the first stage's 'current' p95 to this fraction of its own budget (a 15% overshoot)."""


def default_current_fixture() -> dict[str, float]:
    """Deterministic 'current' measurement fixture: 95% of each stage's registered budget."""
    return {stage: budget * DEFAULT_CURRENT_FRACTION_OF_BUDGET for stage, budget in STAGE_BUDGETS_MS.items()}


def evaluate(current: dict[str, float], *, baseline: dict[str, float] | None = None) -> RegressionReport:
    """Run RegressionDetector against ``current`` measurements, baselined at ``baseline`` (default: STAGE_BUDGETS_MS)."""
    baselines = baseline if baseline is not None else dict(STAGE_BUDGETS_MS)
    detector = RegressionDetector(baselines)
    report = BenchmarkReport(
        environment="ci",
        results=tuple(
            StageBenchmarkResult(
                stage_name=stage,
                p95_ms=p95_ms,
                budget_ms=STAGE_BUDGETS_MS.get(stage, p95_ms),
                sample_count=1,
            )
            for stage, p95_ms in current.items()
        ),
    )
    return detector.check(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--current",
        type=Path,
        default=None,
        help="Path to a JSON object of {stage_name: measured_p95_ms}. Defaults to a deterministic fixture.",
    )
    parser.add_argument(
        "--simulate-regression",
        action="store_true",
        help="Inflate one stage's current p95 to 115%% of its budget, to prove the gate fails correctly.",
    )
    args = parser.parse_args(argv)

    current = json.loads(args.current.read_text(encoding="utf-8")) if args.current else default_current_fixture()

    if args.simulate_regression:
        stage = next(iter(STAGE_BUDGETS_MS))
        current[stage] = STAGE_BUDGETS_MS[stage] * SIMULATED_REGRESSION_FRACTION_OVER_BUDGET

    report = evaluate(current)

    if not report.passed:
        print("[check_performance_regression] FAIL -- one or more stages regressed by more than 10%:")
        for regression in report.regressions():
            print(
                f"  {regression.stage_name}: baseline={regression.baseline_p95_ms:.1f}ms "
                f"measured={regression.measured_p95_ms:.1f}ms ({regression.regression_pct:+.1%})"
            )
        return 1

    print(f"[check_performance_regression] PASS -- {len(report.results)} stage(s) checked, all within budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
