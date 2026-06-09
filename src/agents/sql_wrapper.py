"""SQLAgent wrapper — registers 智能问数 as a ManagedAgent."""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.agents.base import AgentResult, ManagedAgent

logger = logging.getLogger(__name__)


class SQLAgentWrapper(ManagedAgent):
    """Wraps the warehouse SQLAgent for multi-agent orchestration.

    Handles natural language data analysis: user asks a question in
    Chinese, the agent explores schemas, prints its thinking process,
    generates SQL, executes it, and optionally materializes results.
    """

    name = "sql_query"
    description = "智能问数：用自然语言提问，自动生成 SQL 查询并验证结果。适用于数据分析、统计报表、数据探索。"
    intent_keywords = [
        "查询", "统计", "分析", "SQL", "数据",
        "多少", "金额", "数量", "汇总", "报表",
        "购买", "消费", "订单", "销售", "月度",
        "查", "看", "计算", "排名", "对比",
    ]

    def __init__(self, llm: Any, db: Any, convention_file: Optional[str] = None):
        """Initialize with LLM and database connection.

        Args:
            llm: LangChain chat model instance.
            db: DuckDB connection or file path.
            convention_file: Optional path to convention YAML.
        """
        self._llm = llm
        self._db = db
        self._convention_file = convention_file

    def process(self, message: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Process a data analysis question through SQLAgent.

        The message is treated as a natural language data question.
        Context may override the default db/convention settings.
        """
        from src.warehouse.sql_agent import SQLAgent

        db = (context or {}).get("db", self._db)
        convention = (context or {}).get("convention_file", self._convention_file)

        try:
            agent = SQLAgent(
                llm=self._llm,
                db=db,
                convention_file=convention,
            )
            result = agent.analyze(message)

            # Build readable content from the analysis result
            parts = []
            if result.reasoning:
                parts.append(f"【思考过程】\n{result.reasoning}")
            if result.sql:
                parts.append(f"```sql\n{result.sql}\n```")
            if not parts:
                parts.append("分析完成，但未生成有效 SQL。")

            content = "\n\n".join(parts)

            return AgentResult(
                agent_name=self.name,
                content=content,
                metadata={
                    "question": result.question,
                    "sql": result.sql,
                    "reasoning": result.reasoning,
                    "tool_calls": len(result.tool_calls_log),
                },
                success=result.success,
                error=result.error,
            )

        except Exception as e:
            logger.error("SQLAgentWrapper error: %s", e, exc_info=True)
            return AgentResult(
                agent_name=self.name,
                content=f"智能问数处理失败: {e}",
                success=False,
                error=str(e),
            )
