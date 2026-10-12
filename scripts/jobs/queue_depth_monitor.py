#!/usr/bin/env python3
"""W11 Queue-Depth Monitor — watches Redis dialer queues and logs warnings.

Runs continuously (15-second poll). Emits structured log lines that
Prometheus/Loki can scrape. When any tenant queue exceeds WARN_THRESHOLD,
logs a WARNING so the ops team can manually scale workers.

Usage:
    python scripts/jobs/queue_depth_monitor.py [--once]
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time

import redis as redis_lib

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    stream=sys.stdout,
)
log = logging.getLogger('voiceos.queue_depth_monitor')

WARN_THRESHOLD  = int(os.environ.get('QUEUE_DEPTH_WARN', '50'))
CRIT_THRESHOLD  = int(os.environ.get('QUEUE_DEPTH_CRIT', '200'))
POLL_INTERVAL_S = int(os.environ.get('QUEUE_MONITOR_INTERVAL_S', '15'))
PRIORITIES      = ('high', 'normal', 'low')


def _redis() -> redis_lib.Redis:
    return redis_lib.Redis(
        host=os.environ.get('REDIS_HOST', '127.0.0.1'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        password=os.environ.get('REDIS_PASSWORD') or None,
        decode_responses=True,
    )


def _scan_queues(r: redis_lib.Redis) -> dict[str, int]:
    """Return {tenant_id: total_pending} for all discovered tenant queues."""
    totals: dict[str, int] = {}
    for pri in PRIORITIES:
        pattern = f'voiceos:pending_calls:*:{pri}'
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=200)
            for key in keys:
                # key = voiceos:pending_calls:<tid>:<pri>
                parts = key.split(':')
                if len(parts) >= 4:
                    tid = parts[2]
                    depth = r.llen(key)
                    totals[tid] = totals.get(tid, 0) + depth
            if cursor == 0:
                break

    # Also check retry/callback queues
    for qtype in ('retry_calls', 'callback_calls'):
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=f'voiceos:{qtype}:*', count=200)
            for key in keys:
                parts = key.split(':')
                if len(parts) >= 3:
                    tid = parts[2]
                    depth = r.zcard(key)
                    totals[tid] = totals.get(tid, 0) + depth
            if cursor == 0:
                break
    return totals


def _system_active(r: redis_lib.Redis) -> int:
    try:
        return int(r.get('voiceos:active_calls:system') or 0)
    except Exception:
        return 0


def poll_once(r: redis_lib.Redis) -> dict:
    totals   = _scan_queues(r)
    sys_active = _system_active(r)
    total_pending = sum(totals.values())
    metrics = {
        'total_pending': total_pending,
        'system_active_calls': sys_active,
        'tenants': totals,
    }
    level = logging.INFO
    if total_pending >= CRIT_THRESHOLD:
        level = logging.CRITICAL
    elif total_pending >= WARN_THRESHOLD:
        level = logging.WARNING
    log.log(level, 'queue_depth event=poll pending=%d active=%d tenants=%d',
            total_pending, sys_active, len(totals))
    if level >= logging.WARNING:
        for tid, depth in sorted(totals.items(), key=lambda x: -x[1])[:5]:
            log.log(level, '  tenant=%s depth=%d', tid, depth)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Poll once and exit')
    args = parser.parse_args()
    r = _redis()
    if args.once:
        result = poll_once(r)
        print(json.dumps(result, indent=2))
        return
    log.info('queue_depth_monitor starting poll_interval=%ds warn=%d crit=%d',
             POLL_INTERVAL_S, WARN_THRESHOLD, CRIT_THRESHOLD)
    while True:
        try:
            poll_once(r)
        except Exception as e:
            log.error('poll_error: %s', e)
        time.sleep(POLL_INTERVAL_S)


if __name__ == '__main__':
    main()
