"""
DataForge AI - SQL Analysis Agent with Explicit Reasoning

Uses BaseAgent for the ReAct loop. The model MUST output its thinking
process (【思考过程】) before writing any SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.warehouse.base_agent import BaseAgent, ToolCallLog
from src.warehouse.tools import ALL_TOOLS, init_tool_context


SYSTEM_PROMPT = """\
你是 DataForge AI 数据分析助手。用户会用自然语言描述数据需求，你需要生成 SQL 查询并验证结果。

## 可用工具

| 工具 | 用途 |
|------|------|
| list_tables | 列出所有表名和行数 |
| describe_table(table_name) | 查看表的字段详情 |
| get_sample_data(table_name, limit) | 查看样本数据 |
| execute_query(sql) | 执行 SELECT 查询，返回结果 |
| execute_ddl(ddl) | 执行 CREATE TABLE 等 DDL |
| create_table_from_query(table_name, select_sql, table_comment) | 将查询结果固化为持久化表 |
| read_convention | 读取建表规范（固化建表时需要） |

## 核心工作流程（必须严格遵守）

**第一步：探索** — list_tables + describe_table + get_sample_data
**第二步：思考** — 必须输出【思考过程】：需求理解、数据来源、关联关系、筛选条件、聚合逻辑、排序
**第三步：生成 SQL** — execute_query 验证，报错则修正
**第四步：总结** — 展示结果，建议是否固化

## SQL 规范（DuckDB）
- 时间：strftime(col, '%Y-%m')
- 金额：CAST(SUM(col) AS DECIMAL(18,2))
- 不加分号

## 约束
- 永远先输出【思考过程】再写 SQL
- 不猜测字段名，用 describe_table 确认
"""


@dataclass
class AnalysisResult:
    """Agent analysis result."""
    question: str
    sql: Optional[str] = None
    reasoning: Optional[str] = None
    tool_calls_log: list[ToolCallLog] = field(default_factory=list)
    success: bool = False
    error: Optional[str] = None


class SQLAgent(BaseAgent):
    """ReAct Agent for natural language data analysis."""

    def __init__(
        self,
        llm: Any,
        db: Any,
        convention_file: Optional[str] = None,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        init_tool_context(db, convention_file)
        super().__init__(llm, ALL_TOOLS, system_prompt)

    def analyze(self, question: str) -> AnalysisResult:
        """Analyze a natural language data question."""
        result = AnalysisResult(question=question)
        log: list[ToolCallLog] = []

        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=f"请帮我分析：{question}"),
        ]

        def on_reasoning(content: str) -> None:
            if "【思考过程】" in content:
                match = re.search(
                    r"【思考过程】(.*?)(?=```sql|\Z)", content, re.DOTALL
                )
                if match:
                    text = match.group(1).strip()
                    print()
                    print("    " + "=" * 56)
                    print("    【思考过程】")
                    for line in text.splitlines():
                        print(f"    {line.strip()}")
                    print("    " + "=" * 56)

        try:
            final = self._run_loop(messages, log, on_reasoning=on_reasoning)
            content = final.content or ""

            sql_matches = re.findall(r"```sql\s*\n(.*?)```", content, re.DOTALL)
            if sql_matches:
                result.sql = sql_matches[-1].strip()

            reasoning_match = re.search(
                r"【思考过程】(.*?)(?=```|$)", content, re.DOTALL
            )
            if reasoning_match:
                result.reasoning = reasoning_match.group(1).strip()

            result.success = True
        except Exception as e:
            result.error = str(e)

        result.tool_calls_log = log
        return result
