from __future__ import annotations

from pathlib import Path

import pytest

from src.services.media_gateway.recording_lifecycle import FilesystemRecordingStorage, RecordingLifecycleManager


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None

    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(sql.split()), params))
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO telephony_recordings"):
            self.row = ("rid-1",)
        elif s.startswith("UPDATE telephony_recordings") and "RETURNING recording_id" in s:
            self.row = ("rid-1",)
        elif s.startswith("SELECT recording_id,state FROM telephony_recordings"):
            self.row = ("rid-1", "RETAINED")
        elif s.startswith("SELECT recording_id,tenant_id,object_key,state"):
            self.row = ("rid-1", "tenant-a", "tenant-a/rid-1/C1.zip", "RETAINED")
        elif s.startswith("INSERT INTO telephony_recording_events"):
            event_id = params[2]
            self.row = ("event-1",) if event_id not in self.conn.events else None
            self.conn.events.add(event_id)
        elif s.startswith("SELECT recording_id,object_key"):
            self.row = []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.row or []


class FakeConn:
    def __init__(self):
        self.sql = []
        self.events = set()
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def test_valid_recording_lifecycle(tmp_path: Path):
    conn = FakeConn()
    storage = FilesystemRecordingStorage(str(tmp_path / "objects"))
    manager = RecordingLifecycleManager(conn, storage, retention_days=7)
    manager.start(tenant_id="tenant-a", call_sid="C1")
    artifact = tmp_path / "C1_events.jsonl"
    artifact.write_text('{"event":"call_start"}\n', encoding="utf-8")
    manager.finalize(tenant_id="tenant-a", call_sid="C1", artifacts=[artifact])
    assert list((tmp_path / "objects" / "tenant-a").rglob("*.zip"))


def test_unknown_recording_access_is_denied():
    conn = FakeConn()
    with pytest.raises(PermissionError):
        RecordingLifecycleManager(conn, FilesystemRecordingStorage("/tmp/voiceos-test"), 7).authorize_access(
            recording_id="missing", tenant_id="tenant-a"
        )


def test_duplicate_provider_event_is_idempotent():
    conn = FakeConn()
    manager = RecordingLifecycleManager(conn, FilesystemRecordingStorage("/tmp/voiceos-test"), 7)
    assert manager.process_provider_event(
        tenant_id="tenant-a", provider="twilio", provider_event_id="evt-1",
        event_type="completed", provider_recording_id="RE1", payload={"CallSid": "C1"}
    ) is True
    assert manager.process_provider_event(
        tenant_id="tenant-a", provider="twilio", provider_event_id="evt-1",
        event_type="completed", provider_recording_id="RE1", payload={"CallSid": "C1"}
    ) is False


def test_filesystem_storage_blocks_path_escape(tmp_path: Path):
    storage = FilesystemRecordingStorage(str(tmp_path))
    with pytest.raises(ValueError):
        storage._path("../other")
