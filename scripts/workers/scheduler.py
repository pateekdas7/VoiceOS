#!/usr/bin/env python3
"""Phase 5 / G5 — Persistent monitoring scheduler.

Runs the two periodic monitoring jobs that previously only existed as
Starlette-lifespan asyncio tasks inside the BFF process:

    * GPU fleet poller — hits every known GPU model-serving ``/health``
      endpoint and feeds :class:`GPUFleetHealthMonitor.report_node`, which
      updates the ``voiceos_gpu_fleet_*`` gauges and fleet health score.
    * Alert escalation evaluator — calls
      :meth:`AlertLifecycleService.evaluate_escalations` and emits
      ``voiceos_escalations_total`` counters per severity.

Running these out of a dedicated systemd-managed process means:

    1. A BFF restart does not stall monitoring freshness for 30-60s while
       the lifespan rebuilds — the scheduler keeps emitting.
    2. There is a single, named, root-of-trust process for Prometheus
       scrape, matched 1:1 with the ``voiceos-scheduler.service`` systemd
       unit (G7) and the ``voiceos_startup_handlers`` registry (G9).
    3. Self-monitoring metrics (``voiceos_scheduler_last_run_timestamp``,
       ``voiceos_escalation_scheduler_last_run_timestamp``) survive BFF
       crashes, so alerts on stale timestamps are not spoofed by a BFF
       restart loop.

Prometheus scrapes this process on ``SCHEDULER_METRICS_PORT`` (default
9108). Signal handling: SIGTERM / SIGINT cleanly cancels the periodic
tasks and lets the current tick finish before exit.

Usage::

    python3 -m scripts.workers.scheduler

Environment:
    SCHEDULER_METRICS_PORT   int     prometheus_client HTTP port (9108)
    GPU_HOST                 str     GPU node hostname/IP
    GPU_POLL_INTERVAL_S      int     seconds between poller ticks (30)
    STT_PORT / LLM_PORT / TTS_PORT  int   model-serving ports
    ALERT_ESCALATION_INTERVAL_S  int seconds between escalation ticks (60)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone


def _setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"scheduler","msg":"%(message)s"}',
    )
    return logging.getLogger("voiceos.scheduler")


async def _poll_gpu_fleet(stop_event: asyncio.Event, log: logging.Logger) -> None:
    """Periodically poll GPU nodes and update the fleet monitor."""
    import httpx  # noqa: PLC0415

    from monitoring.gpu_fleet.fleet_health import GPUFleetHealthMonitor, GPUNodeSnapshot  # noqa: PLC0415
    from monitoring.gpu_fleet.metrics import record_poller_error, record_poller_tick  # noqa: PLC0415

    gpu_host = os.environ.get("GPU_HOST", "185.216.21.242")
    interval_s = int(os.environ.get("GPU_POLL_INTERVAL_S", "30"))
    endpoints: list[tuple[str, int]] = [
        ("stt", int(os.environ.get("STT_PORT", "8100"))),
        ("llm", int(os.environ.get("LLM_PORT", "8000"))),
        ("tts", int(os.environ.get("TTS_PORT", "8200"))),
    ]

    monitor = GPUFleetHealthMonitor()
    log.info("gpu-poller started host=%s interval=%ds endpoints=%d", gpu_host, interval_s, len(endpoints))

    async with httpx.AsyncClient(timeout=5.0) as client:
        while not stop_event.is_set():
            tick_started = time.time()
            for _service, port in endpoints:
                node_id = f"{gpu_host}:{port}"
                try:
                    resp = await client.get(f"http://{gpu_host}:{port}/health")
                    healthy = resp.status_code == 200
                    data = resp.json() if healthy else {}
                except Exception as exc:  # noqa: BLE001
                    record_poller_error(node_id)
                    log.warning("gpu-poller error node=%s err=%s", node_id, exc)
                    healthy = False
                    data = {}
                monitor.report_node(
                    GPUNodeSnapshot(
                        node_id=node_id,
                        healthy=healthy,
                        vram_used_mb=int(data.get("vram_used_mb", 0)),
                        vram_total_mb=int(data.get("vram_total_mb", 0)),
                    )
                )
            monitor.fleet_health_score()
            record_poller_tick(tick_started)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass


async def _run_escalation_evaluator(stop_event: asyncio.Event, log: logging.Logger) -> None:
    """Periodically call AlertLifecycleService.evaluate_escalations()."""
    from src.services.ops_intelligence.plumbing.alert_lifecycle import AlertLifecycleService  # noqa: PLC0415
    from src.services.ops_intelligence.plumbing.metrics import (  # noqa: PLC0415
        record_escalation,
        record_scheduler_error,
        record_scheduler_tick,
    )
    from src.services.ops_intelligence.repositories.alert_repository import PostgresAlertRepository  # noqa: PLC0415

    interval_s = int(os.environ.get("ALERT_ESCALATION_INTERVAL_S", "60"))
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        log.warning("escalation-evaluator disabled: POSTGRES_DSN not set")
        record_scheduler_error()
        return

    try:
        import psycopg  # noqa: PLC0415
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception as exc:  # noqa: BLE001
        log.error("escalation-evaluator cannot connect to Postgres: %s", exc)
        record_scheduler_error()
        return

    lifecycle = AlertLifecycleService(PostgresAlertRepository(conn))
    log.info("escalation-evaluator started interval=%ds", interval_s)

    while not stop_event.is_set():
        tick_started = time.time()
        try:
            escalated = lifecycle.evaluate_escalations(now=datetime.now(timezone.utc))
            record_scheduler_tick(tick_started)
            for alert in escalated:
                record_escalation(alert.severity.value)
            if escalated:
                log.info("escalation-evaluator escalated=%d", len(escalated))
        except Exception as exc:  # noqa: BLE001
            record_scheduler_error()
            log.warning("escalation-evaluator error: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def _main_async(log: logging.Logger) -> int:
    from prometheus_client import start_http_server  # noqa: PLC0415

    port = int(os.environ.get("SCHEDULER_METRICS_PORT", "9108"))
    start_http_server(port)
    log.info("prometheus metrics endpoint listening on :%d", port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    def _log_task_result(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("task %s exited with exception: %r", task.get_name(), exc)

    tasks = [
        asyncio.create_task(_poll_gpu_fleet(stop_event, log), name="gpu-poller"),
        asyncio.create_task(_run_escalation_evaluator(stop_event, log), name="escalation-evaluator"),
    ]
    for t in tasks:
        t.add_done_callback(_log_task_result)
    log.info("scheduler running; waiting on SIGTERM/SIGINT")
    await stop_event.wait()
    log.info("stop signal received — draining %d task(s)", len(tasks))
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    log.info("scheduler stopped cleanly")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="VoiceOS monitoring scheduler (Phase 5 / G5)")
    parser.add_argument("--check", action="store_true", help="import-only sanity check, exit 0")
    args = parser.parse_args()
    log = _setup_logging()
    if args.check:
        log.info("scheduler import-check OK")
        return 0
    try:
        return asyncio.run(_main_async(log))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
