"""
DataForge AI - SQL Analysis Agent with Explicit Reasoning

The agent uses LangChain tool-calling to:
  1. Explore database schemas
  2. Print explicit step-by-step reasoning (思考过程) BEFORE generating SQL
  3. Generate and execute analytical SQL
  4. Verify results
  5. Suggest & create persistent tables for recurring analyses

The key design principle: the model MUST output its thinking process
(which tables, joins, filters, aggregations) before writing any SQL.
This dramatically improves accuracy, especially for smaller local models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.warehouse.tools import ALL_TOOLS, init_tool_context


# ---------------------------------------------------------------------------
#  System prompt — emphasizes reasoning process
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
你是 DataForge AI 数据分析助手。用户会用自然语言描述数据需求，你需要生成 SQL 查询并验证结果。

## 可用工具

| 工具 | 用途 |
|------|------|
| list_tables | 列出所有表名和行数 |
| describe_table(table_name) | 查看表的字段详情（名称、类型、可空、默认值） |
| get_sample_data(table_name, limit) | 查看样本数据 |
| execute_query(sql) | 执行 SELECT 查询，返回结果 |
| execute_ddl(ddl) | 执行 CREATE TABLE 等 DDL |
| create_table_from_query(table_name, select_sql, table_comment) | 将查询结果固化为持久化表 |
| read_convention | 读取建表规范（固化建表时需要） |

## 核心工作流程（必须严格遵守）

**第一步：探索**
- 用 list_tables 了解有哪些表
- 用 describe_table 查看相关表的字段结构
- 必要时用 get_sample_data 看数据样例

**第二步：思考（必须输出，用【思考过程】标记）**

在生成任何 SQL 之前，你**必须**先输出你的分析思路，格式如下：

【思考过程】
1. 需求理解：用户想要什么数据？
2. 数据来源：需要哪些表？哪些字段？
3. 关联关系：表之间怎么 JOIN？关联条件是什么？
4. 筛选条件：需要 WHERE 过滤什么？时间范围？
5. 聚合逻辑：需要 GROUP BY 什么？用什么聚合函数（SUM/COUNT/AVG）？
6. 排序展示：结果怎么排序？

**第三步：生成 SQL**
- 基于思考过程，写出完整的 SQL
- 用 execute_query 执行验证
- 如果报错，分析原因并修正

**第四步：总结 & 建议**
- 展示查询结果
- 如果这个查询会被反复使用，建议用 create_table_from_query 固化为表
- 如果要固化建表，先 read_convention 了解命名规范

## SQL 编写规范（DuckDB 语法）

- 时间截取：`strftime(col, '%Y-%m')` 取年月，`date_trunc('month', col)` 取月初
- 条件过滤：`WHERE strftime(order_time, '%Y-%m') = '2025-06'`
- 字符串拼接用 `||`
- NULL 处理用 `COALESCE(col, default)`
- 金额聚合用 `CAST(SUM(col) AS DECIMAL(18,2))`
- 每条 SQL 末尾不要加分号

## 重要约束

- 永远先输出【思考过程】，再写 SQL
- 不要猜测字段名，必须用 describe_table 确认
- 如果用户的需求有歧义，先提问澄清
- 每个查询结果都要分析是否合理（行数对不对、金额量级对不对）
"""


# ---------------------------------------------------------------------------
#  Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """Agent 单次分析结果。"""
    question: str
    sql: Optional[str] = None
    query_result: Optional[str] = None
    reasoning: Optional[str] = None         # 思考过程
    materialized_table: Optional[str] = None  # 如果固化了
    tool_calls_log: list[dict] = field(default_factory=list)
    success: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
#  SQL Analysis Agent
# ---------------------------------------------------------------------------

class SQLAgent:
    """ReAct Agent：LLM + 工具调用，强调思考过程输出。"""

    MAX_ITERATIONS = 15

    def __init__(
        self,
        llm: Any,
        db: Any,
        convention_file: Optional[str] = None,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        init_tool_context(db, convention_file)
        self.tools = ALL_TOOLS
        self.llm = llm.bind_tools(self.tools)
        self.system_prompt = system_prompt
        self._tool_map = {t.name: t for t in self.tools}

    def analyze(self, question: str) -> AnalysisResult:
        """分析用户的自然语言数据需求。

        Args:
            question: 用户的自然语言问题，如"查2025年6月各商品购买数和金额"

        Returns:
            AnalysisResult 包含 SQL、查询结果、思考过程
        """
        result = AnalysisResult(question=question)

        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=f"请帮我分析：{question}"),
        ]

        try:
            final = self._run_loop(messages, result)
            content = final.content or ""

            # Extract SQL
            sql_matches = re.findall(r"```sql\s*\n(.*?)```", content, re.DOTALL)
            if sql_matches:
                result.sql = sql_matches[-1].strip()

            # Extract reasoning
            reasoning_match = re.search(
                r"【思考过程】(.*?)(?=```|$)", content, re.DOTALL
            )
            if reasoning_match:
                result.reasoning = reasoning_match.group(1).strip()

            result.success = True

        except Exception as e:
            result.error = str(e)

        return result

    # ------------------------------------------------------------------
    #  ReAct loop
    # ------------------------------------------------------------------

    def _run_loop(
        self,
        messages: list[BaseMessage],
        result: AnalysisResult,
    ) -> AIMessage:
        for iteration in range(1, self.MAX_ITERATIONS + 1):
            response: AIMessage = self.llm.invoke(messages)
            messages.append(response)

            # Print reasoning if present in this turn
            if response.content:
                content = response.content
                if "【思考过程】" in content:
                    match = re.search(r"【思考过程】(.*?)(?=```sql|\Z)", content, re.DOTALL)
                    if match:
                        reasoning_text = match.group(1).strip()
                        print()
                        print("    " + "=" * 56)
                        print("    【思考过程】")
                        for line in reasoning_text.splitlines():
                            print(f"    {line.strip()}")
                        print("    " + "=" * 56)

            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                return response

            for tc in tool_calls:
                tc_name = tc["name"]
                tc_args = tc["args"]
                tc_id = tc["id"]

                result.tool_calls_log.append({
                    "step": iteration,
                    "tool": tc_name,
                    "args": tc_args,
                })

                args_str = self._fmt_args(tc_args)
                print(f"    -> [{iteration}] {tc_name}({args_str})")

                tool_fn = self._tool_map.get(tc_name)
                if tool_fn:
                    try:
                        output = tool_fn.invoke(tc_args)
                    except Exception as e:
                        output = f"工具异常: {e}"
                else:
                    output = f"未知工具: {tc_name}"

                messages.append(ToolMessage(content=str(output), tool_call_id=tc_id))

        return AIMessage(content="[达到最大迭代轮次]")

    @staticmethod
    def _fmt_args(args: dict) -> str:
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)
