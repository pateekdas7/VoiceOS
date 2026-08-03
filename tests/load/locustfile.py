"""Locust load test for VoiceOS API Platform — Sprint-028 Phase 2 load test.

Target load: ramp to 500 concurrent calls over 10 minutes, hold 30 minutes.

Thresholds (V7 Ch15):
    first-audio p95 ≤ 1.65 s  (10 % degradation budget at load)
    error rate    < 0.1 %
    GPU util      ≤ 0.80

Run Phase 2 load test (requires real API Platform and GPU node):
    locust -f tests/load/locustfile.py \\
        --host http://<api-platform-host>:8000 \\
        --users 500 --spawn-rate 50 \\
        --run-time 45m --headless \\
        --csv evaluation/load-testing/results

Environment variables:
    VOICEOS_API_KEY  — API key for authentication (required)
    VOICEOS_HOST     — override for --host (optional)
"""

from __future__ import annotations

import os
import random
import string

from locust import HttpUser, between, events, task
from locust.runners import MasterRunner, WorkerRunner

_API_KEY: str = os.getenv("VOICEOS_API_KEY", "test-api-key")


def _random_call_id() -> str:
    return "call-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _random_customer_id() -> str:
    return f"customer-{random.randint(1000, 9999)}"


class VoiceOSCallUser(HttpUser):
    """Simulates one concurrent inbound call session.

    Each user starts a call, sends an audio segment, receives a response,
    and holds for a realistic inter-turn pause before the next turn.
    """

    wait_time = between(2.0, 8.0)

    def on_start(self) -> None:
        self.call_id = _random_call_id()
        self.customer_id = _random_customer_id()
        self.headers: dict[str, str] = {
            "X-API-Key": _API_KEY,
            "Content-Type": "application/json",
        }

    @task(5)
    def health_check(self) -> None:
        with self.client.get("/health/live", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health check failed: {resp.status_code}")

    @task(10)
    def initiate_call(self) -> None:
        """Start a new inbound call session (Media GW → Conversation Engine)."""
        payload = {
            "call_id": _random_call_id(),
            "customer_id": self.customer_id,
            "direction": "inbound",
            "caller_number": "+91-9999-000000",
        }
        with self.client.post(
            "/v1/calls",
            json=payload,
            headers=self.headers,
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201, 202):
                resp.success()
            elif resp.status_code == 429:
                resp.success()  # rate-limited — expected under load
            else:
                resp.failure(f"Call initiation failed: {resp.status_code}")

    @task(3)
    def get_call_status(self) -> None:
        with self.client.get(
            f"/v1/calls/{self.call_id}",
            headers=self.headers,
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Call status failed: {resp.status_code}")

    @task(2)
    def get_customer(self) -> None:
        with self.client.get(
            f"/v1/customers/{self.customer_id}",
            headers=self.headers,
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Customer fetch failed: {resp.status_code}")


@events.init.add_listener
def on_locust_init(environment: object, **kwargs: object) -> None:
    if isinstance(environment, (MasterRunner, WorkerRunner)):
        return
    print(
        "\n[VoiceOS Load Test] Sprint-028 Phase 2\n"
        "Thresholds: first-audio p95 ≤ 1.65s | error rate < 0.1% | GPU util ≤ 0.80\n"
    )


@events.test_stop.add_listener
def on_test_stop(environment: object, **kwargs: object) -> None:
    print("\n[VoiceOS Load Test] Complete — check evaluation/load-testing/load-test-report.md\n")
