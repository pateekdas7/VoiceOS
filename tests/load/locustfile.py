"""Locust load-test script -- Sprint-028 "2. Load Testing" (V1 Ch23, V7 Ch15).

Ramp to 500 concurrent simulated calls over 10 minutes, hold 30 minutes,
ramp down over 5 minutes (``--users 500 --spawn-rate 0.83 --run-time 45m``,
see the module docstring's usage section below).

VoiceOS's own runtime pipeline is not itself an HTTP service (Media
Gateway speaks SIP/RTP, not HTTP -- see TT-006/BACKLOG.md), so a
Locust ``HttpUser`` cannot literally dial a call the way a browser dials
a REST endpoint. What *is* real, network-reachable, and load-bearing for
the AC's own gates (first-audio p95, GPU utilization) is the GPU node's
three real HTTP inference services deployed in Sprint-009
(``deployment/gpu/services/{stt,tts}/server.py`` + vLLM's own
OpenAI-compatible LLM endpoint) -- exactly the path Sprint-028.md's own
"Latency Validation" procedure instruments (STT -> CIL -> LLM TTFT -> TTS
first clause). ``FullCallUser`` below drives one simulated call as three
sequential HTTP requests against those three real endpoints (STT
``/transcribe`` -> LLM ``/v1/chat/completions`` streaming -> TTS
``/synthesize`` streaming), matching the walking-skeleton pipeline order
(``scripts/validate/walking_skeleton.py``, Sprint-012) without requiring a
real SIP/RTP client.

Usage (against the real GPU node, Phase 2):
    STT_URL=http://<gpu-host>:8100 \\
    LLM_URL=http://<gpu-host>:8000 \\
    TTS_URL=http://<gpu-host>:8200 \\
    locust -f tests/load/locustfile.py --headless \\
        --users 500 --spawn-rate 1 --run-time 45m \\
        --host http://<gpu-host>:8100

The ``--host`` value is required by Locust's CLI but unused by
``FullCallUser`` (every request uses an absolute URL built from the three
``*_URL`` env vars above) -- pass the STT host to satisfy the flag.

Architecture: V1 Ch23 (Latency Budget); V7 Ch15 (Load Testing).
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from typing import Any

from locust import HttpUser, between, events, task
from locust.env import Environment

STT_URL = os.environ.get("STT_URL", "http://localhost:8100")
LLM_URL = os.environ.get("LLM_URL", "http://localhost:8000")
TTS_URL = os.environ.get("TTS_URL", "http://localhost:8200")

_TEST_TRANSCRIPTS = [
    "mera bakaya kitna hai",
    "main abhi paise nahi de sakta",
    "mujhe kiston mein bhugtan karna hai",
    "kya settlement ho sakta hai",
    "theek hai main kal paise dunga",
]
"""Same fixed Hindi/Hinglish collections-call utterances as
``scripts/validate/walking_skeleton.py`` -- representative of this
project's real domain traffic, not generic Lorem Ipsum load."""


def _silence_pcm16le_b64(duration_ms: int = 2000, sample_rate: int = 16000) -> str:
    """A short, valid PCM16LE silence clip -- STT load-tests transcription latency, not accuracy."""
    sample_count = int(sample_rate * duration_ms / 1000)
    frame = struct.pack(f"<{sample_count}h", *([0] * sample_count))
    return base64.b64encode(frame).decode("ascii")


_SILENCE_AUDIO_B64 = _silence_pcm16le_b64()


class FullCallUser(HttpUser):
    """Simulates one end-to-end call turn: STT -> LLM (streaming TTFT) -> TTS (streaming first clause).

    ``wait_time`` models inter-turn think time within a single call
    (customer speaking, agent listening) -- kept short since the AC
    measures per-request latency under sustained concurrency, not
    realistic call pacing.
    """

    wait_time = between(0.5, 2.0)
    host = STT_URL  # required by HttpUser; overridden per-request below via absolute URLs

    @task
    def full_call_turn(self) -> None:
        call_id = f"loadtest-{self.environment.runner.user_count if self.environment.runner else 0}-{time.time_ns()}"

        self._call_stt(call_id)
        ttft_ms = self._call_llm_ttft(call_id)
        self._call_tts_first_clause(call_id, ttft_ms)

    def _call_stt(self, call_id: str) -> None:
        payload = {"audio_b64": _SILENCE_AUDIO_B64, "language": "hi", "beam_size": 5}
        with self.client.post(
            f"{STT_URL}/transcribe",
            json=payload,
            name="/transcribe (STT)",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"STT /transcribe returned {response.status_code}")

    def _call_llm_ttft(self, call_id: str) -> float:
        """Streams the LLM's chat-completion response; reports time-to-first-token as the request's own latency."""
        transcript = _TEST_TRANSCRIPTS[hash(call_id) % len(_TEST_TRANSCRIPTS)]
        payload = {
            "model": "qwen2.5-7b-instruct-fp8",
            "messages": [
                {"role": "system", "content": "You are a collections agent. Respond briefly in Hindi."},
                {"role": "user", "content": transcript},
            ],
            "stream": True,
            "max_tokens": 64,
        }
        start = time.perf_counter()
        ttft_ms = 0.0
        try:
            with self.client.post(
                f"{LLM_URL}/v1/chat/completions",
                json=payload,
                name="/v1/chat/completions (LLM TTFT)",
                catch_response=True,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    response.failure(f"LLM endpoint returned {response.status_code}")
                    return 0.0
                for line in response.iter_lines():
                    if line:
                        ttft_ms = (time.perf_counter() - start) * 1000.0
                        break
        except Exception as exc:
            events.request.fire(  # type: ignore[no-untyped-call]  # locust's own event-hook typing is incomplete
                request_type="POST",
                name="/v1/chat/completions (LLM TTFT)",
                response_time=(time.perf_counter() - start) * 1000.0,
                response_length=0,
                exception=exc,
            )
        return ttft_ms

    def _call_tts_first_clause(self, call_id: str, _preceding_ttft_ms: float) -> None:
        transcript = _TEST_TRANSCRIPTS[hash(call_id) % len(_TEST_TRANSCRIPTS)]
        payload = {"text": transcript, "speaker": "kavya"}
        start = time.perf_counter()
        ttfa_ms: float | None = None
        exception: Exception | None = None

        try:
            resp = self.client.post(f"{TTS_URL}/synthesize", json=payload, stream=True)
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=None):
                if chunk and ttfa_ms is None:
                    ttfa_ms = (time.perf_counter() - start) * 1000.0
                # Drain full response — do NOT break early. The server runs Veena in a
                # background daemon thread; disconnecting early orphans that thread, causing
                # concurrent synthesis contention on the GPU (observed: 15-17s TTFA vs
                # 700ms baseline). Draining ensures the thread finishes before the next
                # Locust iteration begins.
        except Exception as exc:
            exception = exc

        # Fire event with TTFA (not full drain time) as the reported latency so
        # Locust's p95 stat reflects time-to-first-audio, not full synthesis.
        events.request.fire(  # type: ignore[no-untyped-call]
            request_type="POST",
            name="/synthesize (TTS TTFA)",
            response_time=ttfa_ms if ttfa_ms is not None else (time.perf_counter() - start) * 1000.0,
            response_length=0,
            exception=exception,
        )


class HealthPollUser(HttpUser):
    """Lightweight background user polling /health/ready on all three GPU services.

    Models the real readiness-probe traffic Kubernetes would generate
    concurrently with call load, so the load test's own error-rate
    measurement isn't blind to probe-path degradation.
    """

    wait_time = between(5.0, 10.0)
    weight = 1  # FullCallUser instances vastly outnumber this by default Locust weighting

    @task
    def poll_health(self) -> None:
        for base_url, name in ((STT_URL, "STT"), (LLM_URL, "LLM"), (TTS_URL, "TTS")):
            try:
                self.client.get(f"{base_url}/health/ready", name=f"/health/ready ({name})")
            except Exception:
                pass


@events.quitting.add_listener  # type: ignore[untyped-decorator]  # locust's own event-hook typing is incomplete
def _assert_load_test_gates(environment: Environment, **_kwargs: Any) -> None:
    """Sprint-028.md AC: p95 <= 1.65s (10% degradation budget), error rate < 0.1%.

    Sets Locust's own process exit code non-zero if the AC gates were not
    met, so a CI/scheduled load-test run fails loudly rather than
    requiring a human to read the summary table.
    """
    stats = environment.stats.total
    if stats.num_requests == 0:
        return

    error_rate = stats.num_failures / stats.num_requests
    p95_ms = stats.get_response_time_percentile(0.95)

    if error_rate >= 0.001:
        print(f"[locustfile] FAIL: error rate {error_rate:.4%} >= 0.1% gate")
        environment.process_exit_code = 1
    if p95_ms is not None and p95_ms > 1650.0:
        print(f"[locustfile] FAIL: p95 {p95_ms:.0f}ms > 1650ms (1.65s) gate")
        environment.process_exit_code = 1


def load_test_payload(text: str = "mera bakaya kitna hai") -> dict[str, Any]:
    """Exposed for unit-test-level payload-shape assertions without importing Locust's runtime machinery."""
    return {"text": text, "speaker": "kavya"}


if __name__ == "__main__":
    # `python tests/load/locustfile.py` prints the resolved target URLs
    # for a quick sanity check before an interactive `locust -f ...` run.
    print(json.dumps({"stt_url": STT_URL, "llm_url": LLM_URL, "tts_url": TTS_URL}, indent=2))
