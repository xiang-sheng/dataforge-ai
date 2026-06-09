"""Agent Registry — central catalog of all available agents."""

from __future__ import annotations

import logging
from typing import Optional

from src.agents.base import ManagedAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Manages registration and lookup of all agents.

    Usage:
        registry = AgentRegistry()
        registry.register(sql_agent)
        registry.register(ddl_agent)

        agent = registry.get("sql_query")
        all_agents = registry.list_agents()
    """

    def __init__(self):
        self._agents: dict[str, ManagedAgent] = {}

    def register(self, agent: ManagedAgent) -> None:
        """Register an agent. Raises ValueError if name already taken."""
        if not agent.name:
            raise ValueError(f"Agent {type(agent).__name__} has no name set.")
        if agent.name in self._agents:
            raise ValueError(f"Agent name '{agent.name}' is already registered.")
        self._agents[agent.name] = agent
        logger.info("Registered agent: %s — %s", agent.name, agent.description)

    def unregister(self, name: str) -> None:
        """Remove an agent by name."""
        self._agents.pop(name, None)

    def get(self, name: str) -> Optional[ManagedAgent]:
        """Get an agent by name, or None if not found."""
        return self._agents.get(name)

    def list_agents(self) -> list[dict[str, str]]:
        """Return metadata for all registered agents."""
        return [
            {
                "name": a.name,
                "description": a.description,
                "keywords": ", ".join(a.intent_keywords[:8]),
            }
            for a in self._agents.values()
        ]

    @property
    def agents(self) -> dict[str, ManagedAgent]:
        return dict(self._agents)
