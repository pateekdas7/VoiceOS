"""Durable, tenant-scoped recording lifecycle for W2.

The existing CallRecorder remains the capture mechanism. This module owns the
production boundary: durable metadata, private object storage, retention,
deletion, access authorization, and idempotent provider events.

Storage is provider-neutral. The intended production target is S3-compatible
object storage; filesystem storage remains available for controlled local
runs/tests and is never exposed as a public URL.
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


class RecordingStorage(Protocol):
    def put(self, key: str, source: Path, content_type: str) -> int: ...
    def delete(self, key: str) -> None: ...
    def signed_url(self, key: str, expires_seconds: int) -> str | None: ...


class FilesystemRecordingStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError("recording object key escapes storage root")
        return candidate

    def put(self, key: str, source: Path, content_type: str) -> int:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target.stat().st_size

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def signed_url(self, key: str, expires_seconds: int) -> str | None:
        return None


class S3RecordingStorage:
    def __init__(self, bucket: str, region: str, prefix: str = "", endpoint_url: str = "") -> None:
        if not bucket:
            raise ValueError("RECORDING_S3_BUCKET is required for S3 recording storage")
        import boto3
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", region_name=region or None, endpoint_url=endpoint_url or None)

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, source: Path, content_type: str) -> int:
        size = source.stat().st_size
        with source.open("rb") as fh:
            self.client.upload_fileobj(
                fh, self.bucket, self._key(key),
                ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
            )
        return size

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def signed_url(self, key: str, expires_seconds: int) -> str | None:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self._key(key)},
            ExpiresIn=max(1, min(int(expires_seconds), 3600)),
        )


@dataclass(frozen=True)
class RecordingAccess:
    recording_id: str
    tenant_id: str
    object_key: str
    expires_at: datetime
    url: str | None


class RecordingLifecycleManager:
    def __init__(self, conn: Any, storage: RecordingStorage, retention_days: int = 30) -> None:
        self.conn = conn
        self.storage = storage
        self.retention_days = max(1, int(retention_days))

    def start(self, *, tenant_id: str, call_sid: str) -> str:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO telephony_recordings (tenant_id, call_sid, state, retention_until)
               VALUES (%s,%s,'CREATED',NOW()+(%s || ' days')::interval)
               ON CONFLICT (tenant_id, call_sid) DO UPDATE SET updated_at=NOW()
               RETURNING recording_id""",
            (tenant_id, call_sid, self.retention_days),
        )
        recording_id = str(cur.fetchone()[0])
        self.conn.commit()
        return recording_id

    def _bundle(self, call_sid: str, artifacts: list[Path], work_dir: Path) -> Path:
        bundle = work_dir / f"{call_sid}.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for artifact in artifacts:
                if artifact.exists() and artifact.is_file():
                    zf.write(artifact, artifact.name)
        return bundle

    def finalize(self, *, tenant_id: str, call_sid: str, artifacts: list[Path]) -> str:
        cur = self.conn.cursor()
        cur.execute(
            """UPDATE telephony_recordings
               SET state='PROCESSING', updated_at=NOW()
               WHERE tenant_id=%s AND call_sid=%s AND state IN ('CREATED','FAILED')
               RETURNING recording_id""",
            (tenant_id, call_sid),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                "SELECT recording_id,state FROM telephony_recordings WHERE tenant_id=%s AND call_sid=%s",
                (tenant_id, call_sid),
            )
            existing = cur.fetchone()
            self.conn.rollback()
            if not existing:
                raise LookupError("unknown recording")
            if existing[1] in ("AVAILABLE", "RETAINED", "DELETED"):
                return str(existing[0])
            raise RuntimeError("recording is already being processed")
        recording_id = str(row[0])
        self.conn.commit()
        try:
            work_dir = Path(artifacts[0]).parent if artifacts else Path(os.environ.get("TMPDIR", "/tmp"))
            bundle = self._bundle(call_sid, artifacts, work_dir)
            key = f"{tenant_id}/{recording_id}/{call_sid}.zip"
            size = self.storage.put(key, bundle, "application/zip")
            cur = self.conn.cursor()
            cur.execute(
                """UPDATE telephony_recordings r
                   SET campaign_id=ca.campaign_id, lead_id=ca.lead_id, call_attempt_id=ca.attempt_id
                   FROM call_attempts ca
                   WHERE r.recording_id=%s AND r.tenant_id=%s AND ca.call_sid=%s
                     AND ca.tenant_id=%s""",
                (recording_id, tenant_id, call_sid, tenant_id),
            )
            cur.execute(
                """UPDATE telephony_recordings
                   SET state='RETAINED', object_key=%s, content_type='application/zip',
                       byte_size=%s, completed_at=NOW(), updated_at=NOW(), last_error=NULL
                   WHERE recording_id=%s AND tenant_id=%s""",
                (key, size, recording_id, tenant_id),
            )
            self.conn.commit()
            bundle.unlink(missing_ok=True)
            return recording_id
        except Exception as exc:
            self.conn.rollback()
            cur = self.conn.cursor()
            cur.execute(
                """UPDATE telephony_recordings SET state='FAILED', last_error=%s,
                       updated_at=NOW() WHERE recording_id=%s AND tenant_id=%s""",
                (str(exc)[:2000], recording_id, tenant_id),
            )
            self.conn.commit()
            raise

    def authorize_access(self, *, recording_id: str, tenant_id: str, expires_seconds: int = 300) -> RecordingAccess:
        cur = self.conn.cursor()
        cur.execute(
            """SELECT recording_id,tenant_id,object_key,state
               FROM telephony_recordings
               WHERE recording_id=%s AND tenant_id=%s AND state IN ('AVAILABLE','RETAINED')""",
            (recording_id, tenant_id),
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError("recording not found")
        object_key = row[2]
        if not object_key:
            raise LookupError("recording object unavailable")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, min(expires_seconds, 3600)))
        return RecordingAccess(str(row[0]), str(row[1]), object_key, expires_at,
                               self.storage.signed_url(object_key, expires_seconds))

    def process_provider_event(self, *, tenant_id: str, provider: str, provider_event_id: str,
                               event_type: str, provider_recording_id: str | None,
                               payload: dict[str, Any]) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO telephony_recording_events
               (tenant_id,provider,provider_event_id,event_type,payload)
               VALUES (%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT (provider,provider_event_id) DO NOTHING
               RETURNING event_id""",
            (tenant_id, provider, provider_event_id, event_type, json.dumps(payload)),
        )
        first = cur.fetchone() is not None
        if first and provider_recording_id:
            cur.execute(
                """UPDATE telephony_recordings
                   SET provider_recording_id=%s, updated_at=NOW()
                   WHERE tenant_id=%s AND provider=%s AND call_sid=%s""",
                (provider_recording_id, tenant_id, provider, payload.get("CallSid", "")),
            )
        self.conn.commit()
        return first

    def delete_expired(self, *, tenant_id: str, limit: int = 100) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """SELECT recording_id,object_key FROM telephony_recordings
               WHERE tenant_id=%s AND state IN ('AVAILABLE','RETAINED')
                 AND retention_until <= NOW()
               ORDER BY retention_until ASC LIMIT %s FOR UPDATE SKIP LOCKED""",
            (tenant_id, limit),
        )
        rows = cur.fetchall()
        deleted = 0
        for recording_id, key in rows:
            try:
                self.storage.delete(key)
                cur.execute(
                    """UPDATE telephony_recordings
                       SET state='DELETED', deleted_at=NOW(), updated_at=NOW(), last_error=NULL
                       WHERE recording_id=%s AND tenant_id=%s""",
                    (recording_id, tenant_id),
                )
                deleted += 1
            except Exception as exc:
                cur.execute(
                    """UPDATE telephony_recordings
                       SET delete_attempts=delete_attempts+1,last_error=%s,updated_at=NOW()
                       WHERE recording_id=%s AND tenant_id=%s""",
                    (str(exc)[:2000], recording_id, tenant_id),
                )
        self.conn.commit()
        return deleted


def build_recording_storage() -> RecordingStorage:
    backend = os.environ.get("RECORDING_STORAGE_BACKEND", "filesystem").lower()
    if os.environ.get("NODE_ENV") == "production" and backend != "s3":
        raise RuntimeError("production recording requires RECORDING_STORAGE_BACKEND=s3")
    if backend == "s3":
        return S3RecordingStorage(
            bucket=os.environ.get("RECORDING_S3_BUCKET", ""),
            region=os.environ.get("RECORDING_S3_REGION", ""),
            prefix=os.environ.get("RECORDING_S3_PREFIX", "voiceos/recordings"),
            endpoint_url=os.environ.get("RECORDING_S3_ENDPOINT", ""),
        )
    if backend == "filesystem":
        return FilesystemRecordingStorage(os.environ.get("CALL_RECORDING_DIR", "/var/lib/voiceos/recordings"))
    raise ValueError(f"unsupported recording storage backend: {backend}")
