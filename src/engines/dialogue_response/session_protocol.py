"""DialogueSessionState — the minimal per-call state shape
DialogueResponseEngine needs.

A Protocol, not a concrete import of
src.services.conversation_engine.ConversationSessionState: src/engines/ must
never import from src/services/ (check_boundaries.py Rule 2 — engines and
services communicate only through shapes, never concrete cross-package
types). ConversationSessionState (Phase 6b) already satisfies this shape by
construction; the caller (ConversationEngine, Phase 6g) passes its real
session object in, and duck typing does the rest at runtime.

Architecture: V6 Ch2 (module boundaries); V3 Ch6 (Recoverable protocol).
"""

from __future__ import annotations

from typing import Protocol


class DialogueSessionState(Protocol):
    @property
    def dialogue_state_name(self) -> str: ...
    def set_dialogue_state_name(self, value: str) -> None: ...

    @property
    def identity_verified(self) -> bool: ...
    def set_identity_verified(self, value: bool) -> None: ...

    @property
    def identity_reprompted(self) -> bool: ...
    def set_identity_reprompted(self, value: bool) -> None: ...

    @property
    def last_ask(self) -> str: ...
    def set_last_ask(self, value: str) -> None: ...

    @property
    def farewell_requested(self) -> bool: ...
    def set_farewell_requested(self, value: bool) -> None: ...

    @property
    def commitment(self) -> dict[str, str | int | None]: ...
    def update_commitment(
        self,
        amount_minor: int | None = None,
        months: int | None = None,
        date_text: str | None = None,
        cadence: str | None = None,
    ) -> None: ...

    def record_assistant_reply(self, reply: str) -> None: ...

    def set_last_empathy_state(self, value: str) -> None: ...

    @property
    def consecutive_else_count(self) -> int: ...
    def bump_consecutive_else_count(self) -> int: ...
    def reset_consecutive_else_count(self) -> None: ...


__all__ = ["DialogueSessionState"]
