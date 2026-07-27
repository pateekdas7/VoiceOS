"""Reasoning-model adapters (ADR-006 Sec 3.3) -- the ONLY place ``reasoning/`` calls an LLM.

Mirrors the STTAdapter/LLMAdapter/TTSAdapter "replaceable adapter" shape
(``src/services/{stt,llm_runtime,tts}/protocol.py``) -- the reasoning model
is a replaceable adapter behind :class:`ReasoningAdapter`, never coupled to
business logic (CLAUDE.md "AI Model Rules").
"""

from __future__ import annotations

from .claude_adapter import ClaudeReasoningAdapter
from .protocol import NarrationRequest, NarrationResult, ReasoningAdapter

__all__ = ["ClaudeReasoningAdapter", "NarrationRequest", "NarrationResult", "ReasoningAdapter"]
