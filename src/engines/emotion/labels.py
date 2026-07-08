"""Emotion label re-exports for the Emotion Intelligence Engine.

Sentiment and StressLevel are the authoritative contract types defined in
Sprint-001 (src/libs/contracts/streaming.py). This module re-exports them
so emotion engine consumers import from a single package entry point.

The HOSTILE Sentiment value is added in Sprint-010 to support the
collections-domain case where a customer becomes verbally aggressive.

Architecture: V2 Ch10; V1 Ch19 (Emotion Intelligence).
"""

from __future__ import annotations

from src.libs.contracts.streaming import Sentiment, StressLevel

__all__ = ["Sentiment", "StressLevel"]
