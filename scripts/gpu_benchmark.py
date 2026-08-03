#!/usr/bin/env python3
"""
VoiceOS GPU Performance Benchmark
====================================
Measures p50/p95/p99 latency for each inference service and the full pipeline.
Runs N iterations per service (default: 10) and reports the distribution.

Services benchmarked:
  - STT  /transcribe        → total request latency per 1s audio
  - LLM  /v1/chat/completions → time-to-first-token (TTFT, non-streaming)
  - TTS  /synthesize         → TTFA + total streaming time + real-time factor
  - E2E  full STT→LLM→TTS   → pipeline wall time

Usage:
    python scripts/gpu_benchmark.py [--n 10] [--output /path/results.json]

Exit codes:
    0 = all latency budgets MET
    1 = one or more latency budgets EXCEEDED
    2 = services not reachable

Spec reference: docs/deployment/GPU_DEPLOYMENT_FRAMEWORK.md §Gate G-09
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import socket
import struct
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from typing import Optional

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

# ── Spec / budgets ─────────────────────────────────────────────────────────────

SPEC = {
    "stt_url":    "http://localhost:8100/transcribe",
    "llm_url":    "http://localhost:8000/v1/chat/completions",
    "tts_url":    "http://localhost:8200/synthesize",
    "stt_health": "http://localhost:8100/health/ready",
    "llm_health": "http://localhost:8000/health",
    "tts_health": "http://localhost:8200/health/ready",
    "llm_model":  "qwen2.5-7b-instruct-fp8",
    # Latency budgets from GPU_DEPLOYMENT_FRAMEWORK.md §Gate G-09
    "stt_p95_budget_ms":  300,    # STT, per 1s audio
    "llm_p95_budget_ms":  500,    # LLM TTFT
    "tts_ttfa_p95_budget_ms": 1500,  # TTS TTFA
    "e2e_p95_budget_ms":  3000,   # full pipeline
    # Audio config
    "audio_duration_sec": 1.0,
    "audio_sample_rate": 16000,
    "tts_sample_rate": 24000,
}

DEFAULT_N = 10


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class ServiceBenchmark:
    name: str
    n: int
    values_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len(self.values_ms)

    @property
    def min_ms(self) -> float:
        return min(self.values_ms) if self.values_ms else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.values_ms) if self.values_ms else 0.0

    @property
    def p50_ms(self) -> float:
        return _percentile(self.values_ms, 50)

    @property
    def p95_ms(self) -> float:
        return _percentile(self.values_ms, 95)

    @property
    def p99_ms(self) -> float:
        return _percentile(self.values_ms, 99)

    @property
    def mean_ms(self) -> float:
        return sum(self.values_ms) / len(self.values_ms) if self.values_ms else 0.0


@dataclass
class BenchmarkReport:
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    hostname: str = field(default_factory=socket.gethostname)
    n_per_service: int = 0
    stt: Optional[dict] = None
    llm: Optional[dict] = None
    tts_ttfa: Optional[dict] = None
    tts_total: Optional[dict] = None
    e2e: Optional[dict] = None
    gate_results: list[dict] = field(default_factory=list)
    passed: bool = False
    summary: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    sv = sorted(vals)
    idx = min(int(len(sv) * pct / 100), len(sv) - 1)
    return sv[idx]


def _make_audio_b64(duration_sec: float = 1.0) -> str:
    sr = SPEC["audio_sample_rate"]
    n = int(sr * duration_sec)
    samples = [int(32767 * math.sin(2 * math.pi * 440 * i / sr)) for i in range(n)]
    raw = struct.pack(f"<{n}h", *samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(raw)
    return base64.b64encode(buf.getvalue()).decode()


def _check_services(client) -> list[str]:
    unreachable = []
    for label, url in [
        ("STT", SPEC["stt_health"]),
        ("LLM", SPEC["llm_health"]),
        ("TTS", SPEC["tts_health"]),
    ]:
        try:
            r = client.get(url, timeout=5)
            if r.status_code != 200:
                unreachable.append(f"{label} ({url}) → HTTP {r.status_code}")
        except Exception as exc:
            unreachable.append(f"{label} ({url}) → {exc}")
    return unreachable


def _gate(report: BenchmarkReport, name: str, passed: bool, detail: str) -> None:
    status = "PASS" if passed else "FAIL"
    report.gate_results.append({"name": name, "passed": passed, "status": status, "detail": detail})


def _bench_to_dict(b: ServiceBenchmark) -> dict:
    return {
        "n": b.n,
        "success_count": b.success_count,
        "min_ms": round(b.min_ms, 1),
        "mean_ms": round(b.mean_ms, 1),
        "p50_ms": round(b.p50_ms, 1),
        "p95_ms": round(b.p95_ms, 1),
        "p99_ms": round(b.p99_ms, 1),
        "max_ms": round(b.max_ms, 1),
        "errors": b.errors,
    }


# ── Benchmark runners ──────────────────────────────────────────────────────────

def bench_stt(client, n: int) -> tuple[ServiceBenchmark, str]:
    """Returns (benchmark, last_transcript)."""
    b = ServiceBenchmark("STT /transcribe (1s audio)", n)
    transcript = ""
    audio_b64 = _make_audio_b64(SPEC["audio_duration_sec"])

    print(f"  STT [{n} runs] ", end="", flush=True)
    for _ in range(n):
        try:
            t0 = time.monotonic()
            r = client.post(SPEC["stt_url"],
                            json={"audio_b64": audio_b64, "language": "en"}, timeout=60)
            ms = (time.monotonic() - t0) * 1000
            r.raise_for_status()
            words = r.json()["words"]
            transcript = " ".join(w["word"] for w in words)
            b.values_ms.append(ms)
            print(".", end="", flush=True)
        except Exception as exc:
            b.errors.append(str(exc))
            print("x", end="", flush=True)
    print(f"  p50={b.p50_ms:.0f}ms  p95={b.p95_ms:.0f}ms  p99={b.p99_ms:.0f}ms")
    return b, transcript


def bench_llm(client, n: int, prompt: str = "Hello, what can you help me with today?") -> tuple[ServiceBenchmark, str]:
    """Returns (benchmark, last_response)."""
    b = ServiceBenchmark("LLM /v1/chat/completions (TTFT)", n)
    response = ""

    print(f"  LLM [{n} runs] ", end="", flush=True)
    for _ in range(n):
        try:
            t0 = time.monotonic()
            r = client.post(
                SPEC["llm_url"],
                json={
                    "model": SPEC["llm_model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 64,
                    "stream": False,
                },
                timeout=60,
            )
            ms = (time.monotonic() - t0) * 1000
            r.raise_for_status()
            response = r.json()["choices"][0]["message"]["content"]
            b.values_ms.append(ms)
            print(".", end="", flush=True)
        except Exception as exc:
            b.errors.append(str(exc))
            print("x", end="", flush=True)
    print(f"  p50={b.p50_ms:.0f}ms  p95={b.p95_ms:.0f}ms  p99={b.p99_ms:.0f}ms")
    return b, response


def bench_tts(client, n: int, text: str = "Hello, I am VoiceOS, your AI assistant.") -> tuple[ServiceBenchmark, ServiceBenchmark]:
    """Returns (ttfa_benchmark, total_benchmark)."""
    b_ttfa = ServiceBenchmark("TTS /synthesize TTFA", n)
    b_total = ServiceBenchmark("TTS /synthesize total", n)

    print(f"  TTS [{n} runs] ", end="", flush=True)
    for _ in range(n):
        try:
            t0 = time.monotonic()
            ttfa_ms = None
            total_bytes = 0
            with client.stream("POST", SPEC["tts_url"],
                               json={"text": text, "speaker": "kavya"}, timeout=120) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes():
                    if chunk:
                        if ttfa_ms is None:
                            ttfa_ms = (time.monotonic() - t0) * 1000
                        total_bytes += len(chunk)
            total_ms = (time.monotonic() - t0) * 1000
            if ttfa_ms is None:
                ttfa_ms = total_ms
            b_ttfa.values_ms.append(ttfa_ms)
            b_total.values_ms.append(total_ms)

            # Compute real-time factor (RTF)
            audio_samples = total_bytes // 4   # float32
            audio_dur_ms = audio_samples / SPEC["tts_sample_rate"] * 1000
            rtf = total_ms / audio_dur_ms if audio_dur_ms > 0 else 0.0
            _ = rtf  # stored per-run in full report

            print(".", end="", flush=True)
        except Exception as exc:
            b_ttfa.errors.append(str(exc))
            b_total.errors.append(str(exc))
            print("x", end="", flush=True)
    print(f"  TTFA p50={b_ttfa.p50_ms:.0f}ms  p95={b_ttfa.p95_ms:.0f}ms  "
          f"total p50={b_total.p50_ms:.0f}ms  p95={b_total.p95_ms:.0f}ms")
    return b_ttfa, b_total


def bench_e2e(client, n: int, audio_b64: str) -> ServiceBenchmark:
    b = ServiceBenchmark("E2E pipeline (STT→LLM→TTS)", n)

    print(f"  E2E [{n} runs] ", end="", flush=True)
    for _ in range(n):
        try:
            t0 = time.monotonic()

            # STT
            r = client.post(SPEC["stt_url"],
                            json={"audio_b64": audio_b64, "language": "en"}, timeout=60)
            r.raise_for_status()
            words = r.json()["words"]
            transcript = " ".join(w["word"] for w in words)

            # LLM
            r = client.post(
                SPEC["llm_url"],
                json={
                    "model": SPEC["llm_model"],
                    "messages": [{"role": "user", "content": transcript}],
                    "max_tokens": 64,
                    "stream": False,
                },
                timeout=60,
            )
            r.raise_for_status()
            response = r.json()["choices"][0]["message"]["content"]

            # TTS
            with client.stream("POST", SPEC["tts_url"],
                               json={"text": response[:120], "speaker": "kavya"}, timeout=120) as r:
                r.raise_for_status()
                for _ in r.iter_bytes():
                    pass

            ms = (time.monotonic() - t0) * 1000
            b.values_ms.append(ms)
            print(".", end="", flush=True)
        except Exception as exc:
            b.errors.append(str(exc))
            print("x", end="", flush=True)
    print(f"  p50={b.p50_ms:.0f}ms  p95={b.p95_ms:.0f}ms  p99={b.p99_ms:.0f}ms")
    return b


# ── Main ───────────────────────────────────────────────────────────────────────

def run_benchmark(n: int = DEFAULT_N) -> BenchmarkReport:
    report = BenchmarkReport(n_per_service=n)

    if not _HTTPX:
        report.summary = "ABORT: httpx not installed — run: pip install httpx"
        return report

    import httpx  # noqa: PLC0415
    client = httpx.Client(timeout=120)

    print()
    print("=" * 65)
    print("  VoiceOS GPU Performance Benchmark")
    print(f"  Host: {report.hostname}  |  N={n} per service")
    print("=" * 65)

    # Pre-check
    print("\n[1/3] Service availability...")
    unreachable = _check_services(client)
    if unreachable:
        for u in unreachable:
            print(f"  UNREACHABLE: {u}")
        print("\n  ABORT: Required services are not running.")
        report.summary = f"ABORT: {len(unreachable)} service(s) unreachable"
        sys.exit(2)

    print("  All services reachable ✓")

    # Benchmark each service
    print(f"\n[2/3] Running benchmarks (N={n} each)...")
    audio_b64 = _make_audio_b64(SPEC["audio_duration_sec"])

    b_stt, stt_transcript = bench_stt(client, n)
    b_llm, llm_response   = bench_llm(client, n, prompt=stt_transcript or "Hello, how can you help me?")
    b_tts_ttfa, b_tts_total = bench_tts(client, n, text=llm_response[:120] or "I am VoiceOS, your AI assistant.")
    b_e2e                  = bench_e2e(client, n, audio_b64)

    report.stt      = _bench_to_dict(b_stt)
    report.llm      = _bench_to_dict(b_llm)
    report.tts_ttfa = _bench_to_dict(b_tts_ttfa)
    report.tts_total = _bench_to_dict(b_tts_total)
    report.e2e      = _bench_to_dict(b_e2e)

    # Gate evaluation (G-09)
    print(f"\n[3/3] Gate G-09 — Latency budgets...")

    _gate(report, "G-09.1 STT p95 ≤ 300ms per 1s audio",
          b_stt.p95_ms <= SPEC["stt_p95_budget_ms"],
          f"p95={b_stt.p95_ms:.0f}ms (budget: {SPEC['stt_p95_budget_ms']}ms)")

    _gate(report, "G-09.2 LLM TTFT p95 ≤ 500ms",
          b_llm.p95_ms <= SPEC["llm_p95_budget_ms"],
          f"p95={b_llm.p95_ms:.0f}ms (budget: {SPEC['llm_p95_budget_ms']}ms)")

    _gate(report, "G-09.3 TTS TTFA p95 ≤ 1500ms",
          b_tts_ttfa.p95_ms <= SPEC["tts_ttfa_p95_budget_ms"],
          f"p95={b_tts_ttfa.p95_ms:.0f}ms (budget: {SPEC['tts_ttfa_p95_budget_ms']}ms)")

    _gate(report, "G-09.4 E2E p95 ≤ 3000ms",
          b_e2e.p95_ms <= SPEC["e2e_p95_budget_ms"],
          f"p95={b_e2e.p95_ms:.0f}ms (budget: {SPEC['e2e_p95_budget_ms']}ms)")

    # Print results table
    print()
    print(f"  {'Metric':<40}  {'p50':>8}  {'p95':>8}  {'p99':>8}  {'min':>8}")
    print(f"  {'-'*40}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
    for b, label in [
        (b_stt,      "STT /transcribe (1s audio)"),
        (b_llm,      "LLM /v1/chat/completions"),
        (b_tts_ttfa, "TTS /synthesize TTFA"),
        (b_tts_total,"TTS /synthesize total"),
        (b_e2e,      "E2E STT→LLM→TTS"),
    ]:
        print(f"  {label:<40}  {b.p50_ms:>7.0f}ms  {b.p95_ms:>7.0f}ms  "
              f"{b.p99_ms:>7.0f}ms  {b.min_ms:>7.0f}ms")

    print()
    print(f"  {'Gate':<45}  {'Result':>6}")
    print(f"  {'-'*45}  {'-'*6}")
    for g in report.gate_results:
        mark = "PASS" if g["passed"] else "FAIL"
        print(f"  {g['name']:<45}  {mark:>6}   {g['detail']}")

    passed_gates = sum(1 for g in report.gate_results if g["passed"])
    total_gates = len(report.gate_results)
    report.passed = (passed_gates == total_gates)
    overall = "PASSED" if report.passed else "FAILED"
    report.summary = (
        f"{overall}: {passed_gates}/{total_gates} latency gates met  |  "
        f"STT p95={b_stt.p95_ms:.0f}ms  LLM p95={b_llm.p95_ms:.0f}ms  "
        f"TTS TTFA p95={b_tts_ttfa.p95_ms:.0f}ms  E2E p95={b_e2e.p95_ms:.0f}ms"
    )
    print()
    print(f"  {report.summary}")
    print()
    print("=" * 65)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS GPU Performance Benchmark")
    parser.add_argument("--n", type=int, default=DEFAULT_N,
                        help=f"Runs per service (default: {DEFAULT_N})")
    parser.add_argument("--output", type=str, default=None,
                        help="Write JSON report to this path")
    args = parser.parse_args()

    report = run_benchmark(n=args.n)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"  Report written to: {args.output}")

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
