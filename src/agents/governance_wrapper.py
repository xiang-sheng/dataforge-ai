"""GovernanceAgent wrapper — 数据治理：识别冗余表."""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.agents.base import AgentResult, ManagedAgent

logger = logging.getLogger(__name__)


GOVERNANCE_SYSTEM_PROMPT = """\
你是 DataForge AI 数据治理专家。你的任务是分析数据库中的表，识别冗余和重叠，给出治理建议。

## 可用工具

| 工具 | 用途 |
|------|------|
| list_tables | 列出所有表名和行数 |
| describe_table(table_name) | 查看表的字段详情 |
| get_sample_data(table_name, limit) | 查看样本数据 |
| compare_tables(table_a, table_b) | 对比两张表的结构和数据重叠程度 |
| execute_query(sql) | 执行 SELECT 查询 |

## 工作流程

1. list_tables 获取所有表清单
2. 根据表名和行数初步筛选可能冗余的表对
3. describe_table 逐表查看字段结构
4. compare_tables 对疑似冗余的表对做详细对比
5. get_sample_data 确认数据是否真的重叠
6. 输出治理报告

## 冗余判断标准

- 列名相似度 ≥ 80% 且类型匹配 ≥ 80% → 高度冗余，建议合并
- 列名相似度 50%-80% → 部分重叠，可能有优化空间
- 表名有明确的前后缀关系（如 _bak, _copy, _old, _tmp, _v2）→ 高度可疑

## 输出格式

最终报告必须包含：
1. **扫描概览** — 总共扫描了多少张表
2. **冗余发现** — 列出发现的冗余表对，标注相似度
3. **治理建议** — 对每对冗余表给出具体建议（合并/归档/删除）
4. **风险提醒** — 合并或删除前需要注意的依赖和注意事项

## 约束
- 不要猜测，必须通过工具获取实际数据
- 对所有可能的冗余对都做 compare_tables 确认
- 给出可执行的建议，不要只列数据
"""


class GovernanceAgentWrapper(ManagedAgent):
    """Wraps a BaseAgent-based governance agent for multi-agent orchestration.

    Analyzes database tables to identify redundancy, overlapping schemas,
    and suggest consolidation strategies.
    """

    name = "data_governance"
    description = "数据治理：扫描所有表，识别冗余表和重叠结构，给出合并/归档建议。"
    intent_keywords = [
        "冗余", "重复", "治理", "重叠", "合并",
        "清理", "归档", "废弃", "冗余表", "对比表",
        "哪些表重复", "表太多", "优化表", "整理",
    ]

    def __init__(self, llm: Any, db: Any, convention_file: Optional[str] = None):
        self._llm = llm
        self._db = db
        self._convention_file = convention_file

    def process(self, message: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Run governance analysis on the database.

        Uses BaseAgent (powered by create_agent) to autonomously explore
        tables and identify redundancy.
        """
        from src.warehouse.base_agent import BaseAgent, ToolCallLog
        from src.warehouse.tools import ALL_TOOLS, init_tool_context

        db = (context or {}).get("db", self._db)
        convention = (context or {}).get("convention_file", self._convention_file)

        try:
            init_tool_context(db, convention)

            agent = BaseAgent(self._llm, ALL_TOOLS, GOVERNANCE_SYSTEM_PROMPT)
            log: list[ToolCallLog] = []

            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(content=GOVERNANCE_SYSTEM_PROMPT),
                HumanMessage(content=f"请对当前数据库做数据治理分析，识别冗余表并给出治理建议。\n\n分析需求：{message}"),
            ]

            final = agent.invoke(messages, log)
            content = final.content or "治理分析完成，但未输出有效报告。"

            return AgentResult(
                agent_name=self.name,
                content=content,
                metadata={
                    "tool_calls": len(log),
                    "tool_calls_log": [
                        {"step": e.step, "tool": e.tool, "args": e.args}
                        for e in log
                    ],
                },
                success=True,
            )

        except Exception as e:
            logger.error("GovernanceAgent error: %s", e, exc_info=True)
            return AgentResult(
                agent_name=self.name,
                content=f"数据治理分析失败: {e}",
                success=False,
                error=str(e),
            )
