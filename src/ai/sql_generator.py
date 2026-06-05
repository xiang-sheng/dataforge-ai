# -*- coding: utf-8 -*-
"""
DataForge AI - SQL generation service.

Provides high-level SQL generation, explanation, and cross-dialect translation
powered by the AI provider layer and the prompt template registry.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.ai.provider import BaseAIProvider, ChatMessage
from src.ai.prompts import default_registry as prompt_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & data models
# ---------------------------------------------------------------------------

class SQLDialect(str, Enum):
    """Supported SQL dialects."""

    POSTGRESQL = "PostgreSQL"
    MYSQL = "MySQL"
    HIVE = "Hive"
    SPARK_SQL = "Spark SQL"
    CLICKHOUSE = "ClickHouse"
    STARROCKS = "StarRocks"
    DORIS = "Doris"
    ORACLE = "Oracle"
    SQL_SERVER = "SQL Server"
    BIGQUERY = "BigQuery"
    SNOWFLAKE = "Snowflake"
    REDSHIFT = "Redshift"
    TRINO = "Trino"


@dataclass
class SchemaContext:
    """Structured representation of database schema information.

    Used to give the AI model full awareness of the existing tables, columns,
    relationships, and sample data when generating SQL.

    Attributes:
        tables: A list of table descriptors.
        relationships: Foreign-key relationships expressed as
            ``(from_table, from_col, to_table, to_col)`` tuples.
        sample_values: Optional mapping of ``table.column`` to a list of
            representative sample values.
    """

    @dataclass
    class ColumnInfo:
        """Descriptor for a single column.

        Attributes:
            name: Column name.
            data_type: SQL data type string.
            nullable: Whether the column allows NULLs.
            comment: Optional column description / comment.
        """

        name: str
        data_type: str
        nullable: bool = True
        comment: str = ""

    @dataclass
    class TableInfo:
        """Descriptor for a single table.

        Attributes:
            schema_name: The database schema (namespace) the table belongs to.
            table_name: The table name.
            columns: Ordered list of column descriptors.
            primary_key: Column names forming the primary key.
            comment: Optional table description / comment.
        """

        schema_name: str
        table_name: str
        columns: List["SchemaContext.ColumnInfo"] = field(default_factory=list)
        primary_key: List[str] = field(default_factory=list)
        comment: str = ""

        @property
        def full_name(self) -> str:
            """Return ``schema.table`` qualified name."""
            if self.schema_name:
                return f"{self.schema_name}.{self.table_name}"
            return self.table_name

    tables: List[TableInfo] = field(default_factory=list)
    relationships: List[tuple[str, str, str, str]] = field(default_factory=list)
    sample_values: Dict[str, List[str]] = field(default_factory=dict)

    def to_ddl_string(self) -> str:
        """Serialize the schema context into a DDL-like string for prompt injection.

        Returns:
            A multi-line string resembling ``CREATE TABLE`` statements.
        """
        lines: List[str] = []
        for table in self.tables:
            cols = ",\n    ".join(
                f"{c.name} {c.data_type}"
                + ("" if c.nullable else " NOT NULL")
                + (f"  -- {c.comment}" if c.comment else "")
                for c in table.columns
            )
            pk = ""
            if table.primary_key:
                pk = f",\n    PRIMARY KEY ({', '.join(table.primary_key)})"
            comment_line = f"-- {table.comment}\n" if table.comment else ""
            lines.append(
                f"{comment_line}"
                f"CREATE TABLE {table.full_name} (\n    {cols}{pk}\n);"
            )

        # Append FK relationships
        if self.relationships:
            lines.append("\n-- Foreign Key Relationships:")
            for ft, fc, tt, tc in self.relationships:
                lines.append(f"-- {ft}.{fc} -> {tt}.{tc}")

        # Append sample values
        if self.sample_values:
            lines.append("\n-- Sample Values:")
            for col_key, values in self.sample_values.items():
                lines.append(f"-- {col_key}: {', '.join(str(v) for v in values[:5])}")

        return "\n\n".join(lines)


@dataclass
class SQLGenerationResult:
    """Result object returned by SQL generation methods.

    Attributes:
        sql: The generated SQL string.
        explanation: Optional human-readable explanation of the query.
        assumptions: Assumptions made when the question was ambiguous.
        dialect: The SQL dialect the query was generated for.
        raw_response: The full text response from the AI model.
        confidence: A self-assessed confidence score from 0.0 to 1.0.
    """

    sql: str
    explanation: str = ""
    assumptions: List[str] = field(default_factory=list)
    dialect: str = ""
    raw_response: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Helper: extract SQL from AI response
# ---------------------------------------------------------------------------

def _extract_sql_from_response(response_text: str) -> str:
    """Extract the SQL code block from the AI model's markdown response.

    Looks for content inside ```sql ... ``` fenced code blocks.  Falls back
    to returning the full response if no code block is found.

    Args:
        response_text: The raw text response from the AI model.

    Returns:
        The extracted SQL string, stripped of leading/trailing whitespace.
    """
    pattern = r"```sql\s*\n(.*?)```"
    matches = re.findall(pattern, response_text, re.DOTALL)
    if matches:
        return "\n".join(m.strip() for m in matches)

    # Fallback: look for any code block
    fallback_pattern = r"```\s*\n(.*?)```"
    fallback_matches = re.findall(fallback_pattern, response_text, re.DOTALL)
    if fallback_matches:
        return "\n".join(m.strip() for m in fallback_matches)

    return response_text.strip()


def _extract_assumptions(response_text: str) -> List[str]:
    """Extract assumption lines from the AI response.

    Looks for lines starting with ``-`` or ``*`` under an 'Assumptions' heading.

    Args:
        response_text: The raw text response.

    Returns:
        A list of assumption strings.
    """
    assumptions: List[str] = []
    in_assumptions = False
    for line in response_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("assumption"):
            in_assumptions = True
            continue
        if in_assumptions:
            if stripped.startswith(("-", "*")):
                assumptions.append(stripped.lstrip("-* ").strip())
            elif stripped == "":
                in_assumptions = False
    return assumptions


# ---------------------------------------------------------------------------
# SQLGenerator
# ---------------------------------------------------------------------------

class SQLGenerator:
    """High-level SQL generation, explanation, and translation service.

    Wraps the AI provider and prompt templates to offer context-aware SQL
    operations for the DataForge AI platform.

    Args:
        provider: An initialized ``BaseAIProvider`` instance.
        default_dialect: The default SQL dialect to use when none is specified.

    Usage::

        generator = SQLGenerator(provider, default_dialect=SQLDialect.HIVE)
        result = await generator.generate_sql(
            natural_language="Show me the top 10 customers by revenue",
            db_schema=schema_context,
        )
        print(result.sql)
    """

    def __init__(
        self,
        provider: BaseAIProvider,
        default_dialect: SQLDialect = SQLDialect.POSTGRESQL,
    ) -> None:
        self._provider = provider
        self._default_dialect = default_dialect

    # -- Public API ---------------------------------------------------------

    async def generate_sql(
        self,
        natural_language: str,
        db_schema: Optional[SchemaContext] = None,
        dialect: Optional[SQLDialect] = None,
        extra_instructions: str = "",
        include_explanation: bool = False,
    ) -> SQLGenerationResult:
        """Generate a SQL query from a natural-language question.

        Args:
            natural_language: The user's question in plain English (or other
                language).
            db_schema: Optional schema context to make the generation
                context-aware.  When ``None``, a generic prompt is used.
            dialect: Target SQL dialect.  Defaults to the instance's
                ``default_dialect``.
            extra_instructions: Additional instructions appended to the prompt
                (e.g. "Use CTEs for readability").
            include_explanation: If ``True``, request the model to also
                explain the generated query.

        Returns:
            A ``SQLGenerationResult`` with the generated SQL and metadata.
        """
        target_dialect = dialect or self._default_dialect
        schema_str = db_schema.to_ddl_string() if db_schema else "(No schema provided)"

        instructions = extra_instructions
        if include_explanation:
            instructions += "\n- After the SQL, provide a brief plain-language explanation."

        template = prompt_registry.get("sql_generation")
        rendered = template.render(
            dialect=target_dialect.value,
            schema=schema_str,
            question=natural_language,
            extra_instructions=instructions,
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)

        sql = _extract_sql_from_response(response.content)
        assumptions = _extract_assumptions(response.content)

        explanation = ""
        if include_explanation:
            # Attempt to extract explanation (text outside code blocks)
            parts = re.split(r"```(?:sql)?\s*\n.*?```", response.content, flags=re.DOTALL)
            explanation = "\n".join(p.strip() for p in parts if p.strip())

        return SQLGenerationResult(
            sql=sql,
            explanation=explanation,
            assumptions=assumptions,
            dialect=target_dialect.value,
            raw_response=response.content,
            confidence=0.85,  # Heuristic baseline; refined by validation layer
        )

    async def explain_sql(
        self,
        sql: str,
        dialect: Optional[SQLDialect] = None,
    ) -> str:
        """Generate a plain-language explanation of a SQL query.

        Args:
            sql: The SQL query to explain.
            dialect: The dialect of the SQL.  Defaults to the instance default.

        Returns:
            A human-readable explanation string.
        """
        target_dialect = dialect or self._default_dialect
        template = prompt_registry.get("sql_explanation")
        rendered = template.render(dialect=target_dialect.value, sql=sql)

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)
        return response.content

    async def translate_sql(
        self,
        sql: str,
        source_dialect: SQLDialect,
        target_dialect: SQLDialect,
    ) -> str:
        """Translate a SQL query from one dialect to another.

        Args:
            sql: The original SQL query.
            source_dialect: The dialect the query is currently written in.
            target_dialect: The dialect to translate the query into.

        Returns:
            The translated SQL string.
        """
        template = prompt_registry.get("sql_translation")
        rendered = template.render(
            source_dialect=source_dialect.value,
            target_dialect=target_dialect.value,
            sql=sql,
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)
        return _extract_sql_from_response(response.content)

    async def generate_ddl(
        self,
        requirements: str,
        dialect: Optional[SQLDialect] = None,
        naming_convention: str = "snake_case",
        warehouse_layer: str = "DWD",
    ) -> str:
        """Generate DDL statements from natural-language table requirements.

        Args:
            requirements: Description of the table(s) to create.
            dialect: Target SQL dialect.
            naming_convention: Naming convention to follow (e.g. ``snake_case``,
                ``camelCase``).
            warehouse_layer: The warehouse layer the table belongs to
                (ODS, DWD, DWS, ADS).

        Returns:
            DDL string wrapped in a SQL code block.
        """
        target_dialect = dialect or self._default_dialect
        template = prompt_registry.get("ddl_generation")
        rendered = template.render(
            dialect=target_dialect.value,
            requirements=requirements,
            naming_convention=naming_convention,
            warehouse_layer=warehouse_layer,
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)
        return _extract_sql_from_response(response.content)

    async def review_and_improve(
        self,
        sql: str,
        dialect: Optional[SQLDialect] = None,
        execution_plan: str = "N/A",
        table_stats: str = "N/A",
        optimization_goals: str = "Reduce execution time and resource usage",
    ) -> str:
        """Review a SQL query and suggest optimizations.

        This is a convenience wrapper that delegates to the optimization
        prompt template.

        Args:
            sql: The SQL query to review.
            dialect: The dialect of the query.
            execution_plan: Optional ``EXPLAIN`` output.
            table_stats: Optional table statistics summary.
            optimization_goals: What to optimize for (speed, memory, etc.).

        Returns:
            A detailed analysis and rewrite suggestions.
        """
        target_dialect = dialect or self._default_dialect
        template = prompt_registry.get("sql_optimization")
        rendered = template.render(
            dialect=target_dialect.value,
            sql=sql,
            execution_plan=execution_plan,
            table_stats=table_stats,
            optimization_goals=optimization_goals,
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)
        return response.content
