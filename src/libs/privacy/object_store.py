"""LocalDiskObjectStore — real (non-fake) object store for audio recordings.

No S3/GCS account exists for this project. Mirroring the self-managed
infrastructure precedent set elsewhere (Sprint-018's self-managed mTLS CA),
this backs the "delete audio recordings from object storage" erasure step
(V4 Ch9 §9.11) with real file I/O against the CPU node's
``/opt/voiceos/recordings`` directory rather than a cloud object store.

``tests/fixtures/fake_object_store.py::FakeObjectStore`` is the Phase 1 test double.

Architecture: V4 Ch9 §9.11 (erasure sequence: "delete audio recordings from
object store").
"""

from __future__ import annotations

from pathlib import Path


class LocalDiskObjectStore:
    """Disk-backed object store — one file per key under ``base_dir``."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        # Reject path traversal — key is a caller-controlled string (e.g. a
        # call_id), never trusted to double as a filesystem path unchecked.
        safe_key = key.replace("/", "_").replace("\\", "_").replace("..", "_")
        return self._base_dir / safe_key

    def put(self, key: str, data: bytes) -> None:
        self._path_for(key).write_bytes(data)

    def exists(self, key: str) -> bool:
        return self._path_for(key).exists()

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        path.unlink(missing_ok=True)
