"""Data Platform ETL consumer — drains Redis Streams into raw_events table."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import asyncpg

_log = logging.getLogger("voiceos.data_platform.consumer")

STREAM_NAME    = "voiceos-events"
CONSUMER_GROUP = "data-platform-etl"
CONSUMER_NAME  = "etl-worker-0"
BATCH_SIZE     = 200
POLL_INTERVAL  = 5.0


class ETLConsumer:
    def __init__(self, redis: Any, pg_pool: asyncpg.Pool) -> None:
        self._redis = redis
        self._pool  = pg_pool

    def _ensure_group(self) -> None:
        try:
            self._redis.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def _save_checkpoint(self, entry_id: str) -> None:
        await self._pool.execute(
            """INSERT INTO event_consumer_checkpoint (consumer_group, last_entry_id, updated_at)
               VALUES ($1, $2, NOW())
               ON CONFLICT (consumer_group) DO UPDATE
               SET last_entry_id=EXCLUDED.last_entry_id, updated_at=NOW()""",
            CONSUMER_GROUP, entry_id,
        )

    async def _ingest_batch(self, entries: list) -> int:
        rows = []
        for entry_id, fields in entries:
            raw = fields.get(b"envelope") or fields.get("envelope")
            if not raw:
                continue
            try:
                env = json.loads(raw if isinstance(raw, str) else raw.decode())
            except Exception as e:
                _log.warning("ETL: failed to parse envelope entry=%s: %s", entry_id, e)
                continue
            rows.append((
                env.get("event_id", ""),
                env.get("event_type", ""),
                int(env.get("version", 1)),
                env.get("tenant_id", ""),
                env.get("correlation_id", ""),
                env.get("causation_id"),
                env.get("trace_id", ""),
                env.get("occurred_at"),
                json.dumps(env.get("payload", {})),
            ))
        if not rows:
            return 0
        await self._pool.executemany(
            """INSERT INTO raw_events
                 (event_id, event_type, schema_version, tenant_id,
                  correlation_id, causation_id, trace_id, occurred_at, payload)
               VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, $8::timestamptz, $9::jsonb)
               ON CONFLICT (event_id, occurred_at) DO NOTHING""",
            rows,
        )
        return len(rows)

    async def run_once(self) -> int:
        self._ensure_group()
        raw = self._redis.xreadgroup(
            CONSUMER_GROUP, CONSUMER_NAME,
            {STREAM_NAME: ">"},
            count=BATCH_SIZE,
            block=0,
        )
        if not raw:
            return 0
        _, entries = raw[0]
        written = await self._ingest_batch(entries)
        entry_ids = [eid for eid, _ in entries]
        if entry_ids:
            self._redis.xack(STREAM_NAME, CONSUMER_GROUP, *entry_ids)
            last = entry_ids[-1]
            await self._save_checkpoint(last.decode() if isinstance(last, bytes) else str(last))
            _log.debug("ETL: ingested=%d acked=%d", written, len(entry_ids))
        return written

    async def run_forever(self) -> None:
        _log.info("ETL consumer started stream=%s group=%s", STREAM_NAME, CONSUMER_GROUP)
        while True:
            try:
                count = await self.run_once()
                if count == 0:
                    await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.error("ETL consumer error: %s", e)
                await asyncio.sleep(POLL_INTERVAL)
