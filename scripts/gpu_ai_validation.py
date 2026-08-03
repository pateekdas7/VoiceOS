#!/usr/bin/env python3
"""
VoiceOS GPU AI Pipeline Validation
=====================================
Exercises the full STT→LLM→TTS pipeline against the live GPU inference
services. Measures per-stage latency and VRAM consumption over N iterations.

What this proves:
  - Real Whisper/Qwen/Veena inference is working end-to-end
  - API contracts are correct (TranscribeRequest, chat/completions, /synthesize)
  - VRAM does not grow unboundedly across iterations (leak detection)
  - Latency numbers match the budgets defined in GPU_DEPLOYMENT_FRAMEWORK.md

Usage:
    python scripts/gpu_ai_validation.py [--n 5] [--output /path/report.json]

Exit codes:
    0 = all assertions PASSED
    1 = one or more assertions FAILED
    2 = services not reachable (pre-condition failed)

Spec reference: docs/deployment/GPU_DEPLOYMENT_FRAMEWORK.md §Acceptance Gate G-08
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


# ── Optional imports (gracefully absent on non-GPU hosts) ─────────────────────

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

try:
    import subprocess
    import shutil
    _SUBPROCESS = True
except ImportError:
    _SUBPROCESS = False

import subprocess
import shutil

# ── Spec constants ─────────────────────────────────────────────────────────────

SPEC = {
    "stt_url":    "http://localhost:8100/transcribe",
    "llm_url":    "http://localhost:8000/v1/chat/completions",
    "tts_url":    "http://localhost:8200/synthesize",
    "stt_health": "http://localhost:8100/health/ready",
    "llm_health": "http://localhost:8000/health",
    "tts_health": "http://localhost:8200/health/ready",
    "llm_model":  "qwen2.5-7b-instruct-fp8",
    # Latency budgets (ms) from GPU_DEPLOYMENT_FRAMEWORK.md §Latency Budgets
    "stt_latency_budget_ms": 500,    # per-request, 1s audio input
    "llm_ttft_budget_ms":    500,    # time-to-first-token
    "tts_ttfa_budget_ms":   1500,    # time-to-first-audio
    "e2e_budget_ms":        3000,    # full pipeline
    # VRAM stability
    "vram_growth_limit_mib": 200,    # max acceptable growth across all iterations
    # Audio input
    "audio_duration_sec": 1.0,
    "audio_sample_rate": 16000,
}

DEFAULT_N = 5


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class IterResult:
    iteration: int
    stt_ms: float
    llm_ms: float
    tts_ttfa_ms: float
    tts_total_ms: float
    e2e_ms: float
    vram_used_mib: Optional[int]
    transcript: str
    llm_response: str
    tts_bytes: int
    error: str = ""


@dataclass
class AIValidationReport:
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    hostname: str = field(default_factory=socket.gethostname)
    n_iterations: int = 0
    iterations: list[IterResult] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    passed: bool = False
    summary: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_audio_b64(duration_sec: float = 1.0) -> str:
    """PCM16LE 16kHz mono 440Hz sine, wrapped in WAV, base64-encoded."""
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


def _vram_used_mib() -> Optional[int]:
    """Query current GPU VRAM usage via nvidia-smi. Returns None if unavailable."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split("\n")[0])
    except Exception:
        pass
    return None


def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    sv = sorted(vals)
    idx = min(int(len(sv) * pct / 100), len(sv) - 1)
    return sv[idx]


def _assert(report: AIValidationReport, name: str, passed: bool, message: str) -> None:
    status = "PASS" if passed else "FAIL"
    report.assertions.append({"name": name, "passed": passed, "status": status, "message": message})


# ── Service availability pre-check ────────────────────────────────────────────

def _check_services(client) -> list[str]:
    """Return list of services that are not reachable."""
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


# ── Pipeline stages ────────────────────────────────────────────────────────────

def _run_stt(client, audio_b64: str) -> tuple[float, str]:
    t0 = time.monotonic()
    r = client.post(SPEC["stt_url"], json={"audio_b64": audio_b64, "language": "en"}, timeout=60)
    ms = (time.monotonic() - t0) * 1000
    r.raise_for_status()
    words = r.json()["words"]
    transcript = " ".join(w["word"] for w in words)
    return ms, transcript


def _run_llm(client, text: str) -> tuple[float, str]:
    t0 = time.monotonic()
    r = client.post(
        SPEC["llm_url"],
        json={
            "model": SPEC["llm_model"],
            "messages": [{"role": "user", "content": text}],
            "max_tokens": 64,
            "stream": False,
        },
        timeout=60,
    )
    ms = (time.monotonic() - t0) * 1000
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return ms, content


def _run_tts(client, text: str) -> tuple[float, float, int]:
    """Returns (ttfa_ms, total_ms, total_bytes)."""
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
    return ttfa_ms or total_ms, total_ms, total_bytes


# ── Main validation ────────────────────────────────────────────────────────────

def run_validation(n: int = DEFAULT_N) -> AIValidationReport:
    report = AIValidationReport(n_iterations=n)

    if not _HTTPX:
        report.summary = "ABORT: httpx not installed — run: pip install httpx"
        return report

    import httpx  # noqa: PLC0415
    client = httpx.Client(timeout=120)

    print()
    print("=" * 65)
    print("  VoiceOS GPU AI Pipeline Validation")
    print(f"  Host: {report.hostname}  |  Iterations: {n}")
    print("=" * 65)

    # Pre-check: services reachable
    print("\n[1/3] Service availability...")
    unreachable = _check_services(client)
    if unreachable:
        for u in unreachable:
            print(f"  UNREACHABLE: {u}")
        print("\n  ABORT: Required services are not running.")
        print("  Start services with: systemctl start voiceos-llm voiceos-stt voiceos-tts")
        report.summary = f"ABORT: {len(unreachable)} service(s) unreachable"
        sys.exit(2)

    print(f"  STT /health/ready → HTTP 200  ✓")
    print(f"  LLM /health       → HTTP 200  ✓")
    print(f"  TTS /health/ready → HTTP 200  ✓")

    # Run pipeline iterations
    print(f"\n[2/3] Running {n} pipeline iterations (STT→LLM→TTS)...")
    audio_b64 = _make_audio_b64(SPEC["audio_duration_sec"])
    vram_baseline = _vram_used_mib()
    if vram_baseline is not None:
        print(f"  VRAM baseline: {vram_baseline} MiB")

    for i in range(n):
        t_e2e_start = time.monotonic()
        error = ""
        try:
            stt_ms, transcript = _run_stt(client, audio_b64)
            llm_ms, response = _run_llm(client, transcript)
            tts_ttfa, tts_total, tts_bytes = _run_tts(client, response[:120])
            e2e_ms = (time.monotonic() - t_e2e_start) * 1000
            vram = _vram_used_mib()
        except Exception as exc:
            error = str(exc)
            stt_ms = llm_ms = tts_ttfa = tts_total = e2e_ms = 0.0
            tts_bytes = 0
            transcript = response = ""
            vram = None

        it = IterResult(
            iteration=i + 1,
            stt_ms=round(stt_ms, 1),
            llm_ms=round(llm_ms, 1),
            tts_ttfa_ms=round(tts_ttfa, 1),
            tts_total_ms=round(tts_total, 1),
            e2e_ms=round(e2e_ms, 1),
            vram_used_mib=vram,
            transcript=transcript,
            llm_response=response[:80],
            tts_bytes=tts_bytes,
            error=error,
        )
        report.iterations.append(it)

        status = "OK" if not error else "FAIL"
        vram_str = f"  VRAM={vram}MiB" if vram is not None else ""
        print(f"  [{i+1:2d}/{n}] STT={stt_ms:.0f}ms  LLM={llm_ms:.0f}ms  "
              f"TTS_TTFA={tts_ttfa:.0f}ms  E2E={e2e_ms:.0f}ms  "
              f"[{status}]{vram_str}")
        if error:
            print(f"       ERROR: {error}")

    # Assertions
    print(f"\n[3/3] Assertions...")
    successful = [it for it in report.iterations if not it.error]

    if not successful:
        _assert(report, "A-00 At least one successful iteration", False,
                "All pipeline iterations failed")
    else:
        # Latency budget assertions (p95)
        stt_p95  = _percentile([it.stt_ms     for it in successful], 95)
        llm_p95  = _percentile([it.llm_ms     for it in successful], 95)
        ttfa_p95 = _percentile([it.tts_ttfa_ms for it in successful], 95)
        e2e_p95  = _percentile([it.e2e_ms     for it in successful], 95)

        _assert(report, "A-01 STT p95 latency within budget",
                stt_p95 <= SPEC["stt_latency_budget_ms"],
                f"STT p95={stt_p95:.0f}ms ≤ {SPEC['stt_latency_budget_ms']}ms")

        _assert(report, "A-02 LLM TTFT p95 within budget",
                llm_p95 <= SPEC["llm_ttft_budget_ms"],
                f"LLM p95={llm_p95:.0f}ms ≤ {SPEC['llm_ttft_budget_ms']}ms")

        _assert(report, "A-03 TTS TTFA p95 within budget",
                ttfa_p95 <= SPEC["tts_ttfa_budget_ms"],
                f"TTS TTFA p95={ttfa_p95:.0f}ms ≤ {SPEC['tts_ttfa_budget_ms']}ms")

        _assert(report, "A-04 E2E pipeline p95 within budget",
                e2e_p95 <= SPEC["e2e_budget_ms"],
                f"E2E p95={e2e_p95:.0f}ms ≤ {SPEC['e2e_budget_ms']}ms")

        # STT returns non-empty transcript
        non_empty_transcripts = sum(1 for it in successful if it.transcript.strip())
        _assert(report, "A-05 STT returns non-empty transcript",
                non_empty_transcripts == len(successful),
                f"{non_empty_transcripts}/{len(successful)} iterations had non-empty transcript")

        # TTS returns audio bytes
        audio_returned = sum(1 for it in successful if it.tts_bytes > 0)
        _assert(report, "A-06 TTS returns audio bytes",
                audio_returned == len(successful),
                f"{audio_returned}/{len(successful)} iterations returned TTS audio")

        # TTS audio is plausible size: each float32 sample = 4 bytes, 24kHz
        min_expected = 4 * 24000 * 0.1    # at least 100ms of audio
        large_enough = sum(1 for it in successful if it.tts_bytes >= min_expected)
        _assert(report, "A-07 TTS audio size plausible (≥100ms at 24kHz)",
                large_enough == len(successful),
                f"{large_enough}/{len(successful)} had ≥{min_expected:.0f} bytes")

        # VRAM stability
        vram_readings = [it.vram_used_mib for it in successful if it.vram_used_mib is not None]
        if len(vram_readings) >= 2:
            vram_growth = vram_readings[-1] - vram_readings[0]
            _assert(report, "A-08 VRAM growth ≤ 200 MiB (no memory leak)",
                    vram_growth <= SPEC["vram_growth_limit_mib"],
                    f"VRAM grew {vram_growth} MiB from {vram_readings[0]} → {vram_readings[-1]}")
        else:
            _assert(report, "A-08 VRAM stability (nvidia-smi not available)",
                    True, "Skipped — nvidia-smi not available in this environment")

    # Summary
    total = len(report.assertions)
    passed = sum(1 for a in report.assertions if a["passed"])
    failed = total - passed
    report.passed = (failed == 0) and len(successful) > 0

    print()
    print(f"  {'Name':<50}  {'Status':>6}")
    print(f"  {'-'*50}  {'-'*6}")
    for a in report.assertions:
        mark = "PASS" if a["passed"] else "FAIL"
        print(f"  {a['name']:<50}  {mark:>6}   {a['message']}")

    print()
    overall = "PASSED" if report.passed else "FAILED"
    report.summary = f"{overall}: {passed}/{total} assertions passed, {len(successful)}/{n} iterations succeeded"
    print(f"  {report.summary}")
    print()
    print("=" * 65)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS GPU AI Pipeline Validation")
    parser.add_argument("--n", type=int, default=DEFAULT_N,
                        help=f"Number of pipeline iterations (default: {DEFAULT_N})")
    parser.add_argument("--output", type=str, default=None,
                        help="Write JSON report to this path")
    args = parser.parse_args()

    report = run_validation(n=args.n)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"  Report written to: {args.output}")

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
