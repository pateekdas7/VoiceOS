#!/usr/bin/env python3
"""Sprint-028 §2 — Concurrent load test (threading-based, no locust dependency).

Simulates N concurrent call turns (STT → LLM TTFT → TTS TTFA drain) against the
real GPU node. Designed for single-L4 scale testing (10–20 users), NOT 500-user
production scale (requires GPU fleet — V7 Ch6).

Gate (Sprint-028 AC): first-audio p95 ≤ 1650ms under concurrent load.
Note: Locust's 10% degradation budget over the 1500ms baseline = 1650ms.

Usage (from CPU node, intra-DC path):
    python3 load_test_concurrent.py --gpu-host 217.18.55.78 --users 10 --duration 120

GPU thermal note: Single L4 throttles after ~110s continuous inference (observed in
latency_intra_dc.py). Concurrent load will accelerate thermal throttling. p95 values
at throttled state are expected to exceed gate; this test documents the degradation curve.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
import threading
import time
from collections import defaultdict
from statistics import median
from typing import Any

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("ERROR: requests not installed.", file=sys.stderr)
    sys.exit(1)

GPU_HOST = "217.18.55.78"
STT_PORT = 8100
LLM_PORT = 8000
TTS_PORT = 8200

_TRANSCRIPTS = [
    "mera bakaya kitna hai",
    "main abhi paise nahi de sakta",
    "mujhe kiston mein bhugtan karna hai",
    "meri tankha nahi aayi hai",
    "kya settlement ho sakta hai",
    "mujhe ek mahine ka samay chahiye",
    "main das hazar de sakta hoon",
    "aap mujhe pareshan kyun kar rahe hain",
    "theek hai main kal paise dunga",
    "mera khata number kya hai",
]

_SILENCE_B64: str = ""


def _make_silence_b64(duration_ms: int = 2000, sample_rate: int = 16000) -> str:
    count = int(sample_rate * duration_ms / 1000)
    return base64.b64encode(struct.pack(f"<{count}h", *([0] * count))).decode()


def _pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = max(0, int(p * len(s)) - 1)
    return s[idx]


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stt: list[float] = []
        self.llm: list[float] = []
        self.tts: list[float] = []
        self.first_audio: list[float] = []
        self.errors: list[str] = []
        self.call_count = 0

    def record(
        self,
        stt_ms: float,
        llm_ms: float,
        tts_ms: float,
        first_audio_ms: float,
    ) -> None:
        with self._lock:
            self.stt.append(stt_ms)
            self.llm.append(llm_ms)
            self.tts.append(tts_ms)
            self.first_audio.append(first_audio_ms)
            self.call_count += 1

    def record_error(self, msg: str) -> None:
        with self._lock:
            self.errors.append(msg)
            self.call_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            n = len(self.first_audio)
            if n == 0:
                return {"calls": 0}
            return {
                "calls": self.call_count,
                "errors": len(self.errors),
                "stt_p50": _pct(self.stt, 0.50),
                "stt_p95": _pct(self.stt, 0.95),
                "llm_p50": _pct(self.llm, 0.50),
                "llm_p95": _pct(self.llm, 0.95),
                "tts_p50": _pct(self.tts, 0.50),
                "tts_p95": _pct(self.tts, 0.95),
                "fa_p50": _pct(self.first_audio, 0.50),
                "fa_p95": _pct(self.first_audio, 0.95),
                "fa_p99": _pct(self.first_audio, 0.99),
                "fa_min": min(self.first_audio),
                "fa_max": max(self.first_audio),
            }


def _make_session(gpu_host: str) -> requests.Session:
    s = requests.Session()
    # No retries — we want to see real failures, not hide them
    adapter = HTTPAdapter(max_retries=0)
    s.mount("http://", adapter)
    return s


def _call_stt(session: requests.Session, stt_url: str, silence_b64: str) -> float:
    t0 = time.perf_counter()
    resp = session.post(
        f"{stt_url}/transcribe",
        json={"audio_b64": silence_b64, "language": "hi", "beam_size": 1},
        timeout=30.0,
    )
    resp.raise_for_status()
    return (time.perf_counter() - t0) * 1000


def _call_llm_ttft(session: requests.Session, llm_url: str, text: str) -> float:
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [
            {"role": "system", "content": "You are a collections agent. Reply briefly in Hindi."},
            {"role": "user", "content": text},
        ],
        "stream": True,
        "max_tokens": 48,
    }
    t0 = time.perf_counter()
    ttft_ms = 0.0
    with session.post(f"{llm_url}/v1/chat/completions", json=payload,
                      stream=True, timeout=30.0) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line:
                decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                if decoded.startswith("data:") and decoded != "data: [DONE]":
                    try:
                        chunk = json.loads(decoded[5:].strip())
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            ttft_ms = (time.perf_counter() - t0) * 1000
                            break
                    except (json.JSONDecodeError, IndexError):
                        pass
    if ttft_ms == 0.0:
        ttft_ms = (time.perf_counter() - t0) * 1000
    return ttft_ms


def _call_tts_ttfa(session: requests.Session, tts_url: str, text: str) -> float:
    """Returns TTFA (time to first audio byte). Drains full response to prevent
    orphaned server synthesis threads (see latency_validation_phase2.py notes)."""
    t0 = time.perf_counter()
    ttfa_ms = 0.0
    with session.post(f"{tts_url}/synthesize", json={"text": text, "speaker": "kavya"},
                      stream=True, timeout=120.0) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk and ttfa_ms == 0.0:
                ttfa_ms = (time.perf_counter() - t0) * 1000
            # Drain full response — prevent orphaned Veena thread
    if ttfa_ms == 0.0:
        ttfa_ms = (time.perf_counter() - t0) * 1000
    return ttfa_ms


def _worker(
    worker_id: int,
    gpu_host: str,
    stop_event: threading.Event,
    metrics: MetricsCollector,
    silence_b64: str,
) -> None:
    stt_url = f"http://{gpu_host}:{STT_PORT}"
    llm_url = f"http://{gpu_host}:{LLM_PORT}"
    tts_url = f"http://{gpu_host}:{TTS_PORT}"
    session = _make_session(gpu_host)
    transcript_idx = worker_id % len(_TRANSCRIPTS)

    while not stop_event.is_set():
        text = _TRANSCRIPTS[transcript_idx % len(_TRANSCRIPTS)]
        transcript_idx += 1
        try:
            stt_ms = _call_stt(session, stt_url, silence_b64)
            llm_ms = _call_llm_ttft(session, llm_url, text)
            tts_ms = _call_tts_ttfa(session, tts_url, text)
            fa_ms = stt_ms + llm_ms + tts_ms
            metrics.record(stt_ms, llm_ms, tts_ms, fa_ms)
        except Exception as exc:
            metrics.record_error(f"worker-{worker_id}: {exc}")
        # No sleep — continuous load to stress GPU


def _health_check(gpu_host: str) -> bool:
    stt_url = f"http://{gpu_host}:{STT_PORT}"
    llm_url = f"http://{gpu_host}:{LLM_PORT}"
    tts_url = f"http://{gpu_host}:{TTS_PORT}"
    try:
        requests.get(f"{stt_url}/health/ready", timeout=5).raise_for_status()
        requests.get(f"{tts_url}/health/ready", timeout=5).raise_for_status()
        requests.get(f"{llm_url}/health", timeout=5).raise_for_status()
        return True
    except Exception as e:
        print(f"ABORT: GPU service health check failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprint-028 concurrent load test")
    parser.add_argument("--gpu-host", default=GPU_HOST)
    parser.add_argument("--users", type=int, default=10, help="Concurrent virtual users (10-20 max for single L4)")
    parser.add_argument("--duration", type=int, default=120, help="Test duration in seconds")
    parser.add_argument("--report-interval", type=int, default=30, help="Progress report every N seconds")
    args = parser.parse_args()

    if args.users > 20:
        print(f"WARNING: --users {args.users} exceeds single-L4 safe limit (20).")
        print("Single L4 GPU thermal throttles under sustained concurrent load.")
        print("Set --users ≤ 20 for single-GPU testing, or provision GPU fleet.")

    print(f"Sprint-028 Load Test — {args.users} concurrent users, {args.duration}s duration")
    print(f"GPU node: {args.gpu_host}")
    print("NOTE: Single L4 GPU; thermal throttling expected after ~110s")
    print("NOTE: Gate is first-audio p95 ≤ 1650ms (1500ms + 10% concurrent degradation budget)")
    print()

    if not _health_check(args.gpu_host):
        return 1

    silence_b64 = _make_silence_b64()
    metrics = MetricsCollector()
    stop_event = threading.Event()

    print(f"All GPU services healthy. Starting {args.users} worker threads...\n")
    test_start = time.perf_counter()

    threads = []
    for i in range(args.users):
        t = threading.Thread(
            target=_worker,
            args=(i, args.gpu_host, stop_event, metrics, silence_b64),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Progress reports
    next_report = args.report_interval
    while (elapsed := time.perf_counter() - test_start) < args.duration:
        if elapsed >= next_report:
            snap = metrics.snapshot()
            calls = snap.get("calls", 0)
            errors = snap.get("errors", 0)
            error_rate = errors / calls * 100 if calls > 0 else 0
            throughput = calls / elapsed
            print(
                f"[t={elapsed:.0f}s] calls={calls} errors={errors} ({error_rate:.1f}%) "
                f"tput={throughput:.2f}/s  "
                f"fa_p50={snap.get('fa_p50', 0):.0f}ms  fa_p95={snap.get('fa_p95', 0):.0f}ms"
            )
            next_report += args.report_interval
        time.sleep(1)

    stop_event.set()
    for t in threads:
        t.join(timeout=30)

    total_elapsed = time.perf_counter() - test_start
    snap = metrics.snapshot()

    print()
    print("=" * 70)
    print(f"SPRINT-028 LOAD TEST RESULTS — {args.users} concurrent users, {total_elapsed:.0f}s")
    print("=" * 70)

    calls = snap.get("calls", 0)
    errors = snap.get("errors", 0)
    error_rate = errors / calls * 100 if calls > 0 else 0
    throughput = calls / total_elapsed

    print(f"  Total calls:    {calls}")
    print(f"  Errors:         {errors} ({error_rate:.2f}%)")
    print(f"  Throughput:     {throughput:.3f} calls/sec")
    print()
    print(f"  STT            p50={snap.get('stt_p50', 0):.0f}ms  p95={snap.get('stt_p95', 0):.0f}ms")
    print(f"  LLM TTFT       p50={snap.get('llm_p50', 0):.0f}ms  p95={snap.get('llm_p95', 0):.0f}ms")
    print(f"  TTS TTFA       p50={snap.get('tts_p50', 0):.0f}ms  p95={snap.get('tts_p95', 0):.0f}ms")
    print(f"  FIRST-AUDIO    p50={snap.get('fa_p50', 0):.0f}ms  p95={snap.get('fa_p95', 0):.0f}ms  "
          f"p99={snap.get('fa_p99', 0):.0f}ms  min={snap.get('fa_min', 0):.0f}ms  max={snap.get('fa_max', 0):.0f}ms")
    print()

    fa_p95 = snap.get("fa_p95", float("inf"))
    error_gate = error_rate < 0.1
    latency_gate = fa_p95 <= 1650.0
    gate_pass = error_gate and latency_gate

    print(f"GATE (first-audio p95 ≤ 1650ms): {'PASS' if latency_gate else 'FAIL'} — measured {fa_p95:.0f}ms")
    print(f"GATE (error rate < 0.1%):          {'PASS' if error_gate else 'FAIL'} — measured {error_rate:.2f}%")
    print(f"OVERALL: {'PASS' if gate_pass else 'FAIL'}")

    if metrics.errors:
        print(f"\nSample errors (first 10):")
        for e in metrics.errors[:10]:
            print(f"  {e}")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
