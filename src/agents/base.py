"""Abstract base for all managed agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentResult:
    """Unified result from any managed agent."""
    agent_name: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


class ManagedAgent(ABC):
    """Base class for agents managed by the Orchestrator.

    Every agent must declare:
      - name: unique identifier
      - description: what this agent does (shown to users)
      - intent_keywords: hints for intent classification
    """

    name: str = ""
    description: str = ""
    intent_keywords: list[str] = []

    @abstractmethod
    def process(self, message: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Process a user message and return a result.

        Args:
            message: The user's natural language input.
            context: Optional session context (db connection, prior results, etc.)

        Returns:
            AgentResult with the agent's output.
        """
        ...
