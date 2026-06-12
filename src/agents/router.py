"""Intent Router — LLM-based classification of user messages to agents."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

if TYPE_CHECKING:
    from src.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = """\
你是一个意图分类器。根据用户消息判断应该使用哪个智能体来处理。

可用智能体：
{agent_descriptions}

只返回 JSON 格式的 agent name，不要输出任何其他内容。
格式：{{"agent": "agent_name"}}

如果无法判断，返回：{{"agent": "unknown"}}
"""


class IntentRouter:
    """Uses LLM to classify user intent and route to the correct agent.

    The router sends a lightweight classification prompt to the LLM,
    which returns just the agent name. This is separate from the
    actual agent processing.
    """

    def __init__(self, registry: AgentRegistry, llm: Any):
        self.registry = registry
        self.llm = llm

    def classify(self, message: str) -> str:
        """Classify a user message and return the target agent name.

        Uses LLM to determine intent. Falls back to keyword matching
        if LLM classification fails.
        """
        agents = self.registry.agents
        if not agents:
            return "unknown"

        # If only one agent, skip classification
        if len(agents) == 1:
            return next(iter(agents))

        # Build agent descriptions for the prompt
        descriptions = "\n".join(
            f"- {a.name}: {a.description}（关键词：{', '.join(a.intent_keywords[:5])}）"
            for a in agents.values()
        )

        system = ROUTER_SYSTEM_PROMPT.format(agent_descriptions=descriptions)

        try:
            response = self.llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=message),
            ])

            content = response.content.strip()
            # Extract JSON from response
            if "{" in content:
                start = content.index("{")
                end = content.rindex("}") + 1
                data = json.loads(content[start:end])
                agent_name = data.get("agent", "unknown")

                if agent_name in agents:
                    logger.info("Intent classified: '%s' → %s", message[:50], agent_name)
                    return agent_name

        except Exception as e:
            logger.warning("LLM classification failed: %s, falling back to keywords", e)

        # Fallback: keyword matching
        return self._keyword_match(message)

    def _keyword_match(self, message: str) -> str:
        """Fallback: match by intent keywords."""
        msg_lower = message.lower()
        best_match = "unknown"
        best_score = 0

        for agent in self.registry.agents.values():
            score = sum(1 for kw in agent.intent_keywords if kw in msg_lower)
            if score > best_score:
                best_score = score
                best_match = agent.name

        if best_score > 0:
            logger.info("Keyword match: '%s' → %s (score=%d)", message[:50], best_match, best_score)

        return best_match
