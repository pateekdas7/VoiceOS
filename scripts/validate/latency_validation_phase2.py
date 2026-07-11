#!/usr/bin/env python3
"""Sprint-028 Phase 2 — 100-call sequential latency validation.

Measures per-stage p50/p95/p99 against real GPU infrastructure.

Stages measured (real GPU inference):
  STT   — POST audio/silence to Whisper endpoint (port 8100/transcribe)
  LLM   — POST to vLLM streaming endpoint (port 8000), measure TTFT
  TTS   — POST to Veena streaming endpoint (port 8200), measure TTFA
  Total — STT + LLM_TTFT + TTS_TTFA (client-side wall clock)

Stages NOT measured (TT-006 health-stub pods — no real processing):
  Media GW, Audio Session Manager, Audio Preprocessing, VAD/Endpointing

CIL (Conversation Intelligence Layer) timing is NOT separately measured here
because it is a pure-Python in-process library. It is covered by
walking_skeleton.py which instruments (turn_start → first AudioClause) —
that measurement subsumes CIL + LLM + TTS.

Gate: first-audio p95 <= 1500ms  (V1 Ch23 / Sprint-028 AC)

Usage:
    python3 scripts/validate/latency_validation_phase2.py \\
        --gpu-host 217.18.55.78 --calls 100

RI-8 NOTE: GPU Scheduler is BLOCKED (TT-015 / stub path). This test
exercises the real inference path without VoiceOS admission control.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
import time
from statistics import median, quantiles
from typing import Any

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

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
    "byaaj kitna hai",
    "kya mujhe rasid milegi",
    "main online payment kar sakta hoon",
    "mujhe nahi pata tha itna bakaya hai",
    "kya aap mujhe EMI de sakte hain",
    "main paanch hazar aaj de sakta hoon",
    "mujhe RBI guidelines pata hain",
    "theek hai main samajh gaya",
    "Haan bhai, theek hai payment karte hain",
    "Arey yaar, thoda time do mujhe",
]

_SILENCE_B64: str = ""


def _make_silence_b64(duration_ms: int = 2000, sample_rate: int = 16000) -> str:
    count = int(sample_rate * duration_ms / 1000)
    return base64.b64encode(struct.pack(f"<{count}h", *([0] * count))).decode()


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = max(0, int(p * len(s)) - 1)
    return s[idx]


# ---------------------------------------------------------------------------
# Stage measurements
# ---------------------------------------------------------------------------


def measure_stt(client: httpx.Client, stt_url: str) -> float:
    """POST 2s silence to Whisper /transcribe. Returns elapsed ms."""
    global _SILENCE_B64
    if not _SILENCE_B64:
        _SILENCE_B64 = _make_silence_b64()
    t0 = time.perf_counter()
    resp = client.post(
        f"{stt_url}/transcribe",
        json={"audio_b64": _SILENCE_B64, "language": "hi", "beam_size": 1},
        timeout=30.0,
    )
    resp.raise_for_status()
    return (time.perf_counter() - t0) * 1000


def measure_llm_ttft(client: httpx.Client, llm_url: str, text: str) -> float:
    """Stream vLLM /v1/chat/completions. Returns time-to-first-token ms."""
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [
            {"role": "system", "content": "You are a collections agent. Reply briefly in Hindi, 1-2 sentences."},
            {"role": "user", "content": text},
        ],
        "stream": True,
        "max_tokens": 48,
    }
    t0 = time.perf_counter()
    ttft_ms = 0.0
    with client.stream("POST", f"{llm_url}/v1/chat/completions", json=payload, timeout=30.0) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            line = line.strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data:"):
                try:
                    chunk = json.loads(line[5:].strip())
                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        ttft_ms = (time.perf_counter() - t0) * 1000
                        break
                except (json.JSONDecodeError, IndexError):
                    continue
    if ttft_ms == 0.0:
        ttft_ms = (time.perf_counter() - t0) * 1000
    return ttft_ms


def measure_tts_ttfa(client: httpx.Client, tts_url: str, text: str) -> float:
    """Stream Veena /synthesize. Returns time-to-first-audio-bytes ms (client-side)."""
    t0 = time.perf_counter()
    ttfa_ms = 0.0
    with client.stream(
        "POST",
        f"{tts_url}/synthesize",
        json={"text": text, "speaker": "kavya"},
        timeout=60.0,
    ) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes():
            if chunk:
                ttfa_ms = (time.perf_counter() - t0) * 1000
                break
    if ttfa_ms == 0.0:
        ttfa_ms = (time.perf_counter() - t0) * 1000
    return ttfa_ms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprint-028 Phase 2 latency validation")
    parser.add_argument("--gpu-host", required=True)
    parser.add_argument("--calls", type=int, default=100)
    parser.add_argument("--skip-stt", action="store_true", help="Skip STT stage (faster for CIL/LLM/TTS focus)")
    args = parser.parse_args()

    stt_url = f"http://{args.gpu_host}:8100"
    llm_url = f"http://{args.gpu_host}:8000"
    tts_url = f"http://{args.gpu_host}:8200"

    print(f"Sprint-028 Phase 2 Latency Validation — {args.calls} sequential calls")
    print(f"STT: {stt_url} | LLM: {llm_url} | TTS: {tts_url}")
    print("RI-8: BLOCKED (stub scheduler — see preflight report)")
    print("NOTE: Media GW / ASM / Preprocessing / VAD = TT-006 stubs, not measured")
    print()

    stt_times: list[float] = []
    llm_times: list[float] = []
    tts_times: list[float] = []
    total_times: list[float] = []
    errors: list[str] = []

    with httpx.Client() as client:
        # Health check
        try:
            client.get(f"{stt_url}/health/ready", timeout=5).raise_for_status()
            client.get(f"{tts_url}/health/ready", timeout=5).raise_for_status()
            client.get(f"{llm_url}/health", timeout=5).raise_for_status()
            print("All GPU services healthy. Starting test...\n")
        except Exception as e:
            print(f"ABORT: GPU service health check failed: {e}", file=sys.stderr)
            return 1

        for i in range(args.calls):
            transcript = _TRANSCRIPTS[i % len(_TRANSCRIPTS)]
            call_start = time.perf_counter()

            try:
                # Stage 1: STT
                if not args.skip_stt:
                    stt_ms = measure_stt(client, stt_url)
                    stt_times.append(stt_ms)
                else:
                    stt_ms = 0.0

                # Stage 2: LLM TTFT
                llm_ms = measure_llm_ttft(client, llm_url, transcript)
                llm_times.append(llm_ms)

                # Stage 3: TTS TTFA
                tts_ms = measure_tts_ttfa(client, tts_url, transcript)
                tts_times.append(tts_ms)

                total_ms = (time.perf_counter() - call_start) * 1000
                total_times.append(total_ms)

                print(
                    f"[{i+1:03d}/{args.calls}] "
                    f"STT={stt_ms:.0f}ms  LLM={llm_ms:.0f}ms  TTS={tts_ms:.0f}ms  "
                    f"total={total_ms:.0f}ms  text={transcript[:30]!r}"
                )

            except Exception as exc:
                err = f"Call {i+1}: {exc}"
                errors.append(err)
                print(f"[{i+1:03d}/{args.calls}] ERROR: {exc}")
                stt_times.append(9999.0) if not args.skip_stt else None
                llm_times.append(9999.0)
                tts_times.append(9999.0)
                total_times.append(9999.0)

    # Results
    print()
    print("=" * 70)
    print(f"SPRINT-028 PHASE 2 LATENCY RESULTS — {args.calls} SEQUENTIAL CALLS")
    print("=" * 70)

    def _stats(label: str, data: list[float]) -> None:
        if not data:
            return
        p50 = _percentile(data, 0.50)
        p95 = _percentile(data, 0.95)
        p99 = _percentile(data, 0.99)
        mn = min(data)
        mx = max(data)
        print(f"  {label:<20} p50={p50:.0f}ms  p95={p95:.0f}ms  p99={p99:.0f}ms  min={mn:.0f}ms  max={mx:.0f}ms")

    if not args.skip_stt:
        _stats("STT (Whisper):", stt_times)
    _stats("LLM TTFT (Qwen):", llm_times)
    _stats("TTS TTFA (Veena):", tts_times)
    _stats("TOTAL first-audio:", total_times)

    print()
    total_p95 = _percentile(total_times, 0.95)
    gate_label = "first-audio" if args.skip_stt else "STT+LLM+TTS"
    gate_pass = total_p95 <= 1500.0
    gate_symbol = "PASS" if gate_pass else "FAIL"
    print(f"GATE ({gate_label} p95 <= 1500ms): {gate_symbol} — measured {total_p95:.0f}ms")

    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e}")

    if not gate_pass:
        print("\nBottleneck: TTS TTFA is the dominant latency contributor.")
        tts_p95 = _percentile(tts_times, 0.95)
        llm_p95 = _percentile(llm_times, 0.95)
        print(f"  TTS p95={tts_p95:.0f}ms (budget 250ms, over by {tts_p95-250:.0f}ms)")
        print(f"  LLM p95={llm_p95:.0f}ms (budget 350ms)")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
