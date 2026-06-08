"""
DataForge AI - Base Agent for ReAct tool-calling loops.

Shared logic between SQLAgent and DDLAgent:
  - Tool binding and mapping
  - ReAct iteration loop
  - Tool call logging
  - Rate limiting (basic)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


@dataclass
class ToolCallLog:
    """Single tool call record."""
    step: int
    tool: str
    args: dict


class BaseAgent:
    """Abstract base for ReAct agents with LangChain tool-calling.

    Subclasses should:
      - Set ``self.system_prompt`` and ``self.tools``
      - Call ``_bind_tools(llm)`` in __init__
      - Implement their own public entry method that calls ``_run_loop``
    """

    MAX_ITERATIONS = 15
    MIN_INTERVAL_BETWEEN_CALLS = 0.5  # seconds, basic rate limiting

    def __init__(self, llm: Any, tools: list, system_prompt: str):
        self.tools = tools
        self.system_prompt = system_prompt
        self._tool_map: dict[str, Any] = {t.name: t for t in tools}
        self._bind_tools(llm)

    def _bind_tools(self, llm: Any) -> None:
        self.llm = llm.bind_tools(self.tools)

    def _run_loop(
        self,
        messages: list[BaseMessage],
        tool_calls_log: list[ToolCallLog],
        on_reasoning: Any = None,
    ) -> AIMessage:
        """Execute the ReAct loop until the model stops calling tools.

        Args:
            messages: Conversation messages (will be mutated in-place).
            tool_calls_log: List to append tool call records to.
            on_reasoning: Optional callback(content) when the model
                          outputs text containing reasoning markers.

        Returns:
            The final AIMessage with no tool calls.
        """
        last_call_time = 0.0

        for iteration in range(1, self.MAX_ITERATIONS + 1):
            # Basic rate limiting
            elapsed = time.monotonic() - last_call_time
            if elapsed < self.MIN_INTERVAL_BETWEEN_CALLS:
                time.sleep(self.MIN_INTERVAL_BETWEEN_CALLS - elapsed)

            response: AIMessage = self.llm.invoke(messages)
            messages.append(response)
            last_call_time = time.monotonic()

            # Notify reasoning callback
            if response.content and on_reasoning:
                on_reasoning(response.content)

            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                return response

            for tc in tool_calls:
                tc_name = tc["name"]
                tc_args = tc["args"]
                tc_id = tc["id"]

                tool_calls_log.append(ToolCallLog(
                    step=iteration,
                    tool=tc_name,
                    args=tc_args,
                ))

                args_str = self._fmt_args(tc_args)
                print(f"    -> [{iteration}] {tc_name}({args_str})")

                tool_fn = self._tool_map.get(tc_name)
                if tool_fn:
                    try:
                        output = tool_fn.invoke(tc_args)
                    except Exception as e:
                        output = f"工具执行异常: {e}"
                else:
                    output = f"未知工具: {tc_name}"

                messages.append(ToolMessage(content=str(output), tool_call_id=tc_id))

        return AIMessage(
            content=f"[达到最大迭代 {self.MAX_ITERATIONS} 轮，请简化任务或拆分问题]"
        )

    @staticmethod
    def _fmt_args(args: dict) -> str:
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)
