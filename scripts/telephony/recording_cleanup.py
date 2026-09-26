#!/usr/bin/env python3
"""W2 recording-retention worker.

Run from cron/systemd on the CPU node. It deletes only recordings whose
retention_until has passed; storage failures remain durable as delete_attempts
and last_error so the next invocation retries them.
"""
from __future__ import annotations

import os
import psycopg2

from src.services.media_gateway.recording_lifecycle import RecordingLifecycleManager, build_recording_storage

def main() -> int:
    dsn=os.environ.get("POSTGRES_DSN")
    conn=psycopg2.connect(dsn) if dsn else psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST","127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT","5432")),
        dbname=os.environ.get("POSTGRES_DB","voiceos"),
        user=os.environ.get("POSTGRES_USER","voiceos"),
        password=os.environ["POSTGRES_PASSWORD"],
    )
    storage=build_recording_storage()
    manager=RecordingLifecycleManager(conn,storage,int(os.environ.get("RECORDING_RETENTION_DAYS","30")))
    rows=conn.cursor()
    rows.execute("SELECT tenant_id FROM tenants WHERE status='ACTIVE'")
    total=0
    for (tenant_id,) in rows.fetchall():
        total += manager.delete_expired(tenant_id=str(tenant_id), limit=100)
    print({"deleted": total})
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
