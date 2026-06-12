"""
DataForge AI - Data modeling advisor.

Provides AI-powered data modeling recommendations including dimensional model
design, schema review, partitioning strategy, and index recommendations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from src.ai.prompts import default_registry as prompt_registry
from src.ai.provider import BaseAIProvider, ChatMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class SchemaType(StrEnum):
    """Supported dimensional-model schema types."""

    STAR = "star"
    SNOWFLAKE = "snowflake"
    DATA_VAULT = "data_vault"
    ONE_DATA = "one_data"


class TableType(StrEnum):
    """Warehouse table classification."""

    FACT = "fact"
    DIMENSION = "dimension"
    BRIDGE = "bridge"
    AGGREGATE = "aggregate"
    STAGING = "staging"


@dataclass
class ColumnDef:
    """Column definition within a modeled table.

    Attributes:
        name: Column name.
        data_type: SQL data type.
        nullable: Whether NULL values are allowed.
        description: Business description of the column.
        is_primary_key: Whether this column is (part of) the primary key.
        is_foreign_key: Whether this column references another table.
        references: If ``is_foreign_key``, the referenced ``table.column``.
    """

    name: str
    data_type: str
    nullable: bool = True
    description: str = ""
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str = ""


@dataclass
class TableDesign:
    """A single table produced by the modeling advisor.

    Attributes:
        table_name: Fully qualified table name.
        table_type: Classification (fact, dimension, etc.).
        grain: The grain of the table (for fact tables).
        columns: Ordered list of column definitions.
        partition_keys: Columns used for partitioning.
        description: Business description of the table.
        rationale: Design rationale explaining key decisions.
    """

    table_name: str
    table_type: TableType = TableType.FACT
    grain: str = ""
    columns: list[ColumnDef] = field(default_factory=list)
    partition_keys: list[str] = field(default_factory=list)
    description: str = ""
    rationale: str = ""


@dataclass
class ModelingRecommendation:
    """Complete modeling recommendation returned by the advisor.

    Attributes:
        tables: List of designed tables.
        summary: High-level summary of the modeling approach.
        trade_offs: Identified trade-offs and their implications.
        naming_convention: The naming convention applied.
        raw_response: The full AI model response for reference.
    """

    tables: list[TableDesign] = field(default_factory=list)
    summary: str = ""
    trade_offs: list[str] = field(default_factory=list)
    naming_convention: str = ""
    raw_response: str = ""


@dataclass
class IndexRecommendation:
    """A recommended index for a table.

    Attributes:
        table_name: The table the index applies to.
        index_name: Suggested index name.
        columns: Ordered columns in the index.
        index_type: Type of index (B-tree, bitmap, etc.).
        rationale: Why this index is recommended.
    """

    table_name: str
    index_name: str
    columns: list[str]
    index_type: str = "btree"
    rationale: str = ""


@dataclass
class PartitionRecommendation:
    """A recommended partitioning strategy for a table.

    Attributes:
        table_name: The table to partition.
        partition_columns: Columns to partition on.
        granularity: Partition granularity description.
        strategy_description: Full description of the strategy.
        ddl_snippet: DDL snippet to implement the partitioning.
    """

    table_name: str
    partition_columns: list[str]
    granularity: str = "daily"
    strategy_description: str = ""
    ddl_snippet: str = ""


@dataclass
class SchemaReviewResult:
    """Result of reviewing an existing schema.

    Attributes:
        overall_score: Quality score from 1 (poor) to 10 (excellent).
        findings: List of individual findings / issues.
        recommendations: Actionable improvement recommendations.
        raw_response: The full AI model response.
    """

    overall_score: float = 0.0
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    raw_response: str = ""


# ---------------------------------------------------------------------------
# ModelAdvisor
# ---------------------------------------------------------------------------

class ModelAdvisor:
    """AI-powered data modeling advisor for data warehouse design.

    Provides methods for suggesting schemas, designing dimensional models,
    recommending partitioning and indexing strategies, and reviewing existing
    models.

    Args:
        provider: An initialized ``BaseAIProvider`` instance.

    Usage::

        advisor = ModelAdvisor(provider)
        recommendation = await advisor.suggest_schema(
            requirements="E-commerce order analytics",
            existing_tables=["raw_orders", "raw_products"],
        )
        for table in recommendation.tables:
            print(table.table_name, table.table_type)
    """

    def __init__(self, provider: BaseAIProvider) -> None:
        self._provider = provider

    # -- Public API ---------------------------------------------------------

    async def suggest_schema(
        self,
        requirements: str,
        existing_tables: list[str] | None = None,
        schema_type: SchemaType = SchemaType.STAR,
        naming_convention: str = "snake_case",
        query_patterns: str = "Aggregation, filtering, time-series analysis",
        extra_instructions: str = "",
    ) -> ModelingRecommendation:
        """Suggest a complete data model based on business requirements.

        Args:
            requirements: Description of the business process and analytical
                needs.
            existing_tables: List of existing table names / DDLs to consider.
            schema_type: The target schema type (star, snowflake, etc.).
            naming_convention: Naming convention to follow.
            query_patterns: Common query patterns the model should optimize for.
            extra_instructions: Additional instructions for the AI model.

        Returns:
            A ``ModelingRecommendation`` with table designs and rationale.
        """
        template = prompt_registry.get("data_modeling")
        rendered = template.render(
            business_process=requirements,
            entities="See existing tables and requirements",
            existing_tables="\n".join(existing_tables) if existing_tables else "None",
            schema_type=schema_type.value,
            query_patterns=query_patterns,
            naming_convention=naming_convention,
            extra_instructions=extra_instructions,
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)

        # Parse the response into structured tables
        tables = self._parse_table_designs(response.content)

        return ModelingRecommendation(
            tables=tables,
            summary=self._extract_section(response.content, "summary"),
            trade_offs=self._extract_list_section(response.content, "trade"),
            naming_convention=naming_convention,
            raw_response=response.content,
        )

    async def design_dimension_model(
        self,
        business_process: str,
        entities: list[str],
        schema_type: SchemaType = SchemaType.STAR,
        naming_convention: str = "snake_case",
    ) -> ModelingRecommendation:
        """Design a dimensional model focused on a specific business process.

        This is a more targeted variant of ``suggest_schema`` that concentrates
        on identifying the correct grain, facts, and dimensions for a given
        business process.

        Args:
            business_process: The business process to model (e.g. "order
                fulfillment", "customer onboarding").
            entities: Key business entities involved (e.g. ["order", "customer",
                "product"]).
            schema_type: Target schema type.
            naming_convention: Naming convention for table/column names.

        Returns:
            A ``ModelingRecommendation`` with the dimensional model design.
        """
        entities_str = ", ".join(entities)
        instructions = (
            f"Focus specifically on the '{business_process}' process.  "
            f"Key entities: {entities_str}.  "
            f"Identify the correct grain first, then derive facts and dimensions."
        )

        return await self.suggest_schema(
            requirements=business_process,
            schema_type=schema_type,
            naming_convention=naming_convention,
            extra_instructions=instructions,
        )

    async def suggest_partitioning(
        self,
        table_schema: str,
        query_patterns: str,
        estimated_rows: str = "unknown",
        daily_volume: str = "unknown",
        retention_policy: str = "Keep all data",
        dialect: str = "PostgreSQL",
    ) -> PartitionRecommendation:
        """Recommend a partitioning strategy for a given table.

        Args:
            table_schema: The ``CREATE TABLE`` DDL of the table.
            query_patterns: Description of common query patterns.
            estimated_rows: Estimated total number of rows.
            daily_volume: Estimated daily row ingestion volume.
            retention_policy: Data retention / lifecycle policy.
            dialect: Target database dialect.

        Returns:
            A ``PartitionRecommendation`` with the suggested strategy.
        """
        template = prompt_registry.get("partitioning_strategy")
        rendered = template.render(
            table_schema=table_schema,
            query_patterns=query_patterns,
            estimated_rows=estimated_rows,
            daily_volume=daily_volume,
            retention_policy=retention_policy,
            dialect=dialect,
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)

        return PartitionRecommendation(
            table_name=self._extract_table_name_from_ddl(table_schema),
            partition_columns=[],  # Parsed from response in production
            granularity=self._infer_granularity(response.content),
            strategy_description=response.content,
            ddl_snippet=self._extract_ddl_snippet(response.content),
        )

    async def suggest_indexing(
        self,
        table_schema: str,
        query_patterns: str,
        dialect: str = "PostgreSQL",
    ) -> list[IndexRecommendation]:
        """Recommend indexes for a table based on query patterns.

        Args:
            table_schema: The ``CREATE TABLE`` DDL of the table.
            query_patterns: Description of common query patterns and filters.
            dialect: Target database dialect.

        Returns:
            A list of ``IndexRecommendation`` objects.
        """
        prompt = (
            f"Given the following table schema and common query patterns, "
            f"recommend indexes to optimize query performance.\n\n"
            f"## Table Schema\n```sql\n{table_schema}\n```\n\n"
            f"## Query Patterns\n{query_patterns}\n\n"
            f"## Dialect\n{dialect}\n\n"
            f"For each recommended index, provide:\n"
            f"1. Index name\n"
            f"2. Columns included\n"
            f"3. Index type (btree, bitmap, hash, etc.)\n"
            f"4. Rationale\n"
            f"5. CREATE INDEX DDL\n"
        )

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a database performance tuning expert.  Provide "
                    "precise, actionable index recommendations."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages)

        # Parse the response into IndexRecommendation objects
        return self._parse_index_recommendations(
            response.content,
            table_name=self._extract_table_name_from_ddl(table_schema),
        )

    async def review_model(
        self,
        schema: str,
    ) -> SchemaReviewResult:
        """Review an existing data model and provide improvement suggestions.

        Args:
            schema: The DDL statements of the schema to review.

        Returns:
            A ``SchemaReviewResult`` with findings and recommendations.
        """
        template = prompt_registry.get("schema_review")
        rendered = template.render(schema_ddl=schema)

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)

        return SchemaReviewResult(
            overall_score=self._extract_score(response.content),
            findings=self._extract_list_section(response.content, "finding"),
            recommendations=self._extract_list_section(response.content, "recommend"),
            raw_response=response.content,
        )

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    def _parse_table_designs(response_text: str) -> list[TableDesign]:
        """Parse the AI response to extract structured table designs.

        This is a best-effort parser that looks for common patterns in the
        model output (headers, bullet lists, etc.).

        Args:
            response_text: The raw AI response text.

        Returns:
            A list of ``TableDesign`` objects.
        """
        tables: list[TableDesign] = []
        current_table: TableDesign | None = None

        for line in response_text.splitlines():
            stripped = line.strip()

            # Detect table header patterns like "## Fact Table: fact_orders"
            # or "### dim_customer"
            if stripped.startswith("#") and any(
                kw in stripped.lower() for kw in ("fact", "dim", "bridge", "agg", "table")
            ):
                if current_table:
                    tables.append(current_table)

                table_type = TableType.FACT
                if "dim" in stripped.lower():
                    table_type = TableType.DIMENSION
                elif "agg" in stripped.lower():
                    table_type = TableType.AGGREGATE
                elif "bridge" in stripped.lower():
                    table_type = TableType.BRIDGE

                # Extract table name: last word after ":" or last word in header
                name = stripped
                if ":" in name:
                    name = name.split(":")[-1].strip()
                name = name.lstrip("#").strip().split()[0] if name else "unnamed"

                current_table = TableDesign(
                    table_name=name,
                    table_type=table_type,
                    description=stripped,
                )

        if current_table:
            tables.append(current_table)

        return tables

    @staticmethod
    def _extract_section(text: str, keyword: str) -> str:
        """Extract a text section by keyword from the AI response."""
        lines = text.splitlines()
        collecting = False
        result_lines: list[str] = []
        for line in lines:
            if keyword.lower() in line.lower() and line.strip().startswith("#"):
                collecting = True
                continue
            if collecting:
                if line.strip().startswith("#"):
                    break
                result_lines.append(line)
        return "\n".join(result_lines).strip()

    @staticmethod
    def _extract_list_section(text: str, keyword: str) -> list[str]:
        """Extract bullet-list items from a section matching *keyword*."""
        lines = text.splitlines()
        collecting = False
        items: list[str] = []
        for line in lines:
            if keyword.lower() in line.lower() and line.strip().startswith("#"):
                collecting = True
                continue
            if collecting:
                stripped = line.strip()
                if stripped.startswith("#"):
                    break
                if stripped.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.")):
                    items.append(stripped.lstrip("-*0123456789. ").strip())
        return items

    @staticmethod
    def _extract_table_name_from_ddl(ddl: str) -> str:
        """Extract the table name from a CREATE TABLE statement."""
        import re

        match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)", ddl, re.IGNORECASE)
        if match:
            return match.group(1).strip("(").strip(";")
        return "unknown_table"

    @staticmethod
    def _infer_granularity(text: str) -> str:
        """Infer partition granularity from the response text."""
        text_lower = text.lower()
        for gran in ("hourly", "daily", "weekly", "monthly", "yearly"):
            if gran in text_lower:
                return gran
        return "daily"

    @staticmethod
    def _extract_ddl_snippet(text: str) -> str:
        """Extract the first SQL code block from the response."""
        import re

        match = re.search(r"```sql\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_score(text: str) -> float:
        """Extract a numeric score (1-10) from the review response."""
        import re

        match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", text)
        if match:
            return float(match.group(1))
        # Fallback: look for "score: X"
        match = re.search(r"score[:\s]+(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return 5.0  # Default middle score

    @staticmethod
    def _parse_index_recommendations(
        text: str,
        table_name: str,
    ) -> list[IndexRecommendation]:
        """Parse index recommendations from the AI response."""
        import re

        recommendations: list[IndexRecommendation] = []

        # Look for CREATE INDEX statements
        index_pattern = re.compile(
            r"CREATE\s+(?:(UNIQUE)\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)\s+"
            r"ON\s+\S+\s*\(([^)]+)\)",
            re.IGNORECASE,
        )
        for match in index_pattern.finditer(text):
            is_unique = bool(match.group(1))
            idx_name = match.group(2)
            columns = [c.strip() for c in match.group(3).split(",")]
            idx_type = "unique_btree" if is_unique else "btree"

            recommendations.append(
                IndexRecommendation(
                    table_name=table_name,
                    index_name=idx_name,
                    columns=columns,
                    index_type=idx_type,
                    rationale="Extracted from AI recommendation",
                )
            )

        return recommendations
