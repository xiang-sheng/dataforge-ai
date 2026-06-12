"""Agent Orchestrator — unified entry point for all AI capabilities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.agents.base import AgentResult
from src.agents.router import IntentRouter

if TYPE_CHECKING:
    from src.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Central coordinator for all AI agents.

    Provides a single `chat()` method that:
      1. Classifies user intent via IntentRouter
      2. Dispatches to the appropriate ManagedAgent
      3. Returns the result with agent metadata

    Usage:
        orch = AgentOrchestrator(registry, llm)
        result = orch.chat("查6月各商品购买数量和金额")
        # result.agent_name == "sql_query"
        # result.content == "..."
    """

    def __init__(self, registry: AgentRegistry, llm: Any):
        self.registry = registry
        self.router = IntentRouter(registry, llm)
        self._session_context: dict[str, Any] = {}

    def chat(
        self,
        message: str,
        target_agent: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Process a user message through the appropriate agent.

        Args:
            message: User's natural language input.
            target_agent: Optional explicit agent name (skips classification).
            context: Optional per-request context overrides.

        Returns:
            AgentResult from the dispatched agent.
        """
        # Merge session + request context
        ctx = dict(self._session_context)
        if context:
            ctx.update(context)

        # Route to agent
        agent_name = target_agent or self.router.classify(message)

        agent = self.registry.get(agent_name)

        if agent is None:
            # Agent not found — return helpful message
            available = self.registry.list_agents()
            names = ", ".join(a["name"] for a in available)
            return AgentResult(
                agent_name="router",
                content=f"无法识别意图。可用功能：{names}",
                metadata={"available_agents": available},
                success=False,
                error=f"Agent '{agent_name}' not found",
            )

        # Dispatch
        logger.info("Dispatching to agent: %s", agent_name)
        try:
            result = agent.process(message, ctx)
            result.metadata["routed_agent"] = agent_name
            return result
        except Exception as e:
            logger.error("Agent '%s' failed: %s", agent_name, e, exc_info=True)
            return AgentResult(
                agent_name=agent_name,
                content=f"处理失败: {e}",
                success=False,
                error=str(e),
            )

    def set_context(self, key: str, value: Any) -> None:
        """Set a session-level context value (e.g., db connection)."""
        self._session_context[key] = value

    def list_agents(self) -> list[dict[str, str]]:
        """List all registered agents with descriptions."""
        return self.registry.list_agents()
