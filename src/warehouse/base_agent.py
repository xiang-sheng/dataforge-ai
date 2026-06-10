"""
DataForge AI - Base Agent powered by LangChain create_agent.

All agents (SQLAgent, DDLAgent, GovernanceAgent) share this base:
  - Tool binding via create_agent (LangGraph compiled graph)
  - Tool call logging via callback handler
  - Configurable recursion limit (replaces manual iteration loop)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage

logger = logging.getLogger(__name__)


@dataclass
class ToolCallLog:
    """Single tool call record."""
    step: int
    tool: str
    args: dict


class _ToolCallTracker(BaseCallbackHandler):
    """Callback handler that records tool calls for logging.

    Attached to the agent's invoke() via the callbacks config.
    """

    def __init__(self) -> None:
        self.calls: list[ToolCallLog] = []
        self._step = 0

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        self._step += 1
        tool_name = serialized.get("name", "unknown")
        try:
            args = eval(input_str) if isinstance(input_str, str) else input_str
            if not isinstance(args, dict):
                args = {"input": input_str}
        except Exception:
            args = {"raw": input_str}

        self.calls.append(ToolCallLog(
            step=self._step,
            tool=tool_name,
            args=args,
        ))

        args_str = BaseAgent._fmt_args(args)
        logger.info("[step=%d] %s(%s)", self._step, tool_name, args_str)


class BaseAgent:
    """Base agent using LangChain's create_agent (LangGraph).

    Subclasses should:
      - Call ``super().__init__(llm, tools, system_prompt)`` in __init__
      - Implement their own public entry method that calls ``invoke()``

    The ``invoke()`` method replaces the old ``_run_loop()``:
      - Creates a compiled agent graph via ``create_agent``
      - Runs tool-calling loop automatically
      - Returns the final AIMessage
    """

    MAX_ITERATIONS = 15

    def __init__(self, llm: Any, tools: list, system_prompt: str):
        self.tools = tools
        self.system_prompt = system_prompt
        self._llm = llm
        self._tool_map: dict[str, Any] = {t.name: t for t in tools}

    def invoke(
        self,
        messages: list[BaseMessage],
        tool_calls_log: Optional[list[ToolCallLog]] = None,
    ) -> AIMessage:
        """Run the agent with the given messages.

        Uses ``create_agent`` to build a LangGraph agent that handles
        tool binding and the tool-calling loop automatically.

        Args:
            messages: Conversation messages (SystemMessage + HumanMessage, etc.).
            tool_calls_log: Optional list to append ToolCallLog records to.

        Returns:
            The final AIMessage (with no pending tool calls).
        """
        agent = create_agent(
            self._llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
        )

        tracker = _ToolCallTracker()
        # recursion_limit counts graph steps (model + tool per iteration)
        # 2 * MAX_ITERATIONS gives roughly the same number of LLM calls
        config = {
            "callbacks": [tracker],
            "recursion_limit": self.MAX_ITERATIONS * 2,
        }

        result = agent.invoke({"messages": messages}, config=config)

        if tool_calls_log is not None:
            tool_calls_log.extend(tracker.calls)

        # Extract final AI message
        final_msgs = result.get("messages", [])
        if final_msgs:
            return final_msgs[-1]

        return AIMessage(content="[Agent 未返回结果]")

    @staticmethod
    def _fmt_args(args: dict) -> str:
        """Format tool arguments for logging."""
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)
