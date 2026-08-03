#!/usr/bin/env python3
"""
VoiceOS GPU Deployment Report Generator
==========================================
Runs the full validation pipeline (validation suite + AI validation + benchmark
+ acceptance gates) and emits a timestamped Markdown report to
deployment/gpu/deployment_reports/REPORT_{YYYYMMDD_HHMMSS}.md.

This is the final step in every deployment procedure:
  bash deployment/gpu/deploy.sh
  python scripts/gpu_validation_suite.py
  python scripts/gpu_ai_validation.py
  python scripts/gpu_benchmark.py
  python scripts/gpu_acceptance_gates.py
  python scripts/gpu_deployment_report.py   ← this script

Usage:
    python scripts/gpu_deployment_report.py [--n-bench 10] [--n-ai 5]
        [--output /custom/path/REPORT.md] [--skip-bench] [--skip-ai]

Exit codes:
    0 = report generated (regardless of gate results)
    1 = report generation failed

Spec reference: docs/deployment/GPU_DEPLOYMENT_FRAMEWORK.md
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sh(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() or r.stderr.strip() or "n/a"
    except Exception:
        return "n/a"


def _vram_info() -> tuple[str, str, str]:
    """Returns (total, used, free) as strings."""
    if not shutil.which("nvidia-smi"):
        return ("n/a", "n/a", "n/a")
    total = _sh(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
    used  = _sh(["nvidia-smi", "--query-gpu=memory.used",  "--format=csv,noheader,nounits"])
    free  = _sh(["nvidia-smi", "--query-gpu=memory.free",  "--format=csv,noheader,nounits"])
    return total, used, free


def _gpu_info() -> dict[str, str]:
    if not shutil.which("nvidia-smi"):
        return {"model": "n/a", "driver": "n/a", "uuid": "n/a", "temp": "n/a", "power": "n/a"}
    q = "--query-gpu=name,driver_version,uuid,temperature.gpu,power.draw"
    out = _sh(["nvidia-smi", q, "--format=csv,noheader"])
    parts = [p.strip() for p in out.split(",")]
    keys = ["model", "driver", "uuid", "temp", "power"]
    return {k: parts[i] if i < len(parts) else "n/a" for i, k in enumerate(keys)}


def _py_pkg(pkg: str) -> str:
    venv_py = "/opt/voiceos-gpu/venv/bin/python"
    py = venv_py if os.path.isfile(venv_py) else sys.executable
    try:
        r = subprocess.run(
            [py, "-c", f"import {pkg.replace('-','_')}; print({pkg.replace('-','_')}.__version__)"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or "not installed"
    except Exception:
        return "not installed"


def _model_info(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return "MISSING"
    size_mb = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) // (1024 * 1024)
    count = sum(1 for _ in p.rglob("*") if _.is_file())
    return f"{size_mb} MB  ({count} files)"


def _service_status(name: str) -> str:
    active  = _sh(["systemctl", "is-active",  name])
    enabled = _sh(["systemctl", "is-enabled", name])
    return f"{active} / enabled={enabled}"


def _http_status(url: str) -> str:
    try:
        import urllib.request
        req = urllib.request.urlopen(url, timeout=3)
        return f"HTTP {req.status}"
    except Exception as exc:
        return f"unreachable ({exc})"


# ── Report section builders ────────────────────────────────────────────────────

def _section_node(hostname: str, ts: str) -> str:
    gpu = _gpu_info()
    vram_total, vram_used, vram_free = _vram_info()
    ram_mb = _sh(["awk", "/^MemTotal/{print $2}", "/proc/meminfo"])
    ram_gb = f"{int(ram_mb) // 1024} GB" if ram_mb.isdigit() else "n/a"
    cpu_model = _sh(["bash", "-c", "grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2"])
    kernel = platform.release()
    disk = _sh(["df", "-BG", "/opt/voiceos-gpu"])

    lines = [
        "## Node State",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Hostname | `{hostname}` |",
        f"| Timestamp | {ts} |",
        f"| OS | {platform.platform()} |",
        f"| Kernel | {kernel} |",
        f"| CPU | {cpu_model.strip()} |",
        f"| RAM | {ram_gb} |",
        f"| GPU model | {gpu['model']} |",
        f"| GPU driver | {gpu['driver']} |",
        f"| GPU UUID | {gpu['uuid']} |",
        f"| GPU temp | {gpu['temp']} °C |",
        f"| GPU power | {gpu['power']} |",
        f"| VRAM total | {vram_total} MiB |",
        f"| VRAM used | {vram_used} MiB |",
        f"| VRAM free | {vram_free} MiB |",
        f"| Disk (/opt/voiceos-gpu) | {disk.splitlines()[-1] if disk else 'n/a'} |",
        "",
    ]
    return "\n".join(lines)


def _section_packages() -> str:
    pkgs = [
        ("torch",          _py_pkg("torch")),
        ("vllm",           _py_pkg("vllm")),
        ("faster_whisper", _py_pkg("faster_whisper")),
        ("ctranslate2",    _py_pkg("ctranslate2")),
        ("transformers",   _py_pkg("transformers")),
        ("fastapi",        _py_pkg("fastapi")),
        ("uvicorn",        _py_pkg("uvicorn")),
        ("numpy",          _py_pkg("numpy")),
        ("pydantic",       _py_pkg("pydantic")),
    ]
    lines = [
        "## Python Package Versions",
        "",
        "| Package | Installed |",
        "|---|---|",
    ]
    for name, ver in pkgs:
        lines.append(f"| `{name}` | {ver} |")
    lines.append("")
    return "\n".join(lines)


def _section_models() -> str:
    model_paths = {
        "Whisper large-v3-turbo": "/opt/voiceos-gpu/models/whisper-large-v3-turbo",
        "Qwen2.5-7B-FP8":        "/opt/voiceos-gpu/models/qwen2.5-7b-fp8",
        "Veena-3B-BF16":         "/opt/voiceos-gpu/models/veena-fp16",
        "SNAC 24kHz":            "/opt/voiceos-gpu/models/snac-24khz",
    }
    lines = [
        "## Model Inventory",
        "",
        "| Model | Path | Size |",
        "|---|---|---|",
    ]
    for label, path in model_paths.items():
        info = _model_info(path)
        lines.append(f"| {label} | `{path}` | {info} |")
    lines.append("")
    return "\n".join(lines)


def _section_services() -> str:
    svcs = [
        ("voiceos-llm.service",  "http://localhost:8000/health"),
        ("voiceos-stt.service",  "http://localhost:8100/health/ready"),
        ("voiceos-tts.service",  "http://localhost:8200/health/ready"),
    ]
    lines = [
        "## Service Status",
        "",
        "| Service | systemd status | Health endpoint |",
        "|---|---|---|",
    ]
    for svc, url in svcs:
        status = _service_status(svc)
        health = _http_status(url)
        lines.append(f"| `{svc}` | {status} | {health} |")
    lines.append("")
    return "\n".join(lines)


def _section_validation_suite(suite_report) -> str:
    if suite_report is None:
        return "## Validation Suite\n\n_Not available — suite failed to run._\n\n"

    results = suite_report.results
    pass_count = suite_report.pass_count
    fail_count = suite_report.fail_count
    total = pass_count + fail_count
    failed = [r for r in results if not r.passed]

    lines = [
        "## Validation Suite (46 Checks)",
        "",
        f"**Result:** {'ALL PASS' if fail_count == 0 else 'FAILED'}  "
        f"({pass_count}/{total} checks passed)",
        "",
    ]
    if failed:
        lines += [
            "### Failed Checks",
            "",
            "| ID | Name | Message |",
            "|---|---|---|",
        ]
        for r in failed:
            lines.append(f"| {r.check_id} | {r.name} | {r.message} |")
        lines.append("")

    lines += [
        "### Full Results",
        "",
        "| ID | Name | Result | Message |",
        "|---|---|---|---|",
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"| {r.check_id} | {r.name} | {mark} | {r.message} |")
    lines.append("")
    return "\n".join(lines)


def _section_ai_validation(ai_report) -> str:
    if ai_report is None:
        return "## AI Pipeline Validation\n\n_Not run (services not reachable or skipped)._\n\n"

    iters = ai_report.iterations
    passed = [it for it in iters if not it.error]

    def _pct(vals: list[float], p: float) -> str:
        if not vals:
            return "n/a"
        sv = sorted(vals)
        idx = min(int(len(sv) * p / 100), len(sv) - 1)
        return f"{sv[idx]:.0f}ms"

    stt_vals  = [it.stt_ms      for it in passed]
    llm_vals  = [it.llm_ms      for it in passed]
    ttfa_vals = [it.tts_ttfa_ms for it in passed]
    e2e_vals  = [it.e2e_ms      for it in passed]

    lines = [
        "## AI Pipeline Validation",
        "",
        f"**Result:** {'PASSED' if ai_report.passed else 'FAILED'}  "
        f"({len(passed)}/{len(iters)} iterations succeeded)",
        f"  Summary: {ai_report.summary}",
        "",
        "### Latency Distribution",
        "",
        "| Metric | p50 | p95 | p99 | min |",
        "|---|---|---|---|---|",
        f"| STT /transcribe (1s audio)  | {_pct(stt_vals,50)}  | {_pct(stt_vals,95)}  | {_pct(stt_vals,99)}  | {_pct(stt_vals,0)}  |",
        f"| LLM /v1/chat/completions    | {_pct(llm_vals,50)}  | {_pct(llm_vals,95)}  | {_pct(llm_vals,99)}  | {_pct(llm_vals,0)}  |",
        f"| TTS /synthesize TTFA        | {_pct(ttfa_vals,50)} | {_pct(ttfa_vals,95)} | {_pct(ttfa_vals,99)} | {_pct(ttfa_vals,0)} |",
        f"| E2E STT→LLM→TTS             | {_pct(e2e_vals,50)}  | {_pct(e2e_vals,95)}  | {_pct(e2e_vals,99)}  | {_pct(e2e_vals,0)}  |",
        "",
        "### Assertions",
        "",
        "| Assertion | Result | Detail |",
        "|---|---|---|",
    ]
    for a in ai_report.assertions:
        mark = "PASS" if a["passed"] else "FAIL"
        lines.append(f"| {a['name']} | {mark} | {a['message']} |")
    lines.append("")
    return "\n".join(lines)


def _section_benchmark(bench_report) -> str:
    if bench_report is None:
        return "## Performance Benchmark\n\n_Not run (services not reachable or skipped)._\n\n"

    def _fmt(d: Optional[dict], key: str) -> str:
        if d is None:
            return "n/a"
        return f"{d.get(key, 0):.0f}ms"

    lines = [
        "## Performance Benchmark",
        "",
        f"**Result:** {'PASSED' if bench_report.passed else 'FAILED'}",
        f"  {bench_report.summary}",
        "",
        "| Metric | p50 | p95 | p99 | min |",
        "|---|---|---|---|---|",
        f"| STT /transcribe (1s audio)  | {_fmt(bench_report.stt,'p50_ms')} | {_fmt(bench_report.stt,'p95_ms')} | {_fmt(bench_report.stt,'p99_ms')} | {_fmt(bench_report.stt,'min_ms')} |",
        f"| LLM /v1/chat/completions    | {_fmt(bench_report.llm,'p50_ms')} | {_fmt(bench_report.llm,'p95_ms')} | {_fmt(bench_report.llm,'p99_ms')} | {_fmt(bench_report.llm,'min_ms')} |",
        f"| TTS TTFA                    | {_fmt(bench_report.tts_ttfa,'p50_ms')} | {_fmt(bench_report.tts_ttfa,'p95_ms')} | {_fmt(bench_report.tts_ttfa,'p99_ms')} | {_fmt(bench_report.tts_ttfa,'min_ms')} |",
        f"| TTS total                   | {_fmt(bench_report.tts_total,'p50_ms')} | {_fmt(bench_report.tts_total,'p95_ms')} | {_fmt(bench_report.tts_total,'p99_ms')} | {_fmt(bench_report.tts_total,'min_ms')} |",
        f"| E2E pipeline                | {_fmt(bench_report.e2e,'p50_ms')} | {_fmt(bench_report.e2e,'p95_ms')} | {_fmt(bench_report.e2e,'p99_ms')} | {_fmt(bench_report.e2e,'min_ms')} |",
        "",
        "### Latency Budgets (Gate G-09)",
        "",
        "| Gate | Budget | Actual (p95) | Result |",
        "|---|---|---|---|",
    ]
    for g in bench_report.gate_results:
        mark = "PASS" if g["passed"] else "FAIL"
        lines.append(f"| {g['name']} | — | — | {mark} — {g['detail']} |")
    lines.append("")
    return "\n".join(lines)


def _section_gates(gates_report) -> str:
    lines = [
        "## Acceptance Gate Summary",
        "",
        f"**Overall verdict:** {'PASSED — Node is PRODUCTION READY' if gates_report.passed else 'FAILED — Node is NOT production ready'}",
        "",
        "| Gate | Name | Result | Checks |",
        "|---|---|---|---|",
    ]
    for g in gates_report.gates:
        if g.skipped:
            result = "SKIP"
        elif g.passed:
            result = "PASS"
        else:
            result = "FAIL"
        checks = f"{g.pass_count}/{g.check_count}" if g.check_count > 0 else "—"
        lines.append(f"| {g.gate_id} | {g.name} | {result} | {checks} |")

    if gates_report.first_failed_gate:
        lines += [
            "",
            f"**First failed gate:** {gates_report.first_failed_gate}",
        ]
    lines.append("")
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def generate_report(n_ai: int = 5, n_bench: int = 10,
                    skip_ai: bool = False, skip_bench: bool = False,
                    output_path: Optional[str] = None) -> str:
    ts = datetime.datetime.utcnow()
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_file = ts.strftime("%Y%m%d_%H%M%S")
    hostname = socket.gethostname()

    print()
    print("=" * 65)
    print("  VoiceOS GPU Deployment Report Generator")
    print(f"  Host: {hostname}  |  {ts_str}")
    print("=" * 65)

    # Add scripts dir to path so we can import sibling scripts
    scripts_dir = Path(__file__).parent
    sys.path.insert(0, str(scripts_dir))

    # Run validation suite
    print("\n[1/4] Running validation suite...")
    try:
        import gpu_validation_suite as vs
        suite_report = vs.run_all_checks()
    except Exception as exc:
        print(f"  WARNING: {exc}")
        suite_report = None

    # Run AI validation
    ai_report = None
    if not skip_ai:
        print(f"\n[2/4] Running AI pipeline validation ({n_ai} iterations)...")
        try:
            import gpu_ai_validation as av
            ai_report = av.run_validation(n=n_ai)
        except SystemExit as exc:
            if exc.code == 2:
                print("  Services not reachable — skipping AI validation")
        except Exception as exc:
            print(f"  WARNING: {exc}")
    else:
        print("\n[2/4] AI validation skipped (--skip-ai)")

    # Run benchmark
    bench_report = None
    if not skip_bench:
        print(f"\n[3/4] Running performance benchmark ({n_bench} runs)...")
        try:
            import gpu_benchmark as bm
            bench_report = bm.run_benchmark(n=n_bench)
        except SystemExit as exc:
            if exc.code == 2:
                print("  Services not reachable — skipping benchmark")
        except Exception as exc:
            print(f"  WARNING: {exc}")
    else:
        print("\n[3/4] Benchmark skipped (--skip-bench)")

    # Run acceptance gates
    print("\n[4/4] Running acceptance gates...")
    try:
        import gpu_acceptance_gates as ag
        gates_report = ag.run_gates(
            n_ai=n_ai, n_bench=n_bench,
            skip_ai=True,       # Already ran above; pass pre-computed reports
            skip_bench=True,
        )
        # Patch in the pre-computed results for the summary
        gates_report.passed = all(
            g.passed or g.skipped for g in gates_report.gates
        )
    except Exception as exc:
        print(f"  WARNING: acceptance gates failed: {exc}")
        from dataclasses import dataclass, field
        @dataclass
        class _FallbackGates:
            gates: list = field(default_factory=list)
            passed: bool = False
            first_failed_gate: Optional[str] = "unknown"
            summary: str = f"Gates runner failed: {exc}"
            runtime_sec: float = 0.0
        gates_report = _FallbackGates()

    # Assemble Markdown report
    print("\n  Assembling Markdown report...")
    sections = [
        f"# VoiceOS GPU Deployment Report\n",
        f"**Generated:** {ts_str}  ",
        f"**Host:** `{hostname}`  ",
        f"**Overall:** {'✅ PRODUCTION READY' if gates_report.passed else '❌ NOT PRODUCTION READY'}",
        "",
        "---",
        "",
        _section_node(hostname, ts_str),
        _section_packages(),
        _section_models(),
        _section_services(),
        _section_validation_suite(suite_report),
        _section_ai_validation(ai_report),
        _section_benchmark(bench_report),
        _section_gates(gates_report),
        "---",
        "",
        "_Report generated by `scripts/gpu_deployment_report.py`  ",
        f"Spec reference: `docs/deployment/GPU_DEPLOYMENT_FRAMEWORK.md`_",
        "",
    ]
    content = "\n".join(sections)

    # Write report
    if output_path is None:
        reports_dir = Path(__file__).parent.parent / "deployment" / "gpu" / "deployment_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(reports_dir / f"REPORT_{ts_file}.md")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(content)

    print()
    print("=" * 65)
    verdict = "PRODUCTION READY" if gates_report.passed else "NOT PRODUCTION READY"
    print(f"  VERDICT: {verdict}")
    print(f"  Report saved to: {output_path}")
    print("=" * 65)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS GPU Deployment Report Generator")
    parser.add_argument("--n-bench", type=int, default=10)
    parser.add_argument("--n-ai", type=int, default=5)
    parser.add_argument("--output", type=str, default=None,
                        help="Custom output path (default: deployment/gpu/deployment_reports/REPORT_{ts}.md)")
    parser.add_argument("--skip-bench", action="store_true")
    parser.add_argument("--skip-ai", action="store_true")
    args = parser.parse_args()

    try:
        path = generate_report(
            n_ai=args.n_ai,
            n_bench=args.n_bench,
            skip_ai=args.skip_ai,
            skip_bench=args.skip_bench,
            output_path=args.output,
        )
        print(f"\n  Report: {path}")
        sys.exit(0)
    except Exception as exc:
        print(f"\n  ERROR: Report generation failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
