"""DDLAgent wrapper — registers 智能建表 as a ManagedAgent."""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar

from src.agents.base import AgentResult, ManagedAgent

logger = logging.getLogger(__name__)


class DDLAgentWrapper(ManagedAgent):
    """Wraps the warehouse DDLAgent for multi-agent orchestration.

    Handles DDL generation: given a source table and target warehouse
    layer (ODS/DWD/DWS/ADS), the agent explores the schema, reads
    conventions, designs the target table, and generates verified DDL.
    """

    name = "ddl_build"
    description = "智能建表：从源表结构自动生成数仓 DDL（支持 ODS/DWD/DWS/ADS 层）。适用于 ETL 建表、数仓分层设计。"
    intent_keywords: ClassVar[list[str]] = [
        "建表", "DDL", "数仓", "创建表", "ETL",
        "ODS", "DWD", "DWS", "ADS", "生成表",
        "分层", "维度", "事实表", "宽表", "汇总表",
        "目标表", "源表", "字段设计",
    ]

    def __init__(self, llm: Any, db: Any, convention_file: str | None = None):
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
        """Process a DDL generation request through DDLAgent.

        Parses the user message to extract source table, target layer,
        and optional business description. Falls back to asking the
        agent to infer these from context.
        """
        from src.warehouse.ddl_agent import DDLAgent

        db = (context or {}).get("db", self._db)
        convention = (context or {}).get("convention_file", self._convention_file)

        # Try to extract structured parameters from the message
        source_table, target_layer, business_desc = self._parse_message(message, context)

        try:
            agent = DDLAgent(
                llm=self._llm,
                db=db,
                convention_file=convention,
            )
            result = agent.build(
                source_table=source_table,
                target_layer=target_layer,
                business_desc=business_desc,
            )

            parts = []
            if result.ddl:
                parts.append(f"生成的 DDL:\n```sql\n{result.ddl}\n```")
            if result.verification:
                parts.append(f"验证结果:\n{result.verification}")
            if not parts:
                parts.append("DDL 生成完成，但未输出有效 DDL。")

            content = "\n\n".join(parts)

            return AgentResult(
                agent_name=self.name,
                content=content,
                metadata={
                    "source_table": result.source_table,
                    "target_layer": result.target_layer,
                    "ddl": result.ddl,
                    "tool_calls": len(result.tool_calls_log),
                },
                success=result.success,
                error=result.error,
            )

        except Exception as e:
            logger.error("DDLAgentWrapper error: %s", e, exc_info=True)
            return AgentResult(
                agent_name=self.name,
                content=f"智能建表处理失败: {e}",
                success=False,
                error=str(e),
            )

    @staticmethod
    def _parse_message(
        message: str, context: dict[str, Any] | None
    ) -> tuple[str, str, str]:
        """Extract source_table, target_layer, business_desc from message.

        Tries context first, then regex extraction from the message.
        Defaults to empty strings if not found — the DDLAgent will
        explore and infer on its own.
        """
        ctx = context or {}

        # Explicit context overrides
        source = ctx.get("source_table", "")
        layer = ctx.get("target_layer", "")
        desc = ctx.get("business_desc", "")

        if source and layer:
            return source, layer, desc

        # Try to extract from message text
        # Layer detection
        layer_patterns = {
            "ODS": r"\bods\b",
            "DWD": r"\bdwd\b",
            "DWS": r"\bdws\b",
            "ADS": r"\bads\b",
        }
        if not layer:
            for name, pat in layer_patterns.items():
                if re.search(pat, message, re.IGNORECASE):
                    layer = name
                    break
        if not layer:
            layer = "DWS"  # sensible default

        # Source table detection — look for backtick-quoted or common patterns
        if not source:
            # Match table names in backticks: `orders`
            m = re.search(r"`(\w+)`", message)
            if m:
                source = m.group(1)
            else:
                # Match "<identifier> 源表" e.g. "order_items 源表生成"
                m = re.search(r"(\w+)\s+源表", message)
                if m:
                    source = m.group(1)
                else:
                    # Match "源表 <identifier>" e.g. "源表 events"
                    m = re.search(r"源表\s+(\w+)", message)
                    if m:
                        source = m.group(1)
                    else:
                        # Match "表 <identifier>" (standalone)
                        m = re.search(r"(?<![源目标])表\s+(\w+)", message)
                        if m:
                            source = m.group(1)

        if not source:
            source = "orders"  # fallback

        # Business description: use the full message as description
        if not desc:
            desc = message

        return source, layer, desc
