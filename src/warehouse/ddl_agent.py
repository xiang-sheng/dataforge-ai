"""
DataForge AI - DDL Agent (ReAct Agent with Tool Calling)

Uses LangChain's bind_tools + tool_call protocol to let the LLM
autonomously:
  1. Explore source table schemas
  2. Read convention files
  3. Design target warehouse tables
  4. Generate DDL
  5. Verify DDL in DuckDB sandbox

No RAG, no vector store — the model reads conventions directly
and decides what information it needs on its own.
"""

from __future__ import annotations

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
#  System prompt — tells the agent its role, available tools, and workflow
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
你是 DataForge AI 数仓架构师。你的任务是根据源库的现有表信息，为数仓指定层生成规范的 DDL 建表语句。

## 可用工具

| 工具 | 用途 |
|------|------|
| list_source_tables | 列出源库所有表 |
| describe_source_table(table_name) | 查看源表字段详情（类型、是否可空、默认值） |
| get_sample_data(table_name, limit) | 查看源表样本数据 |
| read_convention | 读取建表规范文件（命名规则、类型映射、分区策略、质量约束） |
| ddl_verify(ddl) | 在 DuckDB 沙箱中执行 DDL，验证语法正确性 |
| list_target_tables | 查看目标库已建的表 |
| query_target(sql) | 在目标库执行 SELECT |

## 数仓分层设计

- **ODS 层**：操作数据层。与源表结构对齐，追加 etl_time(TIMESTAMP)、source_system(VARCHAR) 字段
- **DWD 层**：明细事实层。清洗去重（ROW_NUMBER），维度退化（JOIN 维度字段），追加 etl_time
- **DWS 层**：汇总层。按业务维度 GROUP BY 聚合（SUM/COUNT/AVG/MAX/MIN），追加 etl_time
- **ADS 层**：应用层。面向业务场景的筛选和宽表

## DuckDB DDL 语法要点

- 类型：VARCHAR, INTEGER, BIGINT, DOUBLE, DECIMAL(18,2), BOOLEAN, DATE, TIMESTAMP
- 表注释：`COMMENT ON TABLE xxx IS '描述';`
- 列注释：`COMMENT ON COLUMN xxx.yyy IS '描述';`
- 不支持 PARTITION BY、ENGINE 等引擎特有语法
- 每条语句用分号结尾
- 每个字段都必须有 COMMENT ON COLUMN

## 工作流程

1. **探索源表**：先用 describe_source_table 看清字段结构，必要时用 get_sample_data 看数据
2. **读取规范**：调用 read_convention 了解命名和类型标准
3. **设计目标表**：根据分层要求确定字段、类型、注释
4. **生成 DDL**：写完整的 CREATE TABLE + COMMENT ON 语句
5. **验证**：调用 ddl_verify 确认能在 DuckDB 中执行
6. **输出**：将最终 DDL 放在 ```sql ... ``` 代码块中

## 重要约束

- 所有字段名使用 snake_case
- 每个字段必须有中文注释
- 金额字段用 DECIMAL(18,2)
- ID 字段用 BIGINT
- 时间字段用 TIMESTAMP
- 必须包含 etl_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- DDL 中不要包含 INSERT、SELECT 等 DML 语句
"""


# ---------------------------------------------------------------------------
#  Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DDLAgentResult:
    """Agent 执行结果。"""
    source_table: str
    target_layer: str
    ddl: Optional[str] = None
    verification: Optional[str] = None
    tool_calls_log: list[dict] = field(default_factory=list)
    success: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
#  DDL Agent
# ---------------------------------------------------------------------------

class DDLAgent:
    """ReAct Agent：LLM + 工具调用循环。

    模型通过 tool-calling 自主决定要调用哪些工具、以什么顺序调用，
    从而完成源表探索 → 规范阅读 → DDL 设计 → 验证的完整流程。
    """

    MAX_ITERATIONS = 15

    def __init__(
        self,
        llm: Any,
        db: Any,
        convention_file: Optional[str] = None,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        # 初始化工具上下文（DB 连接 + 规范路径）
        init_tool_context(db, convention_file)

        # 将工具绑定到 LLM（让模型知道有哪些工具可调用）
        self.tools = ALL_TOOLS
        self.llm = llm.bind_tools(self.tools)
        self.system_prompt = system_prompt

        # 构建 name → tool_fn 映射
        self._tool_map: dict[str, Any] = {t.name: t for t in self.tools}

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def build(
        self,
        source_table: str,
        target_layer: str,
        business_desc: str = "",
    ) -> DDLAgentResult:
        """驱动 Agent 为数仓指定层生成 DDL。

        Args:
            source_table:  源表名（Agent 会用工具去探索它的结构）
            target_layer:  目标层级 — ODS / DWD / DWS / ADS
            business_desc: 可选的业务描述，帮助 Agent 理解上下文

        Returns:
            DDLAgentResult 包含生成的 DDL、验证结果和工具调用日志
        """
        result = DDLAgentResult(
            source_table=source_table,
            target_layer=target_layer,
        )

        # 构建初始消息
        user_msg = self._build_user_message(source_table, target_layer, business_desc)
        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_msg),
        ]

        # 运行 ReAct 循环
        try:
            final = self._run_loop(messages, result)
            result.ddl = self._extract_sql(final)
            result.success = result.ddl is not None
            if not result.success:
                result.error = "Agent 未能在最终回复中输出 DDL（```sql ... ```）"
        except Exception as e:
            result.error = str(e)

        return result

    # ------------------------------------------------------------------
    #  Internal: ReAct loop
    # ------------------------------------------------------------------

    def _run_loop(
        self,
        messages: list[BaseMessage],
        result: DDLAgentResult,
    ) -> AIMessage:
        """ReAct 循环：LLM → tool calls → LLM → ... → final answer."""

        for iteration in range(1, self.MAX_ITERATIONS + 1):
            # 调用 LLM
            response: AIMessage = self.llm.invoke(messages)
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None)

            # 无工具调用 → 模型给出了最终答案
            if not tool_calls:
                return response

            # 依次执行每个工具调用
            for tc in tool_calls:
                tc_name = tc["name"]
                tc_args = tc["args"]
                tc_id = tc["id"]

                # 记录日志
                result.tool_calls_log.append({
                    "step": iteration,
                    "tool": tc_name,
                    "args": tc_args,
                })

                print(f"    → [{iteration}] {tc_name}({self._fmt_args(tc_args)})")

                # 执行工具
                tool_fn = self._tool_map.get(tc_name)
                if tool_fn:
                    try:
                        output = tool_fn.invoke(tc_args)
                    except Exception as e:
                        output = f"工具执行异常: {e}"
                else:
                    output = f"未知工具: {tc_name}"

                messages.append(ToolMessage(content=str(output), tool_call_id=tc_id))

        # 超过最大迭代次数
        return AIMessage(content=f"[达到最大迭代 {self.MAX_ITERATIONS} 轮，请检查任务复杂度]")

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(
        source_table: str, layer: str, desc: str
    ) -> str:
        msg = (
            f"请为源表 `{source_table}` 生成 **{layer}** 层的目标表 DDL。\n"
            f"先探索源表结构，再读取建表规范，然后设计并生成 DDL，最后用 ddl_verify 验证。"
        )
        if desc:
            msg += f"\n\n业务说明: {desc}"
        return msg

    @staticmethod
    def _extract_sql(message: AIMessage) -> Optional[str]:
        """从 AI 回复中提取 ```sql ... ``` 代码块。"""
        import re
        text = message.content or ""
        matches = re.findall(r"```sql\s*\n(.*?)```", text, re.DOTALL)
        if matches:
            return "\n".join(m.strip() for m in matches)
        return None

    @staticmethod
    def _fmt_args(args: dict) -> str:
        """格式化参数用于控制台输出。"""
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 60:
                sv = sv[:57] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)
