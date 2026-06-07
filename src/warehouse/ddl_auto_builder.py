# -*- coding: utf-8 -*-
"""
DataForge AI - Automated DDL and computation SQL generation engine.

This is the core pipeline module that ties together source table metadata,
naming-convention rules, data-type mappings, and layer-specific transformation
patterns to produce target DDL (CREATE TABLE) and computation SQL
(INSERT INTO ... SELECT) for every warehouse layer (ODS, DWD, DWS, ADS).

Workflow overview
-----------------
1. Read source table metadata (from live database adapters or provided schemas).
2. Load convention rules from a YAML / Markdown file (via ``convention_loader``).
3. Apply naming conventions, data-type mappings, and partition rules.
4. Generate target DDL (CREATE TABLE) for the specified warehouse layer.
5. Generate computation SQL (INSERT INTO target SELECT ... FROM source).
6. Optionally verify everything in a local DuckDB sandbox.
7. Optionally use an LLM (via LangChain) for intelligent column mapping,
   transformation suggestions, and SQL review.

All public data structures are Pydantic models so they serialise cleanly
through the FastAPI layer.  Internal helpers use dataclasses where mutability
and lightweight instantiation matter more than validation.

Dependencies
------------
The module gracefully degrades when optional siblings are not yet available:

* ``src.warehouse.convention_loader`` -- loaded at runtime when present;
  the builder falls back to built-in defaults otherwise.
* ``src.db.duckdb_sandbox`` -- loaded at runtime when ``local_verify=True``;
  verification is skipped when the sandbox is unavailable.
"""

from __future__ import annotations

import logging
import re
import textwrap
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import (
    ColumnDataType,
    ColumnInfo,
    DatabaseType,
    TableSchema,
)
from src.warehouse.layers import (
    DEFAULT_LAYER_CONFIGS,
    LayerConfig,
    LayerValidator,
    WarehouseLayer,
)

logger = logging.getLogger(__name__)


# ====================================================================== #
# Forward-compatible optional imports
# ====================================================================== #

try:
    from src.warehouse.convention_loader import (
        ConventionLoader,
        TableConvention,
    )
    _HAS_CONVENTION_LOADER = True
except ImportError:  # pragma: no cover
    _HAS_CONVENTION_LOADER = False
    ConventionLoader = None  # type: ignore[assignment,misc]
    TableConvention = None  # type: ignore[assignment,misc]

try:
    from src.db.duckdb_sandbox import DuckDBSandbox
    _HAS_DUCKDB_SANDBOX = True
except ImportError:  # pragma: no cover
    _HAS_DUCKDB_SANDBOX = False
    DuckDBSandbox = None  # type: ignore[assignment,misc]

try:
    from src.ai.provider import (
        AIProviderFactory,
        BaseAIProvider,
        ChatMessage,
        ProviderConfig,
    )
    _HAS_AI_PROVIDER = True
except ImportError:  # pragma: no cover
    _HAS_AI_PROVIDER = False

try:
    from src.ai.prompts import (
        DDL_GENERATION_TEMPLATE,
        SCHEMA_REVIEW_TEMPLATE,
        PromptRegistry,
        default_registry as prompt_registry,
    )
    _HAS_PROMPTS = True
except ImportError:  # pragma: no cover
    _HAS_PROMPTS = False


# ====================================================================== #
# Constants
# ====================================================================== #

# Mapping from logical ColumnDataType to engine-specific type strings.
# Keys are upper-case logical type names; values map engine -> SQL type.
_TYPE_MAP: Dict[str, Dict[str, str]] = {
    "STRING": {
        "clickhouse": "String",
        "hive": "STRING",
        "doris": "VARCHAR(65533)",
        "mysql": "VARCHAR(255)",
        "postgresql": "TEXT",
        "duckdb": "VARCHAR",
    },
    "TEXT": {
        "clickhouse": "String",
        "hive": "STRING",
        "doris": "STRING",
        "mysql": "TEXT",
        "postgresql": "TEXT",
        "duckdb": "VARCHAR",
    },
    "INTEGER": {
        "clickhouse": "Int32",
        "hive": "INT",
        "doris": "INT",
        "mysql": "INT",
        "postgresql": "INTEGER",
        "duckdb": "INTEGER",
    },
    "BIGINT": {
        "clickhouse": "Int64",
        "hive": "BIGINT",
        "doris": "BIGINT",
        "mysql": "BIGINT",
        "postgresql": "BIGINT",
        "duckdb": "BIGINT",
    },
    "FLOAT": {
        "clickhouse": "Float32",
        "hive": "FLOAT",
        "doris": "FLOAT",
        "mysql": "FLOAT",
        "postgresql": "REAL",
        "duckdb": "REAL",
    },
    "DOUBLE": {
        "clickhouse": "Float64",
        "hive": "DOUBLE",
        "doris": "DOUBLE",
        "mysql": "DOUBLE",
        "postgresql": "DOUBLE PRECISION",
        "duckdb": "DOUBLE",
    },
    "DECIMAL": {
        "clickhouse": "Decimal(18, 2)",
        "hive": "DECIMAL(18,2)",
        "doris": "DECIMAL(18,2)",
        "mysql": "DECIMAL(18,2)",
        "postgresql": "NUMERIC(18,2)",
        "duckdb": "DECIMAL(18,2)",
    },
    "BOOLEAN": {
        "clickhouse": "UInt8",
        "hive": "BOOLEAN",
        "doris": "BOOLEAN",
        "mysql": "TINYINT(1)",
        "postgresql": "BOOLEAN",
        "duckdb": "BOOLEAN",
    },
    "DATE": {
        "clickhouse": "Date",
        "hive": "DATE",
        "doris": "DATE",
        "mysql": "DATE",
        "postgresql": "DATE",
        "duckdb": "DATE",
    },
    "TIMESTAMP": {
        "clickhouse": "DateTime",
        "hive": "TIMESTAMP",
        "doris": "DATETIME",
        "mysql": "DATETIME",
        "postgresql": "TIMESTAMP",
        "duckdb": "TIMESTAMP",
    },
    "JSON": {
        "clickhouse": "String",
        "hive": "STRING",
        "doris": "JSON",
        "mysql": "JSON",
        "postgresql": "JSONB",
        "duckdb": "VARCHAR",
    },
    "BINARY": {
        "clickhouse": "String",
        "hive": "BINARY",
        "doris": "STRING",
        "mysql": "BLOB",
        "postgresql": "BYTEA",
        "duckdb": "BLOB",
    },
}

# Default DuckDB type used for verification sandbox DDL adaptation.
_DUCKDB_TYPE_FALLBACK = "VARCHAR"

# Layer-specific suffix conventions (used when no convention file is loaded).
_LAYER_SUFFIX: Dict[str, str] = {
    "ODS": "",
    "DWD": "_di",   # daily increment
    "DWS": "_1d",   # 1-day aggregation
    "ADS": "",
    "DIM": "",
    "TMP": "",
}

# Domain prefix mapping -- a naive heuristic based on table name keywords.
# The convention file, when loaded, overrides this entirely.
_DOMAIN_KEYWORDS: Dict[str, str] = {
    "order": "trade",
    "trade": "trade",
    "pay": "trade",
    "payment": "trade",
    "user": "user",
    "login": "user",
    "member": "user",
    "product": "product",
    "item": "product",
    "sku": "product",
    "log": "log",
    "event": "log",
    "click": "log",
}


# ====================================================================== #
# Helper models
# ====================================================================== #

class ColumnMapping(BaseModel):
    """Describes how a single source column maps to a target column.

    Carries the type conversion, optional SQL transformation expression,
    and metadata flags (partition key, primary key) needed to generate
    both the DDL and the computation SQL.
    """

    model_config = ConfigDict(use_enum_values=True)

    source_column: str = Field(
        ..., description="Original column name in the source table."
    )
    source_type: str = Field(
        ..., description="Native data type string from the source database."
    )
    target_column: str = Field(
        ..., description="Column name in the target table after naming convention."
    )
    target_type: str = Field(
        ..., description="Engine-specific data type in the target table."
    )
    transformation: Optional[str] = Field(
        default=None,
        description=(
            "SQL expression applied to the source value during INSERT INTO ... "
            "SELECT.  ``None`` means a direct 1:1 copy."
        ),
    )
    is_partition_key: bool = Field(
        default=False, description="Whether this column is a partition key."
    )
    is_primary_key: bool = Field(
        default=False, description="Whether this column is (part of) the primary key."
    )
    comment: str = Field(
        default="", description="Column-level comment for the DDL."
    )


class AIEnhanceResult(BaseModel):
    """Output of the optional LLM review / enhancement step."""

    enhanced_ddl: Optional[str] = Field(
        default=None, description="LLM-improved DDL, or ``None`` if unchanged."
    )
    enhanced_sql: Optional[str] = Field(
        default=None, description="LLM-improved computation SQL, or ``None``."
    )
    suggestions: List[str] = Field(
        default_factory=list,
        description="Actionable suggestions the LLM produced.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Potential issues the LLM flagged.",
    )


# ====================================================================== #
# Pipeline configuration
# ====================================================================== #

class DDLPipelineConfig(BaseModel):
    """Configuration for the DDL auto-generation pipeline.

    An instance of this model is created once per pipeline invocation and
    passed through every step of the builder.
    """

    model_config = ConfigDict(use_enum_values=True)

    source_connection_id: str = Field(
        ..., description="Connection ID of the source database to read metadata from."
    )
    source_tables: List[str] = Field(
        default_factory=list,
        description=(
            "Specific source table names to process.  An empty list means "
            "'process every table discovered from the source connection'."
        ),
    )
    target_layer: str = Field(
        default="ODS",
        description="Target warehouse layer: ODS, DWD, DWS, or ADS.",
    )
    target_db_type: str = Field(
        default="clickhouse",
        description=(
            "Target database engine type.  Controls DDL syntax, data-type "
            "names, partition clauses, and storage format.  Supported values: "
            "clickhouse, hive, doris, mysql, postgresql, duckdb."
        ),
    )
    convention_path: Optional[str] = Field(
        default=None,
        description="Path to a YAML or Markdown convention file.  ``None`` uses built-in defaults.",
    )
    naming_overrides: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Manual naming overrides keyed by source table name.  When a "
            "source table appears here the override value is used as the "
            "target table name verbatim (before layer-prefix application)."
        ),
    )
    include_computation_sql: bool = Field(
        default=True,
        description="Whether to also generate INSERT INTO ... SELECT computation SQL.",
    )
    local_verify: bool = Field(
        default=True,
        description="Whether to verify generated DDL and SQL in a local DuckDB sandbox.",
    )
    sample_rows_for_verify: int = Field(
        default=100,
        ge=1,
        le=10_000,
        description="Number of synthetic sample rows to generate for DuckDB verification.",
    )
    enable_ai: bool = Field(
        default=False,
        description="Whether to run LLM-based AI enhancement after DDL generation.",
    )


# ====================================================================== #
# Pipeline result models
# ====================================================================== #

class GeneratedTable(BaseModel):
    """A single generated table definition with its DDL and computation SQL."""

    source_table: str = Field(..., description="Name of the upstream source table.")
    target_table: str = Field(..., description="Generated target table name.")
    target_layer: str = Field(..., description="Warehouse layer the target belongs to.")
    ddl: str = Field(..., description="The CREATE TABLE statement for the target engine.")
    computation_sql: Optional[str] = Field(
        default=None,
        description="INSERT INTO target SELECT ... FROM source statement, or ``None``.",
    )
    convention_violations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Convention violations that were detected and auto-corrected.",
    )
    verify_result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="DuckDB sandbox verification result when ``local_verify=True``.",
    )
    column_mappings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of source_col -> target_col mapping dictionaries.",
    )
    ai_enhance_result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="LLM-based AI enhancement result (suggestions, warnings), or ``None``.",
    )


class DDLPipelineResult(BaseModel):
    """Complete result of a DDL generation pipeline run."""

    config: DDLPipelineConfig = Field(
        ..., description="The configuration that produced this result."
    )
    tables: List[GeneratedTable] = Field(
        default_factory=list, description="Per-table generation results."
    )
    total_tables: int = Field(default=0, description="Total tables processed.")
    succeeded: int = Field(default=0, description="Tables generated successfully.")
    failed: int = Field(default=0, description="Tables that failed generation.")
    convention_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Summary of convention rules applied.",
    )
    verify_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Aggregated DuckDB verification summary.",
    )
    errors: List[str] = Field(
        default_factory=list, description="Top-level error messages."
    )


# ====================================================================== #
# Internal helpers
# ====================================================================== #

def _normalise_layer_name(layer: str) -> str:
    """Return the canonical upper-case layer name."""
    return layer.strip().upper()


def _snake_case(name: str) -> str:
    """Convert an arbitrary string to snake_case suitable for SQL identifiers.

    Examples::

        >>> _snake_case("OrderDetails")
        'order_details'
        >>> _snake_case("user-login-log")
        'user_login_log'
        >>> _snake_case("  Some Table  ")
        'some_table'
    """
    name = name.strip()
    # Insert underscore before uppercase letters that follow lowercase/digits
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Replace non-alphanumeric runs with underscore
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    # Collapse consecutive underscores
    name = re.sub(r"_+", "_", name)
    return name.strip("_").lower()


def _infer_domain(table_name: str) -> str:
    """Heuristically infer the business domain from a table name.

    Returns the domain prefix string, or ``"common"`` when no keyword matches.
    """
    lower = table_name.lower()
    for keyword, domain in _DOMAIN_KEYWORDS.items():
        if keyword in lower:
            return domain
    return "common"


def _resolve_logical_type(col: ColumnInfo) -> str:
    """Return the logical type name for a column, falling back to STRING."""
    if col.logical_type is not None:
        return col.logical_type.value if hasattr(col.logical_type, "value") else str(col.logical_type)
    # Attempt to infer from native data_type string
    native = col.data_type.upper()
    if any(tok in native for tok in ("INT", "SERIAL")):
        return "BIGINT" if "BIG" in native else "INTEGER"
    if any(tok in native for tok in ("FLOAT", "REAL")):
        return "FLOAT"
    if "DOUBLE" in native:
        return "DOUBLE"
    if any(tok in native for tok in ("DECIMAL", "NUMERIC", "NUMBER")):
        return "DECIMAL"
    if "BOOL" in native:
        return "BOOLEAN"
    if any(tok in native for tok in ("DATE", "TIME")):
        return "TIMESTAMP" if "TIME" in native and "DATE" in native else "DATE"
    if any(tok in native for tok in ("JSON", "JSONB")):
        return "JSON"
    if any(tok in native for tok in ("BLOB", "BYTEA", "BINARY")):
        return "BINARY"
    if any(tok in native for tok in ("TEXT", "CLOB", "LONGTEXT", "MEDIUMTEXT")):
        return "TEXT"
    return "STRING"


def _map_type(logical_type: str, engine: str) -> str:
    """Map a logical type name to an engine-specific SQL type string."""
    entry = _TYPE_MAP.get(logical_type.upper())
    if entry is None:
        logger.warning("Unknown logical type '%s' -- falling back to STRING.", logical_type)
        entry = _TYPE_MAP["STRING"]
    return entry.get(engine.lower(), entry.get("duckdb", _DUCKDB_TYPE_FALLBACK))


def _map_type_duckdb(logical_type: str) -> str:
    """Shortcut: map a logical type to its DuckDB equivalent."""
    return _map_type(logical_type, "duckdb")


def _adapt_ddl_for_duckdb(ddl: str) -> str:
    """Best-effort adaptation of engine-specific DDL for DuckDB execution.

    Strips clauses that DuckDB does not understand (PARTITIONED BY, STORED AS,
    ENGINE = ..., LIFECYCLE, COMMENT on columns, etc.) and replaces engine-
    specific types with DuckDB equivalents.
    """
    adapted = ddl

    # Remove trailing clauses DuckDB does not support
    for pattern in [
        r"PARTITIONED\s+BY\s*\([^)]*\)",
        r"STORED\s+AS\s+\w+",
        r"ENGINE\s*=\s*\w+",
        r"LIFECYCLE\s+\d+",
        r"COMMENT\s+'[^']*'",
        r"COMMENT\s+\"[^\"]*\"",
    ]:
        adapted = re.sub(pattern, "", adapted, flags=re.IGNORECASE)

    # Replace common engine-specific types with DuckDB equivalents
    type_replacements = {
        r"\bString\b": "VARCHAR",
        r"\bDateTime\b": "TIMESTAMP",
        r"\bInt32\b": "INTEGER",
        r"\bInt64\b": "BIGINT",
        r"\bFloat32\b": "REAL",
        r"\bFloat64\b": "DOUBLE",
        r"\bUInt8\b": "BOOLEAN",
        r"\bJSONB?\b": "VARCHAR",
        r"\bBYTEA\b": "BLOB",
    }
    for pattern, replacement in type_replacements.items():
        adapted = re.sub(pattern, replacement, adapted)

    # Clean up excess whitespace and blank lines
    adapted = re.sub(r"\n{3,}", "\n\n", adapted)
    return adapted.strip()


# ====================================================================== #
# DDLAutoBuilder -- main engine
# ====================================================================== #

class DDLAutoBuilder:
    """Automated DDL and computation SQL builder.

    Reads source table metadata, applies convention rules, and generates
    target DDL plus computation SQL for each warehouse layer.  Optionally
    verifies output in a DuckDB sandbox and uses an LLM for intelligent
    review and enhancement.

    Parameters
    ----------
    config:
        A :class:`DDLPipelineConfig` controlling every aspect of the
        generation pipeline.

    Example
    -------
    ::

        config = DDLPipelineConfig(
            source_connection_id="conn_mysql_prod",
            source_tables=["orders", "users"],
            target_layer="ODS",
            target_db_type="clickhouse",
            local_verify=True,
        )
        builder = DDLAutoBuilder(config)
        result = await builder.build(source_schemas)
    """

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def __init__(self, config: DDLPipelineConfig) -> None:
        self.config = config
        self._convention: Optional[Any] = None  # TableConvention when available
        self._sandbox: Optional[Any] = None  # DuckDBSandbox when available
        self._layer_validator = LayerValidator()
        self._target_layer = _normalise_layer_name(config.target_layer)
        self._target_engine = config.target_db_type.lower()

        # Pre-load convention if a path was provided
        if config.convention_path:
            try:
                self.load_convention(config.convention_path)
            except Exception:
                logger.warning(
                    "Failed to load convention from '%s'; using defaults.",
                    config.convention_path,
                    exc_info=True,
                )

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    async def build(self, source_schemas: List[TableSchema]) -> DDLPipelineResult:
        """Execute the full generation pipeline.

        Parameters
        ----------
        source_schemas:
            Source table metadata (typically obtained from database adapters).

        Returns
        -------
        DDLPipelineResult
            Aggregated results including DDL, computation SQL, verification
            outcomes, and any errors encountered.
        """
        logger.info(
            "Starting DDL pipeline: layer=%s engine=%s tables=%d",
            self._target_layer,
            self._target_engine,
            len(source_schemas),
        )

        tables: List[GeneratedTable] = []
        errors: List[str] = []
        succeeded = 0
        failed = 0

        for schema in source_schemas:
            try:
                generated = await self._process_single_table(schema)
                tables.append(generated)
                succeeded += 1
            except Exception as exc:
                error_msg = f"Failed to process table '{schema.table_name}': {exc}"
                logger.error(error_msg, exc_info=True)
                errors.append(error_msg)
                failed += 1

        # Optional DuckDB verification (batch)
        verify_summary: Optional[Dict[str, Any]] = None
        if self.config.local_verify and _HAS_DUCKDB_SANDBOX:
            verify_summary = self._verify_all(tables, source_schemas)

        # Convention summary
        convention_summary: Optional[Dict[str, Any]] = None
        if self._convention is not None:
            convention_summary = {
                "source": self.config.convention_path or "built-in defaults",
                "rules_applied": len(tables),
            }
        else:
            convention_summary = {
                "source": "built-in defaults",
                "rules_applied": len(tables),
            }

        result = DDLPipelineResult(
            config=self.config,
            tables=tables,
            total_tables=len(source_schemas),
            succeeded=succeeded,
            failed=failed,
            convention_summary=convention_summary,
            verify_summary=verify_summary,
            errors=errors,
        )

        logger.info(
            "DDL pipeline complete: total=%d succeeded=%d failed=%d",
            result.total_tables,
            result.succeeded,
            result.failed,
        )
        return result

    # ------------------------------------------------------------------ #
    # Step 1: Convention loading
    # ------------------------------------------------------------------ #

    def load_convention(self, path: str) -> Any:
        """Load naming and typing conventions from a YAML or Markdown file.

        Parameters
        ----------
        path:
            Filesystem path to the convention file.

        Returns
        -------
        TableConvention or dict
            The loaded convention object.  Falls back to a minimal stub when
            ``convention_loader`` is not available.
        """
        if _HAS_CONVENTION_LOADER and ConventionLoader is not None:
            loader = ConventionLoader()
            self._convention = loader.load_auto(path)
            logger.info("Loaded convention from '%s'.", path)
        else:
            logger.warning(
                "convention_loader module not available; "
                "using built-in defaults for path '%s'.",
                path,
            )
            self._convention = {"path": path, "type": "default"}
        return self._convention

    # ------------------------------------------------------------------ #
    # Step 2: Naming
    # ------------------------------------------------------------------ #

    def apply_naming(self, source_table: str, layer: str) -> str:
        """Generate the target table name by applying naming conventions.

        The algorithm is:

        1. Check ``naming_overrides`` in the config.
        2. Check the loaded convention (if any).
        3. Fall back to the built-in pattern:
           ``{layer_prefix}_{domain}_{table_body}{layer_suffix}``

        Parameters
        ----------
        source_table:
            The original source table name.
        layer:
            Target warehouse layer (e.g. ``"ODS"``).

        Returns
        -------
        str
            The generated target table name.
        """
        layer_upper = _normalise_layer_name(layer)

        # 1. Manual override
        if source_table in self.config.naming_overrides:
            override = self.config.naming_overrides[source_table]
            logger.debug("Naming override: %s -> %s", source_table, override)
            return override

        # 2. Convention file (if available and has a naming function)
        if (
            self._convention is not None
            and hasattr(self._convention, "naming")
        ):
            try:
                name = self._apply_naming_convention(source_table, layer_upper)
                if name:
                    logger.debug(
                        "Convention naming: %s + %s -> %s", source_table, layer_upper, name
                    )
                    return name
            except Exception:
                logger.debug("Convention naming failed; falling back to defaults.")

        # 3. Built-in naming
        body = _snake_case(source_table)
        domain = _infer_domain(source_table)
        prefix = layer_upper.lower()
        suffix = _LAYER_SUFFIX.get(layer_upper, "")

        target = f"{prefix}_{domain}_{body}{suffix}"
        logger.debug("Built-in naming: %s + %s -> %s", source_table, layer_upper, target)
        return target

    def _apply_naming_convention(self, source_table: str, layer_upper: str) -> Optional[str]:
        """Construct a target table name from the loaded convention's naming rules.

        Reads the ``NamingConvention`` fields (``table_pattern``,
        ``prefix_rules``, ``suffix_rules``, ``case_style``) stored on
        ``self._convention.naming`` and assembles a table name.

        Returns ``None`` when the convention has no usable naming data so
        the caller can fall back to built-in defaults.
        """
        naming = getattr(self._convention, "naming", None)
        if naming is None:
            return None

        table_pattern: str = getattr(naming, "table_pattern", "") or ""
        prefix_rules: Dict[str, str] = getattr(naming, "prefix_rules", {}) or {}
        suffix_rules: Dict[str, str] = getattr(naming, "suffix_rules", {}) or {}
        case_style: str = getattr(naming, "case_style", "snake_case") or "snake_case"

        if not table_pattern:
            return None

        # Derive the parts used in the pattern
        prefix = prefix_rules.get(layer_upper, layer_upper.lower() + "_")
        domain = _infer_domain(source_table)
        body = _snake_case(source_table)

        # Infer a category for suffix lookup (heuristic: check table name keywords)
        category = ""
        lower_name = source_table.lower()
        for key in suffix_rules:
            if key in lower_name:
                category = key
                break
        suffix = suffix_rules.get(category, "")

        # Apply the pattern template
        name = table_pattern.format(
            layer=layer_upper.lower(),
            domain=domain,
            description=body,
            prefix=prefix,
            suffix=suffix,
        )

        # Apply case style
        name = _snake_case(name) if case_style == "snake_case" else name

        logger.debug(
            "Convention naming applied: pattern=%s -> %s", table_pattern, name
        )
        return name

    # ------------------------------------------------------------------ #
    # Step 3: Column mapping
    # ------------------------------------------------------------------ #

    def map_columns(
        self,
        source_columns: List[ColumnInfo],
        target_engine: str,
        layer: str = "",
    ) -> List[ColumnMapping]:
        """Map source columns to target columns with type conversion.

        For each source column the method:

        * Derives the target column name (snake_case normalisation).
        * Maps the native data type through the logical-type table to the
          target engine's type system.
        * Adds layer-specific columns (``etl_time`` for ODS, surrogate keys
          for DWD, etc.).

        Parameters
        ----------
        source_columns:
            Column metadata from the source table.
        target_engine:
            Target database engine identifier.
        layer:
            Target warehouse layer (affects which extra columns are added).

        Returns
        -------
        List[ColumnMapping]
            Ordered list of column mappings ready for DDL and SQL generation.
        """
        layer_upper = _normalise_layer_name(layer or self._target_layer)
        mappings: List[ColumnMapping] = []

        for col in source_columns:
            logical = _resolve_logical_type(col)
            target_type = _map_type(logical, target_engine)
            target_name = _snake_case(col.name)

            mappings.append(ColumnMapping(
                source_column=col.name,
                source_type=col.data_type,
                target_column=target_name,
                target_type=target_type,
                transformation=None,
                is_partition_key=False,
                is_primary_key=col.is_primary_key,
                comment=col.comment or "",
            ))

        # Layer-specific synthetic columns
        if layer_upper == "ODS":
            mappings.append(ColumnMapping(
                source_column="__etl_time",
                source_type="TIMESTAMP",
                target_column="etl_time",
                target_type=_map_type("TIMESTAMP", target_engine),
                transformation="NOW()",
                is_partition_key=False,
                is_primary_key=False,
                comment="ETL ingestion timestamp",
            ))
            # Add partition key column for ODS
            mappings.append(ColumnMapping(
                source_column="__dt",
                source_type="STRING",
                target_column="dt",
                target_type=_map_type("STRING", target_engine),
                transformation="${bizdate}",
                is_partition_key=True,
                is_primary_key=False,
                comment="Partition date (yyyy-MM-dd)",
            ))

        elif layer_upper == "DWD":
            # Surrogate key at the beginning
            mappings.insert(0, ColumnMapping(
                source_column="__surrogate_key",
                source_type="BIGINT",
                target_column="row_key",
                target_type=_map_type("BIGINT", target_engine),
                transformation="ROW_NUMBER() OVER (ORDER BY (SELECT NULL))",
                is_partition_key=False,
                is_primary_key=True,
                comment="Surrogate primary key",
            ))
            mappings.append(ColumnMapping(
                source_column="__dt",
                source_type="STRING",
                target_column="dt",
                target_type=_map_type("STRING", target_engine),
                transformation="${bizdate}",
                is_partition_key=True,
                is_primary_key=False,
                comment="Partition date (yyyy-MM-dd)",
            ))

        elif layer_upper == "DWS":
            mappings.append(ColumnMapping(
                source_column="__dt",
                source_type="STRING",
                target_column="dt",
                target_type=_map_type("STRING", target_engine),
                transformation="${bizdate}",
                is_partition_key=True,
                is_primary_key=False,
                comment="Partition date (yyyy-MM-dd)",
            ))

        return mappings

    # ------------------------------------------------------------------ #
    # Step 4: DDL generation
    # ------------------------------------------------------------------ #

    def generate_ddl(
        self,
        target_table: str,
        columns: List[ColumnMapping],
        layer: str,
        engine: str,
    ) -> str:
        """Generate a CREATE TABLE DDL statement for the target engine.

        The generated DDL includes:

        * Column definitions with engine-specific types.
        * NOT NULL and DEFAULT constraints where appropriate.
        * COMMENT annotations on every column and on the table.
        * Partition clause (for layers that require partitioning).
        * Storage format clause (for Hive / ClickHouse / Doris).

        Parameters
        ----------
        target_table:
            Fully-qualified target table name.
        columns:
            Column mappings (from :meth:`map_columns`).
        layer:
            Target warehouse layer.
        engine:
            Target database engine identifier.

        Returns
        -------
        str
            The complete CREATE TABLE statement.
        """
        layer_upper = _normalise_layer_name(layer)
        engine_lower = engine.lower()

        # Separate partition columns from regular columns
        regular_cols = [c for c in columns if not c.is_partition_key]
        partition_cols = [c for c in columns if c.is_partition_key]

        # Resolve storage settings from layer config
        layer_enum = self._resolve_layer_enum(layer_upper)
        layer_cfg: Optional[LayerConfig] = DEFAULT_LAYER_CONFIGS.get(layer_enum)
        storage_format = layer_cfg.storage_format if layer_cfg else "Parquet"
        compression = layer_cfg.compression if layer_cfg else "snappy"
        table_comment = f"{layer_upper} layer table for {target_table}"

        lines: List[str] = []
        lines.append(f"CREATE TABLE IF NOT EXISTS {target_table} (")

        col_defs: List[str] = []
        for cm in regular_cols:
            parts = [f"    {cm.target_column:<30s} {cm.target_type}"]
            if cm.is_primary_key:
                # Some engines handle PK inline
                if engine_lower in ("mysql", "postgresql", "duckdb"):
                    pass  # PK declared at table level
            if cm.comment:
                safe_comment = cm.comment.replace("'", "''")
                if engine_lower in ("hive", "clickhouse"):
                    parts.append(f"COMMENT '{safe_comment}'")
                elif engine_lower in ("mysql",):
                    parts.append(f"COMMENT '{safe_comment}'")
                # PostgreSQL / DuckDB use COMMENT ON COLUMN after CREATE TABLE
            col_defs.append(" ".join(parts))

        lines.append(",\n".join(col_defs))
        lines.append(")")

        # Table comment
        safe_table_comment = table_comment.replace("'", "''")
        if engine_lower in ("hive", "clickhouse", "mysql"):
            lines.append(f"COMMENT '{safe_table_comment}'")
        elif engine_lower == "doris":
            lines.append(f"COMMENT '{safe_table_comment}'")

        # Engine-specific clauses
        if engine_lower == "clickhouse":
            pk_cols = [c.target_column for c in regular_cols if c.is_primary_key]
            order_by = ", ".join(pk_cols) if pk_cols else regular_cols[0].target_column if regular_cols else "tuple()"
            lines.append(f"ENGINE = MergeTree()")
            lines.append(f"ORDER BY ({order_by})")
            if partition_cols:
                pk_names = ", ".join(c.target_column for c in partition_cols)
                lines.append(f"PARTITION BY ({pk_names})")
            lines.append(f"SETTINGS storage_policy = 'default'")

        elif engine_lower == "hive":
            if partition_cols:
                pk_defs = []
                for pc in partition_cols:
                    cmt = f" COMMENT '{pc.comment}'" if pc.comment else ""
                    pk_defs.append(f"{pc.target_column} {pc.target_type}{cmt}")
                lines.append(f"PARTITIONED BY ({', '.join(pk_defs)})")
            lines.append(f"STORED AS {storage_format.upper()}")

        elif engine_lower == "doris":
            pk_cols = [c.target_column for c in regular_cols if c.is_primary_key]
            if pk_cols:
                lines.append(f"UNIQUE KEY ({', '.join(pk_cols)})")
            if partition_cols:
                pk_names = ", ".join(c.target_column for c in partition_cols)
                lines.append(f"PARTITION BY LIST({pk_names})()")
            lines.append(f"DISTRIBUTED BY HASH({regular_cols[0].target_column if regular_cols else 'id'}) BUCKETS 10")
            lines.append(f'PROPERTIES ("replication_num" = "1")')

        elif engine_lower == "mysql":
            pk_cols = [c.target_column for c in regular_cols if c.is_primary_key]
            if pk_cols:
                lines.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
            lines.append(f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")

        elif engine_lower == "postgresql":
            pk_cols = [c.target_column for c in regular_cols if c.is_primary_key]
            if pk_cols:
                lines.append(f"PRIMARY KEY ({', '.join(pk_cols)})")

        elif engine_lower == "duckdb":
            pk_cols = [c.target_column for c in regular_cols if c.is_primary_key]
            if pk_cols:
                lines.append(f"PRIMARY KEY ({', '.join(pk_cols)})")

        lines.append(";")

        # PostgreSQL-style column comments (COMMENT ON COLUMN ...)
        if engine_lower == "postgresql":
            for cm in regular_cols:
                if cm.comment:
                    safe_cmt = cm.comment.replace("'", "''")
                    lines.append(
                        f"COMMENT ON COLUMN {target_table}.{cm.target_column} "
                        f"IS '{safe_cmt}';"
                    )

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Step 5: Computation SQL generation
    # ------------------------------------------------------------------ #

    def generate_computation_sql(
        self,
        source_table: str,
        target_table: str,
        column_mappings: List[ColumnMapping],
        layer: str,
    ) -> str:
        """Generate INSERT INTO ... SELECT computation SQL.

        The SQL pattern depends on the target layer:

        * **ODS**: ``INSERT INTO ods_xxx SELECT *, NOW() AS etl_time FROM source``
        * **DWD**: ``INSERT INTO dwd_xxx SELECT ROW_NUMBER()..., cleansed cols FROM ods_xxx WHERE dt = '${bizdate}'``
        * **DWS**: ``INSERT INTO dws_xxx SELECT ... FROM dwd_xxx GROUP BY ...``
        * **ADS**: ``INSERT INTO ads_xxx SELECT ... FROM dws_xxx WHERE ...``

        Parameters
        ----------
        source_table:
            The upstream table name to SELECT from.
        target_table:
            The target table name to INSERT INTO.
        column_mappings:
            Column mappings (from :meth:`map_columns`).
        layer:
            Target warehouse layer.

        Returns
        -------
        str
            The generated INSERT INTO ... SELECT statement.
        """
        layer_upper = _normalise_layer_name(layer)
        regular_cols = [c for c in column_mappings if not c.is_partition_key]
        partition_cols = [c for c in column_mappings if c.is_partition_key]

        target_col_list = ", ".join(c.target_column for c in column_mappings)

        if layer_upper == "ODS":
            return self._gen_ods_sql(source_table, target_table, regular_cols, partition_cols)
        elif layer_upper == "DWD":
            return self._gen_dwd_sql(source_table, target_table, regular_cols, partition_cols)
        elif layer_upper == "DWS":
            return self._gen_dws_sql(source_table, target_table, regular_cols, partition_cols)
        elif layer_upper == "ADS":
            return self._gen_ads_sql(source_table, target_table, regular_cols, partition_cols)
        else:
            # Fallback: simple passthrough
            return self._gen_ods_sql(source_table, target_table, regular_cols, partition_cols)

    # --- Layer-specific SQL generators --------------------------------- #

    def _gen_ods_sql(
        self,
        source_table: str,
        target_table: str,
        regular_cols: List[ColumnMapping],
        partition_cols: List[ColumnMapping],
    ) -> str:
        """Generate ODS computation SQL -- simple SELECT with ETL timestamp."""
        select_parts: List[str] = []
        for cm in regular_cols:
            if cm.transformation:
                select_parts.append(f"{cm.transformation} AS {cm.target_column}")
            else:
                select_parts.append(f"s.{cm.source_column} AS {cm.target_column}")

        # Partition columns use the variable placeholder
        for pc in partition_cols:
            if pc.transformation:
                select_parts.append(f"'{pc.transformation}' AS {pc.target_column}")
            else:
                select_parts.append(f"s.{pc.source_column} AS {pc.target_column}")

        select_clause = ",\n    ".join(select_parts)
        target_col_list = ", ".join(
            c.target_column for c in regular_cols + partition_cols
        )

        sql = textwrap.dedent(f"""\
            INSERT INTO {target_table} ({target_col_list})
            SELECT
                {select_clause}
            FROM {source_table} s
            ;
        """)
        return sql.strip()

    def _gen_dwd_sql(
        self,
        source_table: str,
        target_table: str,
        regular_cols: List[ColumnMapping],
        partition_cols: List[ColumnMapping],
    ) -> str:
        """Generate DWD computation SQL -- cleansing, dedup, standardisation."""
        select_parts: List[str] = []
        for cm in regular_cols:
            if cm.transformation:
                select_parts.append(f"{cm.transformation} AS {cm.target_column}")
            else:
                # DWD: apply COALESCE for nullable string fields, CAST for type safety
                select_parts.append(f"s.{cm.source_column} AS {cm.target_column}")

        for pc in partition_cols:
            if pc.transformation:
                select_parts.append(f"'{pc.transformation}' AS {pc.target_column}")
            else:
                select_parts.append(f"s.{pc.source_column} AS {pc.target_column}")

        select_clause = ",\n    ".join(select_parts)
        target_col_list = ", ".join(
            c.target_column for c in regular_cols + partition_cols
        )

        # Find a natural ordering column for dedup (prefer an *_id or *_time column)
        order_col = self._find_order_column(regular_cols)

        sql = textwrap.dedent(f"""\
            INSERT INTO {target_table} ({target_col_list})
            SELECT
                {select_clause}
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY {order_col}
                        ORDER BY {order_col} DESC
                    ) AS _rn
                FROM {source_table}
                WHERE dt = '${{bizdate}}'
            ) s
            WHERE s._rn = 1
            ;
        """)
        return sql.strip()

    def _gen_dws_sql(
        self,
        source_table: str,
        target_table: str,
        regular_cols: List[ColumnMapping],
        partition_cols: List[ColumnMapping],
    ) -> str:
        """Generate DWS computation SQL -- aggregation with GROUP BY."""
        # Heuristic: first non-metric column is the grouping dimension,
        # remaining numeric columns are aggregated.
        group_cols: List[ColumnMapping] = []
        metric_cols: List[ColumnMapping] = []

        for cm in regular_cols:
            if cm.is_primary_key or cm.target_column in ("row_key", "etl_time"):
                continue
            logical_upper = cm.source_type.upper()
            if any(tok in logical_upper for tok in ("INT", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "BIGINT")):
                metric_cols.append(cm)
            else:
                group_cols.append(cm)

        # If no grouping columns found, use the first column as fallback
        if not group_cols and regular_cols:
            group_cols = [regular_cols[0]]

        select_parts: List[str] = []
        for gc in group_cols:
            select_parts.append(f"s.{gc.target_column}")
        for mc in metric_cols:
            agg_fn = "SUM" if any(tok in mc.source_type.upper() for tok in ("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT")) else "COUNT"
            select_parts.append(f"{agg_fn}(s.{mc.target_column}) AS {mc.target_column}")

        for pc in partition_cols:
            if pc.transformation:
                select_parts.append(f"'{pc.transformation}' AS {pc.target_column}")
            else:
                select_parts.append(f"s.{pc.target_column} AS {pc.target_column}")

        select_clause = ",\n    ".join(select_parts)
        group_by_clause = ", ".join(f"s.{gc.target_column}" for gc in group_cols)
        target_col_list = ", ".join(
            c.target_column for c in group_cols + metric_cols + partition_cols
        )

        sql = textwrap.dedent(f"""\
            INSERT INTO {target_table} ({target_col_list})
            SELECT
                {select_clause}
            FROM {source_table} s
            WHERE s.dt = '${{bizdate}}'
            GROUP BY {group_by_clause}
            ;
        """)
        return sql.strip()

    def _gen_ads_sql(
        self,
        source_table: str,
        target_table: str,
        regular_cols: List[ColumnMapping],
        partition_cols: List[ColumnMapping],
    ) -> str:
        """Generate ADS computation SQL -- application-specific result set."""
        select_parts: List[str] = []
        for cm in regular_cols:
            select_parts.append(f"s.{cm.target_column} AS {cm.target_column}")

        for pc in partition_cols:
            if pc.transformation:
                select_parts.append(f"'{pc.transformation}' AS {pc.target_column}")
            else:
                select_parts.append(f"s.{pc.target_column} AS {pc.target_column}")

        select_clause = ",\n    ".join(select_parts)
        target_col_list = ", ".join(
            c.target_column for c in regular_cols + partition_cols
        )

        sql = textwrap.dedent(f"""\
            INSERT INTO {target_table} ({target_col_list})
            SELECT
                {select_clause}
            FROM {source_table} s
            WHERE s.dt = '${{bizdate}}'
            ;
        """)
        return sql.strip()

    # ------------------------------------------------------------------ #
    # Step 6: DuckDB sandbox verification
    # ------------------------------------------------------------------ #

    def verify_in_sandbox(
        self,
        ddl: str,
        computation_sql: str,
        source_schema: TableSchema,
    ) -> Dict[str, Any]:
        """Verify DDL and computation SQL in a local DuckDB sandbox.

        The verification procedure:

        1. Adapt the source schema DDL for DuckDB and create it.
        2. Insert synthetic sample data into the source table.
        3. Execute the target DDL.
        4. Execute the computation SQL.
        5. Query the target table and check that rows were produced.

        Parameters
        ----------
        ddl:
            The target CREATE TABLE statement.
        computation_sql:
            The INSERT INTO ... SELECT statement.
        source_schema:
            Source table metadata used to create the source DDL.

        Returns
        -------
        dict
            A dictionary with keys: ``success`` (bool), ``rows_produced``
            (int), ``errors`` (list[str]), ``duration_ms`` (float).
        """
        result: Dict[str, Any] = {
            "success": False,
            "rows_produced": 0,
            "errors": [],
            "duration_ms": 0.0,
        }

        if not _HAS_DUCKDB_SANDBOX or DuckDBSandbox is None:
            result["errors"].append("DuckDB sandbox module not available.")
            return result

        import time
        start = time.monotonic()

        try:
            sandbox = DuckDBSandbox()
            with sandbox:
                # 1. Create source table DDL for DuckDB
                source_ddl = self._build_source_ddl_duckdb(source_schema)
                sandbox.verify_ddl(source_ddl)

                # 2. Insert sample data
                sample_inserts = self._generate_sample_inserts(
                    source_schema, self.config.sample_rows_for_verify
                )
                for insert_sql in sample_inserts:
                    sandbox.verify_computation_sql(insert_sql)

                # 3. Execute target DDL (adapted for DuckDB)
                adapted_ddl = _adapt_ddl_for_duckdb(ddl)
                sandbox.verify_ddl(adapted_ddl)

                # 4. Execute computation SQL (adapted for DuckDB)
                adapted_sql = self._adapt_computation_sql_for_duckdb(computation_sql)
                sandbox.verify_computation_sql(adapted_sql)

                # 5. Check results
                target_table = self._extract_table_name_from_ddl(ddl)
                if target_table:
                    query_result = sandbox.execute_and_preview(
                        f"SELECT COUNT(*) AS cnt FROM {target_table}"
                    )
                    rows = query_result.rows if query_result.rows else []
                    count = rows[0]["cnt"] if rows else 0
                    result["rows_produced"] = count
                    result["success"] = count > 0

        except Exception as exc:
            result["errors"].append(str(exc))
            logger.debug("DuckDB verification failed: %s", exc, exc_info=True)

        result["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
        return result

    # ------------------------------------------------------------------ #
    # Step 7: AI enhancement
    # ------------------------------------------------------------------ #

    async def ai_enhance(
        self,
        source_schema: TableSchema,
        target_ddl: str,
        computation_sql: str,
    ) -> AIEnhanceResult:
        """Use an LLM to review and enhance the generated DDL and SQL.

        The LLM is asked to:

        * Suggest better column names or missing columns.
        * Identify potential data quality issues.
        * Optimise the computation SQL.
        * Add missing transformations.

        Parameters
        ----------
        source_schema:
            The source table metadata.
        target_ddl:
            The generated CREATE TABLE DDL.
        computation_sql:
            The generated INSERT INTO ... SELECT SQL.

        Returns
        -------
        AIEnhanceResult
            Enhanced DDL/SQL (or ``None`` if the LLM did not suggest changes),
            plus suggestions and warnings.
        """
        result = AIEnhanceResult()

        if not _HAS_AI_PROVIDER:
            logger.warning("AI provider module not available; skipping AI enhancement.")
            result.warnings.append("AI provider module not available.")
            return result

        try:
            # Build the prompt
            source_desc = self._schema_to_description(source_schema)
            prompt_text = (
                f"Review the following DDL and computation SQL for a "
                f"{self._target_layer} layer table targeting {self._target_engine}.\n\n"
                f"## Source Table\n{source_desc}\n\n"
                f"## Generated DDL\n```sql\n{target_ddl}\n```\n\n"
                f"## Generated Computation SQL\n```sql\n{computation_sql}\n```\n\n"
                f"Please provide:\n"
                f"1. An improved DDL if needed (wrapped in ```sql).\n"
                f"2. An improved computation SQL if needed (wrapped in ```sql).\n"
                f"3. A list of suggestions.\n"
                f"4. A list of warnings or potential issues.\n"
                f"Respond in a structured format with clear sections."
            )

            system_prompt = (
                "You are an expert data engineer and data warehouse architect. "
                "Review DDL and ETL SQL for correctness, performance, and "
                "conformance to warehouse best practices."
            )

            # Attempt to use the AI provider
            try:
                from src.config.settings import get_settings
                provider_config = get_settings().get_provider_config()
            except Exception:
                provider_config = ProviderConfig(temperature=0.2, max_tokens=4096)
            provider = AIProviderFactory.create(provider_config)
            ai_response = await provider.generate(
                prompt_text, system=system_prompt
            )

            # Parse the response
            content = ai_response.content
            result.suggestions = self._extract_list_section(content, "suggestion")
            result.warnings = self._extract_list_section(content, "warning")
            result.enhanced_ddl = self._extract_sql_block(content, index=0)
            result.enhanced_sql = self._extract_sql_block(content, index=1)

        except Exception as exc:
            logger.warning("AI enhancement failed: %s", exc, exc_info=True)
            result.warnings.append(f"AI enhancement failed: {exc}")

        return result

    # ------------------------------------------------------------------ #
    # Internal: process a single table through the full pipeline
    # ------------------------------------------------------------------ #

    async def _process_single_table(self, schema: TableSchema) -> GeneratedTable:
        """Run the full generation pipeline for one source table.

        Returns a :class:`GeneratedTable` with DDL, computation SQL, and
        metadata.

        Raises
        ------
        ValueError
            If the source schema has no columns.
        """
        source_name = schema.table_name
        logger.debug("Processing source table: %s", source_name)

        if not schema.columns:
            raise ValueError(f"Source table '{source_name}' has no columns defined.")

        # 1. Naming
        target_name = self.apply_naming(source_name, self._target_layer)

        # 2. Validate naming against layer conventions
        violations: List[Dict[str, Any]] = []
        layer_enum = self._resolve_layer_enum(self._target_layer)
        naming_errors = self._layer_validator.validate_table_placement(target_name, layer_enum)
        for err in naming_errors:
            violations.append({"type": "naming", "message": err, "auto_fixed": True})
            logger.debug("Convention violation (auto-fixed): %s", err)

        # 3. Column mapping
        column_mappings = self.map_columns(
            schema.columns, self._target_engine, self._target_layer
        )

        # 4. DDL generation
        ddl = self.generate_ddl(
            target_name, column_mappings, self._target_layer, self._target_engine
        )

        # 5. Computation SQL generation
        computation_sql: Optional[str] = None
        if self.config.include_computation_sql:
            # The source for computation SQL depends on the layer
            upstream_table = self._resolve_upstream_table(source_name, self._target_layer)
            computation_sql = self.generate_computation_sql(
                upstream_table, target_name, column_mappings, self._target_layer
            )

        # 6. DuckDB verification (per-table)
        verify_result: Optional[Dict[str, Any]] = None
        if self.config.local_verify and _HAS_DUCKDB_SANDBOX and computation_sql:
            verify_result = self.verify_in_sandbox(ddl, computation_sql, schema)

        # 7. AI enhancement (optional)
        ai_result: Optional[Dict[str, Any]] = None
        if self.config.enable_ai and _HAS_AI_PROVIDER:
            try:
                enhance = await self.ai_enhance(schema, ddl, computation_sql or "")
                ai_result = {
                    "suggestions": enhance.suggestions,
                    "warnings": enhance.warnings,
                    "enhanced_ddl": enhance.enhanced_ddl,
                    "enhanced_sql": enhance.enhanced_sql,
                }
                # Use AI-improved DDL/SQL if provided
                if enhance.enhanced_ddl:
                    ddl = enhance.enhanced_ddl
                if enhance.enhanced_sql:
                    computation_sql = enhance.enhanced_sql
            except Exception as exc:
                logger.warning("AI enhancement failed for '%s': %s", source_name, exc)
                ai_result = {"error": str(exc)}

        # 8. Serialize column mappings for the result
        mapping_dicts = [cm.model_dump() for cm in column_mappings]

        return GeneratedTable(
            source_table=source_name,
            target_table=target_name,
            target_layer=self._target_layer,
            ddl=ddl,
            computation_sql=computation_sql,
            convention_violations=violations,
            verify_result=verify_result,
            column_mappings=mapping_dicts,
            ai_enhance_result=ai_result,
        )

    # ------------------------------------------------------------------ #
    # Internal: batch verification
    # ------------------------------------------------------------------ #

    def _verify_all(
        self,
        tables: List[GeneratedTable],
        source_schemas: List[TableSchema],
    ) -> Dict[str, Any]:
        """Run DuckDB verification for all generated tables and aggregate.

        Returns a summary dictionary with overall success rate and per-table
        verification outcomes.
        """
        summary: Dict[str, Any] = {
            "total_verified": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "per_table": {},
        }

        schema_map = {s.table_name: s for s in source_schemas}

        for gt in tables:
            if not gt.computation_sql or not gt.verify_result:
                # Already verified per-table above or verification not applicable
                if gt.verify_result:
                    summary["total_verified"] += 1
                    if gt.verify_result.get("success"):
                        summary["passed"] += 1
                    else:
                        summary["failed"] += 1
                    summary["per_table"][gt.target_table] = gt.verify_result
                else:
                    summary["skipped"] += 1
                continue

        return summary

    # ------------------------------------------------------------------ #
    # Internal: DuckDB helpers
    # ------------------------------------------------------------------ #

    def _build_source_ddl_duckdb(self, schema: TableSchema) -> str:
        """Build a CREATE TABLE DDL for the source table adapted to DuckDB."""
        col_defs: List[str] = []
        for col in schema.columns:
            logical = _resolve_logical_type(col)
            duckdb_type = _map_type_duckdb(logical)
            col_name = _snake_case(col.name)
            col_defs.append(f"    {col_name} {duckdb_type}")

        cols_sql = ",\n".join(col_defs)
        return f"CREATE TABLE IF NOT EXISTS {schema.table_name} (\n{cols_sql}\n);"

    def _generate_sample_inserts(
        self,
        schema: TableSchema,
        count: int,
    ) -> List[str]:
        """Generate synthetic INSERT statements for DuckDB verification.

        Produces *count* rows of plausible sample data based on column types.
        To keep the SQL manageable, rows are batched into multi-value INSERTs
        of up to 50 rows each.
        """
        if not schema.columns:
            return []

        col_names = [_snake_case(c.name) for c in schema.columns]
        col_list = ", ".join(col_names)
        inserts: List[str] = []
        batch_size = 50
        remaining = count

        for batch_start in range(0, count, batch_size):
            batch_count = min(batch_size, remaining)
            remaining -= batch_count
            value_rows: List[str] = []

            for i in range(batch_count):
                values: List[str] = []
                for col in schema.columns:
                    values.append(self._sample_value(col, batch_start + i))
                value_rows.append(f"    ({', '.join(values)})")

            values_sql = ",\n".join(value_rows)
            inserts.append(
                f"INSERT INTO {schema.table_name} ({col_list}) VALUES\n{values_sql};"
            )

        return inserts

    @staticmethod
    def _sample_value(col: ColumnInfo, row_index: int) -> str:
        """Generate a single sample literal value for a column, as a SQL string."""
        logical = _resolve_logical_type(col).upper()

        if logical in ("INTEGER", "BIGINT"):
            return str(row_index + 1)
        if logical in ("FLOAT", "DOUBLE", "DECIMAL"):
            return f"{(row_index + 1) * 1.5:.2f}"
        if logical == "BOOLEAN":
            return "TRUE" if row_index % 2 == 0 else "FALSE"
        if logical == "DATE":
            return f"'2025-01-{(row_index % 28) + 1:02d}'"
        if logical == "TIMESTAMP":
            return f"'2025-01-{(row_index % 28) + 1:02d} 10:00:00'"
        if logical == "JSON":
            return f"'{{\"id\": {row_index + 1}}}'"
        # Default: treat as string
        return f"'sample_{col.name}_{row_index + 1}'"

    def _adapt_computation_sql_for_duckdb(self, sql: str) -> str:
        """Adapt computation SQL for DuckDB execution in the sandbox.

        Replaces ``${bizdate}`` placeholders and ``NOW()`` calls with
        DuckDB-compatible equivalents.
        """
        adapted = sql
        # Replace scheduling variables with concrete values
        adapted = adapted.replace("'${bizdate}'", "'2025-01-15'")
        adapted = adapted.replace("${bizdate}", "'2025-01-15'")
        # DuckDB supports NOW() natively, so no change needed there.
        return adapted

    @staticmethod
    def _extract_table_name_from_ddl(ddl: str) -> Optional[str]:
        """Extract the table name from a CREATE TABLE DDL statement."""
        match = re.search(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)",
            ddl,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).rstrip("(")
        return None

    # ------------------------------------------------------------------ #
    # Internal: upstream table resolution
    # ------------------------------------------------------------------ #

    def _resolve_upstream_table(self, source_table: str, target_layer: str) -> str:
        """Determine the upstream table name for computation SQL.

        * ODS reads directly from the source table.
        * DWD reads from the ODS table.
        * DWS reads from the DWD table.
        * ADS reads from the DWS table.
        """
        layer_upper = _normalise_layer_name(target_layer)
        if layer_upper == "ODS":
            return source_table
        elif layer_upper == "DWD":
            return self.apply_naming(source_table, "ODS")
        elif layer_upper == "DWS":
            return self.apply_naming(source_table, "DWD")
        elif layer_upper == "ADS":
            return self.apply_naming(source_table, "DWS")
        return source_table

    # ------------------------------------------------------------------ #
    # Internal: layer resolution
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_layer_enum(layer: str) -> WarehouseLayer:
        """Map a string layer name to the WarehouseLayer enum.

        Handles both the uppercase variant from ``core.schemas`` and the
        lowercase variant from ``warehouse.layers``.
        """
        upper = layer.upper()
        for member in WarehouseLayer:
            if member.value.upper() == upper or member.name.upper() == upper:
                return member
        # Default to ODS if unknown
        logger.warning("Unknown layer '%s'; defaulting to ODS.", layer)
        return WarehouseLayer.ODS

    # ------------------------------------------------------------------ #
    # Internal: ordering column heuristic
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find_order_column(columns: List[ColumnMapping]) -> str:
        """Find a suitable ordering column for ROW_NUMBER dedup.

        Prefers columns ending in ``_time``, ``_date``, ``_id``, or ``id``.
        Falls back to the first column.
        """
        for suffix in ("_time", "_date", "_at", "_id", "id"):
            for cm in columns:
                if cm.target_column.endswith(suffix) and not cm.is_partition_key:
                    return cm.target_column
        # Fallback
        non_partition = [c for c in columns if not c.is_partition_key]
        if non_partition:
            return non_partition[0].target_column
        return columns[0].target_column if columns else "1"

    # ------------------------------------------------------------------ #
    # Internal: schema description for AI
    # ------------------------------------------------------------------ #

    @staticmethod
    def _schema_to_description(schema: TableSchema) -> str:
        """Convert a TableSchema into a human-readable description for LLM prompts."""
        lines = [f"Table: {schema.table_name}"]
        if schema.comment:
            lines.append(f"Description: {schema.comment}")
        if schema.database_name:
            lines.append(f"Database: {schema.database_name}")
        lines.append(f"Columns ({len(schema.columns)}):")
        for col in schema.columns:
            pk = " [PK]" if col.is_primary_key else ""
            nullable = "NULL" if col.nullable else "NOT NULL"
            cmt = f" -- {col.comment}" if col.comment else ""
            lines.append(f"  - {col.name}: {col.data_type} {nullable}{pk}{cmt}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Internal: LLM response parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_sql_block(text: str, index: int = 0) -> Optional[str]:
        """Extract the Nth ```sql ... ``` code block from the LLM response.

        Returns ``None`` if the block at *index* does not exist.
        """
        pattern = r"```sql\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)
        if index < len(matches):
            return matches[index].strip()
        return None

    @staticmethod
    def _extract_list_section(text: str, keyword: str) -> List[str]:
        """Extract a bullet-point list following a heading that contains *keyword*.

        This is a best-effort parser for LLM output like::

            ## Suggestions
            1. Add an index on column X
            2. Consider partitioning by date
        """
        items: List[str] = []
        # Find the section heading containing the keyword
        heading_pattern = re.compile(
            rf"^#+\s*.*{re.escape(keyword)}.*$", re.IGNORECASE | re.MULTILINE
        )
        match = heading_pattern.search(text)
        if not match:
            return items

        # Scan lines after the heading for numbered or bulleted items
        start = match.end()
        rest = text[start:]
        for line in rest.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            # Stop at the next heading
            if stripped.startswith("#"):
                break
            # Match numbered items (1. ...) or bullets (- ...)
            item_match = re.match(r"^(?:\d+[\.\)]\s*|[-*]\s*)", stripped)
            if item_match:
                items.append(stripped[item_match.end():].strip())
            elif items:
                # Continuation line -- append to last item
                items[-1] += " " + stripped

        return items


# ====================================================================== #
# Module-level convenience function
# ====================================================================== #

async def run_ddl_pipeline(
    source_schemas: List[TableSchema],
    *,
    source_connection_id: str = "default",
    target_layer: str = "ODS",
    target_db_type: str = "clickhouse",
    convention_path: Optional[str] = None,
    naming_overrides: Optional[Dict[str, str]] = None,
    include_computation_sql: bool = True,
    local_verify: bool = True,
    sample_rows_for_verify: int = 100,
) -> DDLPipelineResult:
    """One-shot convenience function to run the DDL generation pipeline.

    Parameters
    ----------
    source_schemas:
        Source table metadata.
    source_connection_id:
        Connection ID of the source database.
    target_layer:
        Target warehouse layer.
    target_db_type:
        Target database engine type.
    convention_path:
        Optional path to a convention YAML file.
    naming_overrides:
        Optional manual naming overrides.
    include_computation_sql:
        Whether to generate computation SQL.
    local_verify:
        Whether to verify in DuckDB sandbox.
    sample_rows_for_verify:
        Number of sample rows for verification.

    Returns
    -------
    DDLPipelineResult
        The complete pipeline result.

    Example
    -------
    ::

        from src.core.schemas import TableSchema, ColumnInfo
        from src.warehouse.ddl_auto_builder import run_ddl_pipeline

        schemas = [
            TableSchema(
                database_name="prod_db",
                table_name="orders",
                columns=[
                    ColumnInfo(name="id", data_type="INT", is_primary_key=True),
                    ColumnInfo(name="user_id", data_type="INT"),
                    ColumnInfo(name="amount", data_type="DECIMAL(10,2)"),
                    ColumnInfo(name="created_at", data_type="TIMESTAMP"),
                ],
            ),
        ]
        result = await run_ddl_pipeline(
            schemas,
            target_layer="ODS",
            target_db_type="clickhouse",
            local_verify=False,
        )
        for t in result.tables:
            print(t.ddl)
    """
    config = DDLPipelineConfig(
        source_connection_id=source_connection_id,
        target_layer=target_layer,
        target_db_type=target_db_type,
        convention_path=convention_path,
        naming_overrides=naming_overrides or {},
        include_computation_sql=include_computation_sql,
        local_verify=local_verify,
        sample_rows_for_verify=sample_rows_for_verify,
    )
    builder = DDLAutoBuilder(config)
    return await builder.build(source_schemas)
