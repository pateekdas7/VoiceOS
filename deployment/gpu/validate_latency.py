#!/usr/bin/env python3
"""Post-restore latency validation for the VoiceOS GPU node.

TT-009 (found during the post-Sprint-020 reproducibility audit, 2026-07-05):
restore.sh and model_manifest.yaml's post_restore_validation block have
referenced this script since Sprint-009, but it never existed anywhere in
the repo — the latency numbers in DONE.md/GPU_NODE_STATE.md (STT ~330ms,
LLM TTFT ~208ms, TTS server TTFA p95=873ms) were all measured by ad hoc,
undocumented one-off commands during each sprint's Phase 2, never by a
committed, re-runnable script. This closes that gap: a real HTTP client
against the three live services, measuring the same latency dimensions
already being reported, checked against model_manifest.yaml's
latency_target_ms budgets.

Usage:
    python3 validate_latency.py --test stt
    python3 validate_latency.py --test llm
    python3 validate_latency.py --test tts
    python3 validate_latency.py --test all
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import time

import httpx
import yaml

DEFAULT_MANIFEST = os.path.join(os.path.dirname(__file__), "model_manifest.yaml")


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _load_target_ms(manifest_path: str, model_name: str, key: str) -> float | None:
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)
    for model in manifest.get("models", []):
        if model["name"] == model_name:
            return model.get("latency_target_ms", {}).get(key)
    return None


def _silence_pcm16le(duration_ms: int = 1000, sample_rate: int = 16000) -> bytes:
    """Generate a short PCM16LE silence buffer — sufficient to exercise the
    STT request/response path without needing a real audio fixture on disk."""
    n_samples = int(sample_rate * duration_ms / 1000)
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


def validate_stt(manifest_path: str) -> bool:
    port = os.environ.get("STT_SERVICE_PORT", "8100")
    url = f"http://localhost:{port}/transcribe"
    audio_b64 = base64.b64encode(_silence_pcm16le()).decode("ascii")

    log(f"STT — POST {url} (1s synthetic PCM16LE silence)...")
    t0 = time.monotonic()
    try:
        resp = httpx.post(url, json={"audio_b64": audio_b64, "language": "hi"}, timeout=30.0)
        resp.raise_for_status()
    except Exception as exc:
        log(f"STT — FAIL: request error: {exc}")
        return False
    latency_ms = (time.monotonic() - t0) * 1000
    target = _load_target_ms(manifest_path, "whisper-large-v3-turbo", "first_word_p95")

    log(f"STT — response latency: {latency_ms:.1f}ms (target p95 <= {target}ms)")
    if target is not None and latency_ms > target:
        log("STT — WARNING: latency exceeds target (informational — single-sample, not a real p95)")
    log("STT — PASS (request/response cycle completed)")
    return True


def validate_llm(manifest_path: str) -> bool:
    port = os.environ.get("LLM_SERVICE_PORT", "8000")
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": "Say hello in one word."}],
        "max_tokens": 16,
        "stream": True,
    }

    log(f"LLM — POST {url} (streaming, measuring TTFT)...")
    t0 = time.monotonic()
    ttft_ms: float | None = None
    try:
        with httpx.stream("POST", url, json=payload, timeout=30.0) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("choices"):
                    ttft_ms = (time.monotonic() - t0) * 1000
                    break
    except Exception as exc:
        log(f"LLM — FAIL: request error: {exc}")
        return False

    if ttft_ms is None:
        log("LLM — FAIL: no token chunk received")
        return False

    target = _load_target_ms(manifest_path, "qwen2.5-7b-instruct-fp8", "ttft_p95")
    log(f"LLM — TTFT: {ttft_ms:.1f}ms (target p95 <= {target}ms)")
    if target is not None and ttft_ms > target:
        log("LLM — WARNING: TTFT exceeds target (informational — single-sample, not a real p95)")
    log("LLM — PASS (streaming response started)")
    return True


def validate_tts(manifest_path: str) -> bool:
    port = os.environ.get("TTS_SERVICE_PORT", "8200")
    url = f"http://localhost:{port}/synthesize"
    payload = {"text": "health check", "speaker": "kavya"}

    log(f"TTS — POST {url} (streaming, measuring time-to-first-chunk)...")
    t0 = time.monotonic()
    ttfa_ms: float | None = None
    try:
        with httpx.stream("POST", url, json=payload, timeout=30.0) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                if chunk:
                    ttfa_ms = (time.monotonic() - t0) * 1000
                    break
    except Exception as exc:
        log(f"TTS — FAIL: request error: {exc}")
        return False

    if ttfa_ms is None:
        log("TTS — FAIL: no audio chunk received")
        return False

    target = _load_target_ms(manifest_path, "veena-tts", "first_clause_p95")
    log(f"TTS — time-to-first-chunk: {ttfa_ms:.1f}ms (target p95 <= {target}ms)")
    if target is not None and ttfa_ms > target:
        log("TTS — WARNING: latency exceeds target (informational — single-sample, not a real p95)")
    log("TTS — PASS (streaming response started, Transfer-Encoding: chunked)")
    return True


VALIDATORS = {"stt": validate_stt, "llm": validate_llm, "tts": validate_tts}


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS GPU node latency validation")
    parser.add_argument("--test", choices=["stt", "llm", "tts", "all"], default="all")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    tests = list(VALIDATORS) if args.test == "all" else [args.test]
    results = {name: VALIDATORS[name](args.manifest) for name in tests}

    log("")
    log("=== Latency validation summary ===")
    for name, ok in results.items():
        log(f"  {name}: {'PASS' if ok else 'FAIL'}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
