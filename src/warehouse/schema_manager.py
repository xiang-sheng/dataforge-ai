# -*- coding: utf-8 -*-
"""
DataForge AI - Schema management for data warehouse layers.

Provides methods for creating and designing schemas for each warehouse layer
(ODS, DWD, DWS, ADS), including AI-assisted schema design and migration
script generation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.ai.provider import BaseAIProvider, ChatMessage
from src.ai.prompts import default_registry as prompt_registry
from src.warehouse.layers import WarehouseLayer, DEFAULT_LAYER_CONFIGS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ColumnSchema:
    """A column definition within a table schema.

    Attributes:
        name: Column name.
        data_type: SQL data type (dialect-specific).
        nullable: Whether NULLs are allowed.
        default_value: Default value expression, if any.
        comment: Business description of the column.
        is_partition_key: Whether this column is a partition key.
        is_primary_key: Whether this column is (part of) the primary key.
    """

    name: str
    data_type: str
    nullable: bool = True
    default_value: Optional[str] = None
    comment: str = ""
    is_partition_key: bool = False
    is_primary_key: bool = False


@dataclass
class TableSchema:
    """Complete schema definition for a single table.

    Attributes:
        table_name: Fully qualified table name.
        layer: The warehouse layer this table belongs to.
        columns: Ordered list of column definitions.
        comment: Table-level description.
        storage_format: Storage format (e.g. ORC, Parquet).
        compression: Compression codec.
        partition_keys: Column names used as partition keys.
        lifecycle_days: Data retention period.
        properties: Additional table properties (engine-specific).
    """

    table_name: str
    layer: WarehouseLayer = WarehouseLayer.DWD
    columns: List[ColumnSchema] = field(default_factory=list)
    comment: str = ""
    storage_format: str = "Parquet"
    compression: str = "snappy"
    partition_keys: List[str] = field(default_factory=list)
    lifecycle_days: int = 365
    properties: Dict[str, str] = field(default_factory=dict)

    def to_ddl(self, dialect: str = "Hive") -> str:
        """Generate a CREATE TABLE DDL statement for this schema.

        Args:
            dialect: The target SQL dialect.

        Returns:
            A DDL string.
        """
        lines: List[str] = []
        lines.append(f"CREATE TABLE IF NOT EXISTS {self.table_name} (")

        col_defs: List[str] = []
        for col in self.columns:
            if col.is_partition_key:
                continue  # Partition columns go after the main body
            parts = [f"    {col.name:<30s} {col.data_type}"]
            if not col.nullable:
                parts.append("NOT NULL")
            if col.default_value is not None:
                parts.append(f"DEFAULT {col.default_value}")
            if col.comment:
                parts.append(f"COMMENT '{col.comment}'")
            col_defs.append(" ".join(parts))

        lines.append(",\n".join(col_defs))
        lines.append(")")

        if self.comment:
            lines.append(f"COMMENT '{self.comment}'")

        # Partition clause
        if self.partition_keys:
            pk_defs = []
            for pk in self.partition_keys:
                col = next((c for c in self.columns if c.name == pk), None)
                dt = col.data_type if col else "STRING"
                cmt = col.comment if col and col.comment else ""
                pk_str = f"{pk} {dt}"
                if cmt:
                    pk_str += f" COMMENT '{cmt}'"
                pk_defs.append(pk_str)
            lines.append(f"PARTITIONED BY ({', '.join(pk_defs)})")

        # Storage
        if dialect.lower() in ("hive", "spark sql", "spark"):
            lines.append(f"STORED AS {self.storage_format.upper()}")

        # Lifecycle (Alibaba MaxCompute style)
        if self.lifecycle_days > 0:
            lines.append(f"LIFECYCLE {self.lifecycle_days}")

        lines.append(";")
        return "\n".join(lines)


@dataclass
class SourceConfig:
    """Configuration describing a data source for ODS table creation.

    Attributes:
        source_type: Type of source (mysql, postgresql, kafka, api, file, etc.).
        source_table: Source table or topic name.
        columns: Source column names and types.
        connection_id: Reference to the database connection.
        incremental_key: Column used for incremental extraction (e.g. update_time).
        extraction_strategy: ``full`` or ``incremental``.
    """

    source_type: str = "mysql"
    source_table: str = ""
    columns: List[Dict[str, str]] = field(default_factory=list)
    connection_id: str = ""
    incremental_key: Optional[str] = None
    extraction_strategy: str = "full"


@dataclass
class BusinessRule:
    """A business transformation rule applied during layer transitions.

    Attributes:
        rule_id: Unique identifier for the rule.
        description: Human-readable description.
        rule_type: Type of rule (cleansing, enrichment, derivation, filter, etc.).
        expression: SQL or Python expression implementing the rule.
        source_columns: Columns consumed by the rule.
        target_column: Column produced by the rule.
    """

    rule_id: str = ""
    description: str = ""
    rule_type: str = "cleansing"
    expression: str = ""
    source_columns: List[str] = field(default_factory=list)
    target_column: str = ""


@dataclass
class AggregationRule:
    """An aggregation rule for DWS layer design.

    Attributes:
        metric_name: Name of the aggregated metric.
        aggregation_function: SQL aggregate function (SUM, COUNT, AVG, etc.).
        source_column: The column being aggregated.
        group_by_columns: Columns to group by.
        time_granularity: Time granularity (1d, 7d, 30d, etc.).
    """

    metric_name: str = ""
    aggregation_function: str = "SUM"
    source_column: str = ""
    group_by_columns: List[str] = field(default_factory=list)
    time_granularity: str = "1d"


@dataclass
class MigrationScript:
    """A database migration script for schema evolution.

    Attributes:
        from_version: Source schema version identifier.
        to_version: Target schema version identifier.
        statements: Ordered list of SQL statements to execute.
        rollback_statements: Statements to undo the migration.
        description: Description of what the migration does.
    """

    from_version: str = ""
    to_version: str = ""
    statements: List[str] = field(default_factory=list)
    rollback_statements: List[str] = field(default_factory=list)
    description: str = ""

    def to_sql(self) -> str:
        """Render the migration as a SQL script string."""
        lines = [
            f"-- Migration: {self.from_version} -> {self.to_version}",
            f"-- Description: {self.description}",
            "",
        ]
        for stmt in self.statements:
            lines.append(f"{stmt};")
        return "\n".join(lines)

    def to_rollback_sql(self) -> str:
        """Render the rollback SQL script."""
        lines = [
            f"-- Rollback: {self.to_version} -> {self.from_version}",
            "",
        ]
        for stmt in reversed(self.rollback_statements):
            lines.append(f"{stmt};")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SchemaManager
# ---------------------------------------------------------------------------

class SchemaManager:
    """Schema management service for data warehouse layer design.

    Provides methods for creating ODS tables from source configs, designing
    DWD/DWS/ADS layer schemas, and generating migration scripts.

    Args:
        provider: An initialized ``BaseAIProvider`` for AI-assisted design.
        dialect: The target SQL dialect (default ``Hive``).

    Usage::

        manager = SchemaManager(provider, dialect="Hive")
        ddl = await manager.create_ods_table(source_config, target_db="dw")
        print(ddl)
    """

    def __init__(
        self,
        provider: BaseAIProvider,
        dialect: str = "Hive",
    ) -> None:
        self._provider = provider
        self._dialect = dialect

    # -- ODS Layer ---------------------------------------------------------

    async def create_ods_table(
        self,
        source_config: SourceConfig,
        target_db: str = "dw",
        naming_convention: str = "snake_case",
    ) -> str:
        """Generate a DDL statement for an ODS table based on a source config.

        The ODS table mirrors the source with minimal transformation, adding
        standard metadata columns (etl_time, source_system) and a date
        partition.

        Args:
            source_config: Description of the data source.
            target_db: Target database name.
            naming_convention: Naming convention for the ODS table.

        Returns:
            A DDL string for the ODS table.
        """
        # Build the ODS table name
        ods_table_name = f"ods_{source_config.source_table}"

        # Build column list from source config
        column_lines: List[str] = []
        for col in source_config.columns:
            col_name = col.get("name", "unknown")
            col_type = self._map_data_type(
                col.get("type", "VARCHAR"), source_config.source_type
            )
            col_comment = col.get("comment", "")
            col_str = f"    {col_name:<30s} {col_type}"
            if col_comment:
                col_str += f"  COMMENT '{col_comment}'"
            column_lines.append(col_str)

        # Add standard ODS metadata columns
        column_lines.append(
            "    etl_time                       TIMESTAMP  COMMENT 'ETL ingestion timestamp'"
        )
        column_lines.append(
            "    source_system                  STRING     COMMENT 'Source system identifier'"
        )

        columns_str = ",\n".join(column_lines)

        # Build partition clause
        partition_clause = "PARTITIONED BY (dt STRING COMMENT 'Data date partition (yyyyMMdd)')"

        # Build storage clause
        storage_clause = "STORED AS ORC"
        if self._dialect.lower() not in ("hive", "spark sql", "spark"):
            storage_clause = ""

        ddl = (
            f"CREATE TABLE IF NOT EXISTS {target_db}.{ods_table_name} (\n"
            f"{columns_str}\n"
            f")\n"
            f"COMMENT 'ODS mirror of {source_config.source_table} from {source_config.source_type}'\n"
            f"{partition_clause}\n"
        )
        if storage_clause:
            ddl += f"{storage_clause}\n"
        ddl += "LIFECYCLE 90\n;"

        return ddl

    # -- DWD Layer ---------------------------------------------------------

    async def design_dwd_layer(
        self,
        ods_tables: List[TableSchema],
        business_rules: Optional[List[BusinessRule]] = None,
        domain: str = "general",
    ) -> List[TableSchema]:
        """Design DWD (detail) layer schemas from ODS tables.

        Uses AI to analyze the ODS schemas and business rules, then generates
        cleansed and standardized DWD table schemas.

        Args:
            ods_tables: List of ODS table schemas to process.
            business_rules: Optional list of business transformation rules.
            domain: Business domain (e.g. "trade", "user", "finance").

        Returns:
            A list of ``TableSchema`` objects for the DWD layer.
        """
        # Build the prompt context
        ods_ddl = "\n\n".join(t.to_ddl(self._dialect) for t in ods_tables)
        rules_str = ""
        if business_rules:
            rules_str = "\n".join(
                f"- [{r.rule_type}] {r.description}: {r.expression}"
                for r in business_rules
            )

        template = prompt_registry.get("warehouse_layer_design")
        rendered = template.render(
            business_domain=f"DWD layer design for '{domain}' domain",
            data_sources=ods_ddl,
            reporting_requirements="Detail-level cleansed data for downstream aggregation",
            layers="DWD",
            naming_convention="dwd_{domain}_{entity}_{suffix} (e.g. dwd_trade_order_di)",
            storage_engine=self._dialect,
            extra_instructions=(
                f"Design only the DWD layer.\n"
                f"Business rules to apply:\n{rules_str}\n"
                f"Include data quality checks and NULL handling strategies."
            ),
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)

        # Parse the AI response into TableSchema objects
        return self._parse_table_schemas(response.content, WarehouseLayer.DWD)

    # -- DWS Layer ---------------------------------------------------------

    async def design_dws_layer(
        self,
        dwd_tables: List[TableSchema],
        aggregation_rules: Optional[List[AggregationRule]] = None,
        domain: str = "general",
    ) -> List[TableSchema]:
        """Design DWS (summary) layer schemas from DWD tables.

        Creates pre-aggregated tables at common granularity levels based on
        the provided aggregation rules.

        Args:
            dwd_tables: List of DWD table schemas to aggregate from.
            aggregation_rules: Rules defining what metrics to compute and at
                what granularity.
            domain: Business domain.

        Returns:
            A list of ``TableSchema`` objects for the DWS layer.
        """
        dwd_ddl = "\n\n".join(t.to_ddl(self._dialect) for t in dwd_tables)
        agg_str = ""
        if aggregation_rules:
            agg_str = "\n".join(
                f"- {r.metric_name}: {r.aggregation_function}({r.source_column}) "
                f"GROUP BY {', '.join(r.group_by_columns)} [{r.time_granularity}]"
                for r in aggregation_rules
            )

        prompt = (
            f"Design the DWS (Data Warehouse Summary) layer based on the "
            f"following DWD tables and aggregation rules.\n\n"
            f"## DWD Tables\n```sql\n{dwd_ddl}\n```\n\n"
            f"## Aggregation Rules\n{agg_str}\n\n"
            f"## Requirements\n"
            f"- Domain: {domain}\n"
            f"- Naming convention: dws_{{domain}}_{{entity}}_{{granularity}}\n"
            f"- Target dialect: {self._dialect}\n"
            f"- Include both additive and derived metrics.\n"
            f"- Specify partitioning strategy.\n"
            f"- Provide CREATE TABLE DDL for each DWS table.\n"
        )

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a data warehouse architect specializing in "
                    "summary layer design.  Produce clean, well-commented DDL."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages)
        return self._parse_table_schemas(response.content, WarehouseLayer.DWS)

    # -- ADS Layer ---------------------------------------------------------

    async def design_ads_layer(
        self,
        dws_tables: List[TableSchema],
        report_requirements: str,
        domain: str = "general",
    ) -> List[TableSchema]:
        """Design ADS (application) layer schemas from DWS tables.

        Creates application-specific tables tailored for dashboards, reports,
        or downstream services.

        Args:
            dws_tables: List of DWS table schemas to build from.
            report_requirements: Description of reporting needs and dashboards.
            domain: Business domain.

        Returns:
            A list of ``TableSchema`` objects for the ADS layer.
        """
        dws_ddl = "\n\n".join(t.to_ddl(self._dialect) for t in dws_tables)

        prompt = (
            f"Design the ADS (Application Data Store) layer for the following "
            f"reporting requirements.\n\n"
            f"## Available DWS Tables\n```sql\n{dws_ddl}\n```\n\n"
            f"## Reporting Requirements\n{report_requirements}\n\n"
            f"## Requirements\n"
            f"- Domain: {domain}\n"
            f"- Naming convention: ads_{{domain}}_{{report_name}}\n"
            f"- Target dialect: {self._dialect}\n"
            f"- Optimize for the specific query patterns described in the "
            f"reporting requirements.\n"
            f"- Include recommended indexes.\n"
            f"- Provide CREATE TABLE DDL for each ADS table.\n"
        )

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a data warehouse architect.  Design application-layer "
                    "tables optimized for the described reporting patterns."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages)
        return self._parse_table_schemas(response.content, WarehouseLayer.ADS)

    # -- Migration Scripts -------------------------------------------------

    async def generate_migration_script(
        self,
        from_schema: TableSchema,
        to_schema: TableSchema,
    ) -> MigrationScript:
        """Generate a migration script to evolve a table from one schema to another.

        Compares two table schemas and produces the ALTER TABLE statements
        needed to transform the source into the target.

        Args:
            from_schema: The current table schema.
            to_schema: The desired target schema.

        Returns:
            A ``MigrationScript`` with forward and rollback SQL.
        """
        statements: List[str] = []
        rollback_statements: List[str] = []

        # Build column maps
        from_cols = {c.name: c for c in from_schema.columns}
        to_cols = {c.name: c for c in to_schema.columns}

        # Detect added columns
        for col_name, col in to_cols.items():
            if col_name not in from_cols:
                stmt = (
                    f"ALTER TABLE {to_schema.table_name} ADD COLUMN "
                    f"{col.name} {col.data_type}"
                )
                if col.comment:
                    stmt += f" COMMENT '{col.comment}'"
                statements.append(stmt)
                rollback_statements.append(
                    f"ALTER TABLE {to_schema.table_name} DROP COLUMN {col.name}"
                )

        # Detect removed columns
        for col_name, col in from_cols.items():
            if col_name not in to_cols:
                statements.append(
                    f"ALTER TABLE {to_schema.table_name} DROP COLUMN {col.name}"
                )
                rollback_stmt = (
                    f"ALTER TABLE {to_schema.table_name} ADD COLUMN "
                    f"{col.name} {col.data_type}"
                )
                if col.comment:
                    rollback_stmt += f" COMMENT '{col.comment}'"
                rollback_statements.append(rollback_stmt)

        # Detect type changes
        for col_name in set(from_cols.keys()) & set(to_cols.keys()):
            from_col = from_cols[col_name]
            to_col = to_cols[col_name]
            if from_col.data_type != to_col.data_type:
                statements.append(
                    f"ALTER TABLE {to_schema.table_name} "
                    f"ALTER COLUMN {col_name} TYPE {to_col.data_type}"
                )
                rollback_statements.append(
                    f"ALTER TABLE {to_schema.table_name} "
                    f"ALTER COLUMN {col_name} TYPE {from_col.data_type}"
                )

        # Detect comment changes
        for col_name in set(from_cols.keys()) & set(to_cols.keys()):
            from_col = from_cols[col_name]
            to_col = to_cols[col_name]
            if from_col.comment != to_col.comment and to_col.comment:
                statements.append(
                    f"ALTER TABLE {to_schema.table_name} "
                    f"ALTER COLUMN {col_name} COMMENT '{to_col.comment}'"
                )

        # Detect table comment change
        if from_schema.comment != to_schema.comment and to_schema.comment:
            statements.append(
                f"ALTER TABLE {to_schema.table_name} COMMENT '{to_schema.comment}'"
            )

        description_parts: List[str] = []
        added = set(to_cols.keys()) - set(from_cols.keys())
        removed = set(from_cols.keys()) - set(to_cols.keys())
        changed = {
            c for c in (set(from_cols.keys()) & set(to_cols.keys()))
            if from_cols[c].data_type != to_cols[c].data_type
        }
        if added:
            description_parts.append(f"Added columns: {', '.join(sorted(added))}")
        if removed:
            description_parts.append(f"Removed columns: {', '.join(sorted(removed))}")
        if changed:
            description_parts.append(f"Type changes: {', '.join(sorted(changed))}")
        if not description_parts:
            description_parts.append("No schema changes detected")

        return MigrationScript(
            from_version=from_schema.table_name,
            to_version=to_schema.table_name,
            statements=statements,
            rollback_statements=rollback_statements,
            description="; ".join(description_parts),
        )

    # -- Internal helpers --------------------------------------------------

    def _map_data_type(self, source_type: str, source_system: str) -> str:
        """Map a source data type to the target dialect's equivalent.

        Args:
            source_type: The source system's data type string.
            source_system: Identifier of the source system (mysql, postgresql, etc.).

        Returns:
            The mapped data type string for the target dialect.
        """
        type_upper = source_type.upper()

        # Common mappings to Hive / Spark types
        _mappings: Dict[str, Dict[str, str]] = {
            "mysql": {
                "INT": "INT",
                "INTEGER": "INT",
                "BIGINT": "BIGINT",
                "SMALLINT": "SMALLINT",
                "TINYINT": "TINYINT",
                "FLOAT": "FLOAT",
                "DOUBLE": "DOUBLE",
                "DECIMAL": "DECIMAL(18,6)",
                "VARCHAR": "STRING",
                "TEXT": "STRING",
                "DATETIME": "TIMESTAMP",
                "TIMESTAMP": "TIMESTAMP",
                "DATE": "DATE",
                "BOOLEAN": "BOOLEAN",
                "JSON": "STRING",
            },
            "postgresql": {
                "INTEGER": "INT",
                "BIGINT": "BIGINT",
                "SMALLINT": "SMALLINT",
                "REAL": "FLOAT",
                "DOUBLE PRECISION": "DOUBLE",
                "NUMERIC": "DECIMAL(18,6)",
                "VARCHAR": "STRING",
                "TEXT": "STRING",
                "TIMESTAMP": "TIMESTAMP",
                "TIMESTAMPTZ": "TIMESTAMP",
                "DATE": "DATE",
                "BOOLEAN": "BOOLEAN",
                "JSONB": "STRING",
                "UUID": "STRING",
            },
        }

        system_map = _mappings.get(source_system.lower(), {})

        # Try exact match first
        if type_upper in system_map:
            return system_map[type_upper]

        # Try prefix match (e.g. "VARCHAR(255)" -> "STRING")
        for prefix, target in system_map.items():
            if type_upper.startswith(prefix):
                return target

        # Fallback: return STRING for unknown types
        logger.warning(
            "Unknown data type '%s' from '%s', defaulting to STRING",
            source_type,
            source_system,
        )
        return "STRING"

    def _parse_table_schemas(
        self,
        ai_response: str,
        layer: WarehouseLayer,
    ) -> List[TableSchema]:
        """Parse AI response text into structured TableSchema objects.

        Extracts CREATE TABLE statements from the AI response and parses them
        into ``TableSchema`` data structures.

        Args:
            ai_response: The raw text response from the AI provider.
            layer: The warehouse layer to assign parsed tables to.

        Returns:
            A list of parsed ``TableSchema`` objects.
        """
        import re

        schemas: List[TableSchema] = []

        # Find all CREATE TABLE blocks
        ddl_blocks = re.findall(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)\s*\((.*?)\)",
            ai_response,
            re.DOTALL | re.IGNORECASE,
        )

        for table_name, body in ddl_blocks:
            columns: List[ColumnSchema] = []
            for line in body.splitlines():
                line = line.strip().rstrip(",")
                if not line or line.startswith("--"):
                    continue
                # Parse column: name type [NOT NULL] [COMMENT '...']
                col_match = re.match(
                    r"(\w+)\s+(\w+(?:\([^)]*\))?)\s*(.*)",
                    line,
                    re.IGNORECASE,
                )
                if col_match:
                    col_name = col_match.group(1)
                    col_type = col_match.group(2)
                    rest = col_match.group(3)
                    nullable = "NOT NULL" not in rest.upper()
                    comment_match = re.search(r"COMMENT\s+'([^']*)'", rest, re.IGNORECASE)
                    comment = comment_match.group(1) if comment_match else ""

                    columns.append(
                        ColumnSchema(
                            name=col_name,
                            data_type=col_type,
                            nullable=nullable,
                            comment=comment,
                        )
                    )

            # Extract partition info
            partition_match = re.search(
                r"PARTITIONED\s+BY\s*\(([^)]+)\)",
                ai_response,
                re.IGNORECASE,
            )
            partition_keys: List[str] = []
            if partition_match:
                pk_body = partition_match.group(1)
                partition_keys = re.findall(r"(\w+)\s+\w+", pk_body)

            schemas.append(
                TableSchema(
                    table_name=table_name.strip("`\""),
                    layer=layer,
                    columns=columns,
                    partition_keys=partition_keys,
                )
            )

        return schemas
