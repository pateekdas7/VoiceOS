"""FakeAudioBridge — simulates supervisor barge-in/live-transfer audio routing (Sprint-023).

Matches Sprint-023.md's Phase 1 Mock Backends table: "Audio routing |
FakeAudioBridge | Simulates supervisor barge-in without real telephony."
Implements ``src.services.contact_center.live_transfer.AudioBridgePort``.
"""

from __future__ import annotations


class FakeAudioBridge:
    """In-memory audio-bridge double: tracks bridged targets and AI-mute state per call."""

    def __init__(self) -> None:
        self.bridged: dict[str, str] = {}
        self._muted: set[str] = set()
        self.bridge_calls: list[tuple[str, str]] = []

    def bridge(self, call_id: str, target_id: str) -> None:
        self.bridged[call_id] = target_id
        self.bridge_calls.append((call_id, target_id))

    def mute_ai(self, call_id: str) -> None:
        self._muted.add(call_id)

    def unmute_ai(self, call_id: str) -> None:
        self._muted.discard(call_id)

    def is_ai_muted(self, call_id: str) -> bool:
        return call_id in self._muted
