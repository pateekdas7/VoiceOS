"""In-memory object-store test double for right-to-erasure unit tests.

Simulates the audio-recordings object store referenced by
``DataErasureJob`` step 4 ("delete audio recordings from object storage")
without real disk/S3 I/O. Real Phase 2 validation uses
``src.libs.privacy.object_store.LocalDiskObjectStore`` against the CPU
node's ``/opt/voiceos/recordings`` directory.

Architecture: V4 Ch9 (Privacy Architecture); V6 Ch9 (Testing Standards).
"""

from __future__ import annotations


class FakeObjectStore:
    """Records put/delete calls against an in-memory key→bytes map."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.delete_calls: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        self._objects[key] = data

    def exists(self, key: str) -> bool:
        return key in self._objects

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self._objects.pop(key, None)
