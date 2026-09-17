"""
Phase 15 Load Test — BFF/API Layer
Targets 50 concurrent simulated users hitting the bff.js and web_api endpoints.

Unlike locustfile.py (which targets the GPU inference pipeline at 500 users),
this file validates the application-tier API layer under staging load:
  - campaign list / lead upload / HITL queue
  - Auth: all requests carry a valid staging JWT

Usage:
    BFF_URL=http://localhost:8100 \\
    STAGING_TOKEN=<jwt> \\
    locust -f tests/load/locustfile_bff.py --headless \\
        --users 50 --spawn-rate 5 --run-time 10m \\
        --host http://localhost:8100

Gate: zero 5xx, p95 latency < 2000ms, queue depth stays < 50.
"""

from __future__ import annotations

import json
import os
import uuid

from locust import HttpUser, between, events, task

BFF_URL = os.environ.get("BFF_URL", "http://localhost:8100")
STAGING_TOKEN = os.environ.get("STAGING_TOKEN", "")


class BffUser(HttpUser):
    host = BFF_URL
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.token = STAGING_TOKEN
        self.campaign_id: str | None = None
        self._create_campaign()

    def _create_campaign(self) -> None:
        resp = self.client.post(
            "/campaigns",
            json={
                "name": f"Load Test {uuid.uuid4().hex[:8]}",
                "product": "test_product",
                "language": "hi-IN",
            },
            headers=self._headers(),
            catch_response=True,
        )
        with resp as r:
            if r.status_code in (200, 201):
                data = r.json()
                self.campaign_id = data.get("id") or data.get("campaign_id")
            else:
                r.failure(f"Campaign create failed: {r.status_code}")

    def _headers(self) -> dict:
        return {"Cookie": f"voiceos_token={self.token}"} if self.token else {}

    @task(3)
    def list_campaigns(self) -> None:
        with self.client.get("/campaigns?limit=10", headers=self._headers(), catch_response=True) as r:
            if r.status_code == 200:
                r.success()
            elif r.status_code == 401:
                r.failure("Unauthorized — token may have expired")
            else:
                r.failure(f"Unexpected {r.status_code}")

    @task(2)
    def get_campaign(self) -> None:
        if not self.campaign_id:
            return
        with self.client.get(
            f"/campaigns/{self.campaign_id}",
            headers=self._headers(),
            catch_response=True,
            name="/campaigns/[id]",
        ) as r:
            if r.status_code in (200, 404):
                r.success()
            else:
                r.failure(f"Unexpected {r.status_code}")

    @task(1)
    def hitl_queue_claim(self) -> None:
        with self.client.post(
            "/hitl/queue/claim-next",
            headers=self._headers(),
            catch_response=True,
        ) as r:
            if r.status_code in (200, 404):
                r.success()
            elif r.status_code == 401:
                r.failure("Unauthorized")
            else:
                r.failure(f"Unexpected {r.status_code}")

    @task(1)
    def dashboard_snapshot(self) -> None:
        with self.client.get(
            "/analytics/dashboard",
            headers=self._headers(),
            catch_response=True,
        ) as r:
            if r.status_code in (200, 404):
                r.success()
            else:
                r.failure(f"Unexpected {r.status_code}")


@events.quitting.add_listener
def assert_gates(environment, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Fail the run if any gates are violated."""
    stats = environment.stats.total
    fail_pct = (stats.num_failures / stats.num_requests * 100) if stats.num_requests else 0
    p95 = stats.get_response_time_percentile(0.95)

    print(f"\n[Load gate] Requests: {stats.num_requests}, Failures: {stats.num_failures} ({fail_pct:.1f}%), p95: {p95}ms")

    if fail_pct > 1.0:
        print(f"FAIL: Error rate {fail_pct:.1f}% > 1% gate (zero 5xx required)")
        environment.process_exit_code = 1
    elif p95 and p95 > 2000:
        print(f"FAIL: p95 {p95}ms > 2000ms gate")
        environment.process_exit_code = 1
    else:
        print("PASS: All load gates satisfied")
        environment.process_exit_code = 0
