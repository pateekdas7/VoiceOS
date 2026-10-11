"""VoiceOS Dialer Worker — long-running process that executes campaign dial loops.

Reads JSON commands from the Redis list ``voiceos:dialer:cmds`` (BLPOP, 5s
timeout) and dispatches them to DialerSessionManager:

    {"command": "start", "tenant_id": "...", "campaign_id": "...",
     "daily_start_hour": 9, "daily_end_hour": 21,
     "timezone_name": "Asia/Kolkata"}

    {"command": "stop", "campaign_id": "..."}

On SIGTERM the worker:
  1. Stops all running campaign sessions (DialerSessionManager.stop()).
  2. Waits up to 45 s for asyncio tasks to drain.
  3. Exits 0.

Usage:
    python scripts/jobs/run_dialer_worker.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys

logger = logging.getLogger("voiceos.dialer_worker")

_REDIS_CMD_KEY = "voiceos:dialer:cmds"
_BLPOP_TIMEOUT = 5          # seconds per pop attempt
_DRAIN_TIMEOUT = 45.0       # seconds to wait for sessions to stop on SIGTERM


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


async def _run(dialer_mgr: object, raw_redis: object) -> None:
    """Main event loop: pop commands from Redis and dispatch them."""
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()

    def _handle_sigterm() -> None:
        logger.info("SIGTERM received — initiating graceful shutdown")
        shutdown.set()

    loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)

    logger.info("Dialer worker started, watching %s", _REDIS_CMD_KEY)

    while not shutdown.is_set():
        try:
            result = await asyncio.to_thread(
                raw_redis.blpop, _REDIS_CMD_KEY, _BLPOP_TIMEOUT
            )
        except Exception as exc:
            logger.warning("Redis BLPOP error: %s", exc)
            await asyncio.sleep(1.0)
            continue

        if result is None:
            continue  # timeout, loop to check shutdown flag

        _, raw_msg = result
        try:
            msg = json.loads(raw_msg)
        except Exception:
            logger.warning("Malformed dialer command (not JSON): %r", raw_msg)
            continue

        command = msg.get("command", "")
        campaign_id = msg.get("campaign_id", "")

        if command == "start":
            tenant_id = msg.get("tenant_id", "")
            if not tenant_id or not campaign_id:
                logger.warning("start command missing tenant_id or campaign_id: %s", msg)
                continue
            try:
                await dialer_mgr.start(  # type: ignore[attr-defined]
                    tenant_id,
                    campaign_id,
                    daily_start_hour=int(msg.get("daily_start_hour", 9)),
                    daily_end_hour=int(msg.get("daily_end_hour", 21)),
                    timezone_name=msg.get("timezone_name", "Asia/Kolkata"),
                )
                logger.info("Campaign started tenant=%s campaign=%s", tenant_id, campaign_id)
            except Exception as exc:
                logger.error("Failed to start campaign %s: %s", campaign_id, exc)

        elif command == "stop":
            if not campaign_id:
                logger.warning("stop command missing campaign_id: %s", msg)
                continue
            try:
                await dialer_mgr.stop(campaign_id)  # type: ignore[attr-defined]
                logger.info("Campaign stop requested campaign=%s", campaign_id)
            except Exception as exc:
                logger.error("Failed to stop campaign %s: %s", campaign_id, exc)

        else:
            logger.warning("Unknown dialer command: %r", command)

    # Graceful shutdown: stop all running sessions
    logger.info("Draining all campaign sessions (max %.0fs)", _DRAIN_TIMEOUT)
    sessions = getattr(dialer_mgr, "_sessions", {})
    for cid in list(sessions):
        try:
            await dialer_mgr.stop(cid)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Error stopping campaign %s: %s", cid, exc)

    deadline = asyncio.get_event_loop().time() + _DRAIN_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        tasks = getattr(dialer_mgr, "_tasks", {})
        running = [t for t in tasks.values() if not t.done()]
        if not running:
            break
        logger.info("Waiting for %d campaign task(s) to finish...", len(running))
        await asyncio.sleep(1.0)

    logger.info("Dialer worker shut down cleanly")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from deployment.cpu.app import (  # type: ignore[import]
        build_postgres_connection,
        build_raw_redis_client,
        build_dialer_services,
    )

    conn = build_postgres_connection()
    raw_redis = build_raw_redis_client()
    dialer_mgr = build_dialer_services(conn, raw_redis)

    asyncio.run(_run(dialer_mgr, raw_redis))


if __name__ == "__main__":
    main()
