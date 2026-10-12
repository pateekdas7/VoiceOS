#!/usr/bin/env python3
"""
VoiceOS CRM Sync Worker — runs as a systemd one-shot service.

Drains the crm_sync_log table of PENDING/FAILED records and pushes them to
LeadSquared. Designed to run every 5 minutes via systemd timer.

Exit codes:
  0 — success (all processed or nothing to do)
  1 — partial failure (some records could not be synced after retries)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Load .env before importing anything that needs env vars
try:
    env_path = Path('/opt/voiceos/.env')
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
except Exception:
    pass

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger('voiceos.crm_sync_worker')

MAX_SYNC_ATTEMPTS = 5
BATCH_SIZE        = 50   # records per run


async def build_pool() -> asyncpg.Pool:
    dsn = os.environ.get('POSTGRES_DSN')
    if dsn:
        return await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    return await asyncpg.create_pool(
        host=os.environ.get('POSTGRES_HOST', '127.0.0.1'),
        port=int(os.environ.get('POSTGRES_PORT', '5432')),
        database=os.environ.get('POSTGRES_DB', 'voiceos'),
        user=os.environ.get('POSTGRES_USER', 'voiceos'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
        min_size=1, max_size=3,
    )


async def process_pending(pool: asyncpg.Pool) -> tuple[int, int]:
    """
    Process up to BATCH_SIZE pending/retryable sync tasks.
    Returns (processed, errors).
    """
    # Import here to avoid circular imports in tests
    from src.services.crm.sync_service import CRMSyncService

    svc = CRMSyncService(pool)

    # Claim a batch — update to IN_PROGRESS atomically to prevent double-processing
    rows = await pool.fetch(
        """UPDATE crm_sync_log
           SET sync_status='IN_PROGRESS', updated_at=NOW()
           WHERE sync_id IN (
             SELECT sync_id FROM crm_sync_log
             WHERE sync_status IN ('PENDING')
               AND attempt_count < $1
             ORDER BY created_at ASC
             LIMIT $2
             FOR UPDATE SKIP LOCKED
           )
           RETURNING sync_id, tenant_id::text, entity_type, entity_id::text""",
        MAX_SYNC_ATTEMPTS, BATCH_SIZE,
    )

    if not rows:
        logger.info('CRMSyncWorker: nothing to process')
        return 0, 0

    logger.info('CRMSyncWorker: processing %d pending sync tasks', len(rows))
    processed = errors = 0

    for row in rows:
        tenant_id   = row['tenant_id']
        entity_type = row['entity_type']
        entity_id   = row['entity_id']

        try:
            if entity_type == 'disposition':
                ok = await svc.sync_call_disposition(entity_id, tenant_id)
            elif entity_type == 'ptp':
                ok = await svc.sync_ptp(entity_id, tenant_id)
            elif entity_type == 'settlement':
                ok = await svc.sync_settlement(entity_id, tenant_id)
            else:
                logger.warning('CRMSyncWorker: unknown entity_type=%s — skipping', entity_type)
                await pool.execute(
                    "UPDATE crm_sync_log SET sync_status='SKIPPED', updated_at=NOW() "
                    "WHERE tenant_id=$1 AND entity_type=$2 AND entity_id=$3",
                    row['tenant_id'], entity_type, row['entity_id'],
                )
                continue

            if ok:
                processed += 1
            else:
                errors += 1
        except Exception as e:
            errors += 1
            logger.error('CRMSyncWorker: unhandled error entity=%s/%s: %s',
                         entity_type, entity_id, e)
            await pool.execute(
                """UPDATE crm_sync_log
                   SET sync_status=CASE WHEN attempt_count+1>=$1 THEN 'FAILED' ELSE 'PENDING' END,
                       attempt_count=attempt_count+1,
                       last_error=$5, updated_at=NOW()
                   WHERE tenant_id=$2 AND entity_type=$3 AND entity_id=$4""",
                MAX_SYNC_ATTEMPTS, row['tenant_id'], entity_type, row['entity_id'], str(e),
            )

    return processed, errors


async def main() -> int:
    logger.info('VoiceOS CRM Sync Worker starting')
    pool = await build_pool()
    try:
        processed, errors = await process_pending(pool)
        logger.info('CRMSyncWorker done: processed=%d errors=%d', processed, errors)
        return 1 if errors > 0 and processed == 0 else 0
    finally:
        await pool.close()


if __name__ == '__main__':
    sys.path.insert(0, '/opt/voiceos/app')
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
