"""Shared pytest fixtures for VoiceOS GPU runtime tests.

Fixtures start the three GPU services (STT, LLM, TTS) in mock mode as
subprocesses, wait until each reports healthy, then tear them down after
the test session. All services run on their configured ports.

Environment:
    VOICEOS_MODE=dev  — automatically sets STT/LLM/TTS to mock mode.
    STT_SERVICE_PORT  — default 8100
    LLM_SERVICE_PORT  — default 8000
    TTS_SERVICE_PORT  — default 8200

Usage in tests:
    def test_something(stt_client, llm_client, tts_client):
        resp = stt_client.post("/transcribe", json={...})
        assert resp.status_code == 200
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

# ── Port configuration ─────────────────────────────────────────────────────────

STT_PORT = int(os.environ.get("STT_SERVICE_PORT", "8100"))
LLM_PORT = int(os.environ.get("LLM_SERVICE_PORT", "8000"))
TTS_PORT = int(os.environ.get("TTS_SERVICE_PORT", "8200"))

# Path to the GPU deployment directory (one level up from this tests/ dir)
_GPU_DIR = Path(__file__).parent.parent
_SERVICES_DIR = _GPU_DIR / "services"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _wait_healthy(url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _start_service(
    script: Path,
    port: int,
    health_path: str,
    extra_args: list[str] | None = None,
) -> subprocess.Popen:  # type: ignore[type-arg]
    cmd = [sys.executable, str(script), "--port", str(port), "--host", "127.0.0.1"]
    if extra_args:
        cmd.extend(extra_args)
    env = {**os.environ, "VOICEOS_MODE": "dev"}
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    health_url = f"http://127.0.0.1:{port}{health_path}"
    if not _wait_healthy(health_url):
        # Capture any startup output to aid debugging
        if proc.stdout:
            out = proc.stdout.read(4096)
            raise RuntimeError(
                f"Service {script.name} on :{port} did not become healthy within 20s.\n"
                f"Output: {out.decode(errors='replace')}"
            )
        raise RuntimeError(f"Service {script.name} on :{port} did not become healthy within 20s.")
    return proc


# ── Service fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def stt_proc() -> subprocess.Popen:  # type: ignore[type-arg]
    """Start mock STT server for the test session."""
    proc = _start_service(
        script=_SERVICES_DIR / "stt" / "server.py",
        port=STT_PORT,
        health_path="/health/ready",
        extra_args=["--mock"],
    )
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="session")
def llm_proc() -> subprocess.Popen:  # type: ignore[type-arg]
    """Start mock LLM server for the test session."""
    proc = _start_service(
        script=_SERVICES_DIR / "llm" / "mock_server.py",
        port=LLM_PORT,
        health_path="/health",
    )
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="session")
def tts_proc() -> subprocess.Popen:  # type: ignore[type-arg]
    """Start mock TTS server for the test session."""
    proc = _start_service(
        script=_SERVICES_DIR / "tts" / "server.py",
        port=TTS_PORT,
        health_path="/health/ready",
        extra_args=["--mock"],
    )
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


# ── HTTP client fixtures ───────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def stt_client(stt_proc: subprocess.Popen) -> httpx.Client:  # type: ignore[type-arg]
    with httpx.Client(base_url=f"http://127.0.0.1:{STT_PORT}", timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def llm_client(llm_proc: subprocess.Popen) -> httpx.Client:  # type: ignore[type-arg]
    with httpx.Client(base_url=f"http://127.0.0.1:{LLM_PORT}", timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def tts_client(tts_proc: subprocess.Popen) -> httpx.Client:  # type: ignore[type-arg]
    with httpx.Client(base_url=f"http://127.0.0.1:{TTS_PORT}", timeout=60.0) as client:
        yield client


# ── Audio helpers ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def silence_b64() -> str:
    """1 second of PCM16LE silence at 16 kHz, base64-encoded."""
    import base64
    import struct

    n_samples = 16000
    pcm = struct.pack(f"<{n_samples}h", *([0] * n_samples))
    return base64.b64encode(pcm).decode("ascii")


@pytest.fixture(scope="session")
def tone_b64() -> str:
    """1 second of 440 Hz sine wave at 16 kHz PCM16LE, base64-encoded."""
    import base64
    import math
    import struct

    sample_rate = 16000
    freq = 440
    n_samples = sample_rate
    samples = [int(32767 * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(n_samples)]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    return base64.b64encode(pcm).decode("ascii")
