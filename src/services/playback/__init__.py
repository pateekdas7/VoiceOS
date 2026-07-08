"""Playback services — scheduler and audio output conversion.

Architecture: V1 Ch18 (True Streaming Pipeline); V1 Ch21 (Playback).
"""

from __future__ import annotations

from .output import AudioOutput
from .scheduler import PlaybackScheduler

__all__ = ["AudioOutput", "PlaybackScheduler"]
