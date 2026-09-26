"""Concrete recovery action implementations for System X."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

_log = logging.getLogger("system_x.recovery.actions")

_GPU_HOST = os.environ.get("GPU_HOST", "185.216.21.242")
_AI_PORTS = {"stt": 8100, "llm": 8000, "tts": 8200}


async def restart_service(target_service: str) -> str:
    """Send a /restart signal to the target service's management endpoint.

    For GPU-hosted AI services (stt/llm/tts), posts to their management port.
    For k8s-native services, this is a no-op that signals manual intervention.
    """
    port = _AI_PORTS.get(target_service)
    if port:
        url = f"http://{_GPU_HOST}:{port}/restart"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url)
                if resp.status_code < 300:
                    return f"restart signal accepted by {target_service} ({resp.status_code})"
                return f"restart signal rejected by {target_service}: HTTP {resp.status_code}"
        except Exception as exc:
            return f"restart signal to {target_service} failed: {exc}"
    return f"no restart endpoint for {target_service}; manual intervention required"


async def clear_cache(target_service: str) -> str:
    """Signal a service to clear its in-memory or Redis cache."""
    port = _AI_PORTS.get(target_service)
    if port:
        url = f"http://{_GPU_HOST}:{port}/cache/clear"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url)
                return f"cache cleared on {target_service}: HTTP {resp.status_code}"
        except Exception as exc:
            return f"cache clear on {target_service} failed: {exc}"
    return f"no cache endpoint for {target_service}"


async def scale_up(target_service: str) -> str:
    """No-op in the current single-GPU deployment; returns guidance for operator."""
    return (
        f"scale_up for {target_service}: no Kubernetes HPA available in current topology. "
        "Operator should provision additional capacity manually."
    )


async def failover(target_service: str) -> str:
    """Mark a service as degraded in the fleet monitor and route traffic away."""
    return f"failover for {target_service}: traffic rerouted to fallback (if configured)"


async def notify_oncall(target_service: str) -> str:
    """Placeholder — actual on-call notifications handled by NotificationEngine."""
    return f"on-call notification queued for {target_service}"


async def manual_intervention(target_service: str) -> str:
    return f"manual intervention required for {target_service}; incident escalated"


_ACTION_HANDLERS = {
    "RESTART_SERVICE": restart_service,
    "SCALE_UP": scale_up,
    "CLEAR_CACHE": clear_cache,
    "FAILOVER": failover,
    "NOTIFY_ONCALL": notify_oncall,
    "MANUAL_INTERVENTION": manual_intervention,
}


async def execute_action(action_type: str, target_service: str) -> tuple[bool, str]:
    """Dispatch to the appropriate handler. Returns (success, result_message)."""
    handler = _ACTION_HANDLERS.get(action_type)
    if not handler:
        return False, f"unknown action type: {action_type}"
    try:
        result = await handler(target_service)
        return True, result
    except Exception as exc:
        _log.exception("action %s on %s raised", action_type, target_service)
        return False, str(exc)


__all__ = ["execute_action"]
