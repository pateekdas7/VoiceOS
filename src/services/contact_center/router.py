"""SkillsBasedRouter — matches a call to an available agent by skill (V5 Ch7.3).

Architecture: V5 Ch7 (Contact Center Platform — Skills-Based Routing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AgentStatus(StrEnum):
    """Agent presence state (V5 Ch7.12 ``AgentState.status``)."""

    AVAILABLE = "AVAILABLE"
    ON_CALL = "ON_CALL"
    WRAP_UP = "WRAP_UP"
    AWAY = "AWAY"


@dataclass(frozen=True)
class AgentState:
    """One human agent's routing-relevant state (V5 Ch7.12)."""

    agent_id: str
    status: AgentStatus = AgentStatus.AVAILABLE
    skills: tuple[str, ...] = field(default=())
    languages: tuple[str, ...] = field(default=())
    current_call: str | None = None


class SkillsBasedRouter:
    """In-process agent directory + skills-based routing (V5 Ch7.3)."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentState] = {}

    def register_agent(self, agent: AgentState) -> None:
        self._agents[agent.agent_id] = agent

    def update_state(
        self,
        agent_id: str,
        *,
        status: AgentStatus | None = None,
        current_call: str | None = "__unset__",  # sentinel: distinguish "clear" from "leave unchanged"
    ) -> AgentState:
        current = self._agents.get(agent_id)
        if current is None:
            raise ValueError(f"unknown agent: {agent_id}")
        updated = AgentState(
            agent_id=current.agent_id,
            status=status if status is not None else current.status,
            skills=current.skills,
            languages=current.languages,
            current_call=current.current_call if current_call == "__unset__" else current_call,
        )
        self._agents[agent_id] = updated
        return updated

    def find_agent(self, required_skills: tuple[str, ...] = (), language: str | None = None) -> AgentState | None:
        """Return the first AVAILABLE agent matching all ``required_skills`` (+ ``language`` if given)."""
        required = set(required_skills)
        for agent in self._agents.values():
            if agent.status != AgentStatus.AVAILABLE:
                continue
            if not required.issubset(agent.skills):
                continue
            if language is not None and language not in agent.languages:
                continue
            return agent
        return None

    def get(self, agent_id: str) -> AgentState | None:
        return self._agents.get(agent_id)
