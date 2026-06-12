"""
DataForge AI - DDL Agent (ReAct Agent for DDL Generation)

Uses BaseAgent (powered by create_agent) for the tool-calling loop.
The model explores source schemas, reads conventions, designs target
tables, generates DDL, and verifies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.warehouse.base_agent import BaseAgent, ToolCallLog
from src.warehouse.tools import ALL_TOOLS, init_tool_context

SYSTEM_PROMPT = """\
你是 DataForge AI 数仓架构师。根据源库现有表信息，为数仓指定层生成规范 DDL。

## 可用工具
list_tables, describe_table, get_sample_data, execute_query, execute_ddl,
create_table_from_query, read_convention

## 数仓分层
- ODS：与源表对齐 + etl_time TIMESTAMP + source_system VARCHAR
- DWD：清洗去重(ROW_NUMBER) + 维度退化 + etl_time
- DWS：GROUP BY 聚合(SUM/COUNT/AVG) + etl_time
- ADS：面向业务的筛选宽表

## DuckDB DDL 语法
- 类型：VARCHAR, INTEGER, BIGINT, DOUBLE, DECIMAL(18,2), BOOLEAN, DATE, TIMESTAMP
- 注释：COMMENT ON TABLE/COLUMN
- 无 PARTITION BY / ENGINE

## 工作流程
1. describe_table 看清源表结构
2. read_convention 读规范
3. 设计目标表（字段、类型、注释）
4. 写 CREATE TABLE + COMMENT ON 语句
5. execute_ddl 验证
6. 最终 DDL 放在 ```sql ... ``` 中

## 约束
- snake_case 字段名
- 每个字段必须有中文注释
- 金额 DECIMAL(18,2)，ID 用 BIGINT，时间用 TIMESTAMP
- 必须包含 etl_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
"""


@dataclass
class DDLAgentResult:
    """Agent DDL generation result."""
    source_table: str
    target_layer: str
    ddl: str | None = None
    verification: str | None = None
    tool_calls_log: list[ToolCallLog] = field(default_factory=list)
    success: bool = False
    error: str | None = None


class DDLAgent(BaseAgent):
    """ReAct Agent for DDL generation from source table schemas."""

    def __init__(
        self,
        llm: Any,
        db: Any,
        convention_file: str | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        init_tool_context(db, convention_file)
        super().__init__(llm, ALL_TOOLS, system_prompt)

    def build(
        self,
        source_table: str,
        target_layer: str,
        business_desc: str = "",
    ) -> DDLAgentResult:
        """Generate DDL for a warehouse layer from a source table."""
        result = DDLAgentResult(
            source_table=source_table,
            target_layer=target_layer,
        )
        log: list[ToolCallLog] = []

        user_msg = (
            f"请为源表 `{source_table}` 生成 **{target_layer}** 层的目标表 DDL。\n"
            f"先探索源表结构，再读取建表规范，然后设计并生成 DDL，最后用 execute_ddl 验证。"
        )
        if business_desc:
            user_msg += f"\n\n业务说明: {business_desc}"

        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_msg),
        ]

        try:
            final = self.invoke(messages, log)
            content = final.content or ""

            matches = re.findall(r"```sql\s*\n(.*?)```", content, re.DOTALL)
            if matches:
                result.ddl = "\n".join(m.strip() for m in matches)
                result.success = True
            else:
                result.error = "Agent 未能输出 DDL（```sql ... ```）"
        except Exception as e:
            result.error = str(e)

        result.tool_calls_log = log
        return result
