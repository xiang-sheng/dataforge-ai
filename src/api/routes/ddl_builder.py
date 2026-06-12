"""
DataForge AI - DDL auto-builder and DuckDB sandbox verification API routes.

Exposes the DDL auto-generation pipeline and local DuckDB sandbox verification
through REST endpoints.  Supports convention-driven table creation, DDL/SQL
verification, convention file validation, and table compliance checking.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.core.schemas import ColumnInfo, TableSchema
from src.db.duckdb_sandbox import (
    DDLVerifyResult,
    DuckDBSandbox,
    PipelineStep,
    PipelineVerifyResult,
    SQLVerifyResult,
)
from src.warehouse.convention_loader import (
    ConventionLoader,
    ConventionValidator,
    ValidationResult,
)
from src.warehouse.ddl_auto_builder import (
    DDLAutoBuilder,
    DDLPipelineConfig,
    DDLPipelineResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums (re-exported as plain strings for flexibility)
# ---------------------------------------------------------------------------

_TARGET_LAYERS = ("ODS", "DWD", "DWS", "ADS")
_TARGET_DB_TYPES = ("clickhouse", "hive", "doris", "mysql", "postgresql", "duckdb")


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class DDLBuildRequest(BaseModel):
    """Payload for running the full DDL generation pipeline."""

    source_connection_id: str | None = Field(
        None,
        description="Connection ID for live DB introspection.  When omitted, source_schemas must be provided.",
    )
    source_tables: list[str] = Field(
        default_factory=list,
        description="Source table names to process (used with source_connection_id).",
    )
    source_schemas: list[TableSchema] | None = Field(
        None,
        description="Provide table schemas directly instead of introspecting a live connection.",
    )
    target_layer: str = Field(
        "ODS",
        description="Target warehouse layer: ODS, DWD, DWS, or ADS.",
    )
    target_db_type: str = Field(
        "clickhouse",
        description="Target database engine: clickhouse, hive, doris, mysql, postgresql, or duckdb.",
    )
    convention_path: str | None = Field(
        None,
        description="Path to a convention YAML file.  None uses built-in defaults.",
    )
    include_computation_sql: bool = Field(
        True,
        description="Whether to also generate INSERT INTO ... SELECT computation SQL.",
    )
    local_verify: bool = Field(
        True,
        description="Whether to verify generated DDL and SQL in a local DuckDB sandbox.",
    )
    sample_rows_for_verify: int = Field(
        100,
        ge=1,
        le=10_000,
        description="Number of synthetic sample rows for DuckDB verification.",
    )
    ai_enhance: bool = Field(
        False,
        description="Whether to use an LLM to review and enhance the generated output.",
    )


class DDLVerifyRequest(BaseModel):
    """Payload for verifying DDL and/or SQL in the DuckDB sandbox."""

    ddl_statements: list[str] = Field(
        ...,
        min_length=1,
        description="DDL statements to execute (CREATE TABLE, ALTER TABLE, etc.).",
    )
    computation_sql: list[str] | None = Field(
        None,
        description="Optional computation SQL statements to run after DDL.",
    )
    sample_data: dict[str, list[dict[str, Any]]] | None = Field(
        None,
        description=(
            "Optional sample data keyed by table name.  Each value is a list of "
            "row dicts that will be inserted before running computation SQL."
        ),
    )
    verify_pipeline: bool = Field(
        False,
        description=(
            "When True, run the statements as a full pipeline verification with "
            "auto-generated sample data and result assertions."
        ),
    )


class DDLVerifyResponse(BaseModel):
    """Aggregated result of a DDL/SQL verification run."""

    ddl_results: list[DDLVerifyResult] = Field(
        default_factory=list,
        description="Per-DDL verification results.",
    )
    sql_results: list[SQLVerifyResult] = Field(
        default_factory=list,
        description="Per-SQL verification results.",
    )
    pipeline_result: PipelineVerifyResult | None = Field(
        None,
        description="Pipeline verification result when verify_pipeline=True.",
    )
    tables_created: list[str] = Field(
        default_factory=list,
        description="Names of tables successfully created in the sandbox.",
    )


class ConventionValidateRequest(BaseModel):
    """Payload for validating a convention file."""

    convention_path: str = Field(
        ...,
        description="Filesystem path to the convention YAML or Markdown file.",
    )


class ConventionValidateResponse(BaseModel):
    """Result of convention file validation."""

    is_valid: bool = Field(..., description="Whether the convention passed all checks.")
    version: str = Field(default="", description="Convention version string.")
    description: str = Field(default="", description="Convention description.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Human-readable warning messages.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Error messages (non-empty means the file is invalid).",
    )
    completeness_score: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Completeness score from 0 (many sections missing) to 100 (all sections present).",
    )
    sections_present: list[str] = Field(
        default_factory=list,
        description="Top-level convention sections that were found.",
    )
    sections_missing: list[str] = Field(
        default_factory=list,
        description="Expected sections that were not found.",
    )


class ConventionCheckTableRequest(BaseModel):
    """Payload for checking a table against conventions."""

    table_schema: TableSchema = Field(
        ...,
        description="The table schema to validate.",
    )
    convention_path: str = Field(
        ...,
        description="Path to the convention YAML or Markdown file.",
    )
    target_engine: str = Field(
        "clickhouse",
        description="Target database engine for data-type checks.",
    )


class ConventionCheckTableResponse(BaseModel):
    """Result of checking a table against conventions."""

    is_valid: bool
    score: int = Field(default=100, ge=0, le=100)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /ddl/build — Run the full DDL generation pipeline
# ---------------------------------------------------------------------------


@router.post(
    "/build",
    response_model=DDLPipelineResult,
    summary="Run the full DDL generation pipeline",
    description=(
        "Execute the automated DDL generation pipeline.  Provide either a "
        "source connection ID (for live DB introspection) or source schemas "
        "directly.  The pipeline applies naming conventions, maps data types, "
        "generates CREATE TABLE DDL and computation SQL, and optionally "
        "verifies everything in a local DuckDB sandbox."
    ),
)
async def build_ddl(payload: DDLBuildRequest) -> DDLPipelineResult:
    """Run the DDL auto-generation pipeline and return the result."""

    # -- Validate inputs --------------------------------------------------- #
    if not payload.source_connection_id and not payload.source_schemas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Either 'source_connection_id' (for live introspection) or "
                "'source_schemas' (for direct schema input) must be provided."
            ),
        )

    target_layer_upper = payload.target_layer.upper()
    if target_layer_upper not in _TARGET_LAYERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid target_layer '{payload.target_layer}'.  "
                f"Must be one of: {', '.join(_TARGET_LAYERS)}."
            ),
        )

    target_db_lower = payload.target_db_type.lower()
    if target_db_lower not in _TARGET_DB_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid target_db_type '{payload.target_db_type}'.  "
                f"Must be one of: {', '.join(_TARGET_DB_TYPES)}."
            ),
        )

    # -- Build the pipeline config ----------------------------------------- #
    config = DDLPipelineConfig(
        source_connection_id=payload.source_connection_id or "direct",
        source_tables=payload.source_tables,
        target_layer=target_layer_upper,
        target_db_type=target_db_lower,
        convention_path=payload.convention_path,
        include_computation_sql=payload.include_computation_sql,
        local_verify=payload.local_verify,
        sample_rows_for_verify=payload.sample_rows_for_verify,
    )

    builder = DDLAutoBuilder(config)

    # -- Resolve source schemas ------------------------------------------- #
    source_schemas: list[TableSchema] = []

    if payload.source_schemas:
        source_schemas = payload.source_schemas
    elif payload.source_connection_id:
        # Introspect from a live connection
        from src.api.deps import get_connection_manager

        manager = get_connection_manager()
        connection = await manager.get(payload.source_connection_id)
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connection '{payload.source_connection_id}' not found.",
            )

        try:
            adapter = await manager.get_adapter(payload.source_connection_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to connect to '{payload.source_connection_id}': {exc}",
            ) from exc

        tables_to_scan = payload.source_tables
        if not tables_to_scan:
            all_tables = await adapter.list_tables(
                connection.get("database", connection.get("default_database", ""))
            )
            tables_to_scan = [t["table_name"] for t in all_tables]

        database_name = connection.get("database", connection.get("default_database", ""))
        for tbl_name in tables_to_scan:
            try:
                columns = await adapter.get_columns(database_name, tbl_name)
                source_schemas.append(
                    TableSchema(
                        connection_id=payload.source_connection_id,
                        database_name=database_name,
                        table_name=tbl_name,
                        columns=[ColumnInfo(**c) for c in columns],
                    )
                )
            except Exception as exc:
                logger.warning("Failed to introspect table '%s': %s", tbl_name, exc)

    if not source_schemas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No source schemas were resolved.  Check your connection and table names.",
        )

    # -- Run the pipeline -------------------------------------------------- #
    result = await builder.build(source_schemas)

    # -- Optional AI enhancement ------------------------------------------- #
    if payload.ai_enhance and result.tables:
        for table_result in result.tables:
            matching_schema = next(
                (s for s in source_schemas if s.table_name == table_result.source_table),
                None,
            )
            if matching_schema:
                try:
                    ai_result = await builder.ai_enhance(
                        matching_schema,
                        table_result.ddl,
                        table_result.computation_sql or "",
                    )
                    if ai_result.enhanced_ddl:
                        table_result.ddl = ai_result.enhanced_ddl
                    if ai_result.enhanced_sql:
                        table_result.computation_sql = ai_result.enhanced_sql
                    if ai_result.suggestions:
                        table_result.convention_violations.append(
                            {
                                "type": "ai_suggestion",
                                "message": "; ".join(ai_result.suggestions),
                                "auto_fixed": False,
                            }
                        )
                except Exception as exc:
                    logger.warning("AI enhancement failed for '%s': %s", table_result.source_table, exc)

    return result


# ---------------------------------------------------------------------------
# POST /ddl/verify — Verify DDL and/or SQL in DuckDB sandbox
# ---------------------------------------------------------------------------


@router.post(
    "/verify",
    response_model=DDLVerifyResponse,
    summary="Verify DDL and/or SQL in DuckDB sandbox",
    description=(
        "Execute DDL statements and optional computation SQL in an isolated "
        "DuckDB sandbox.  The sandbox translates engine-specific syntax "
        "(ClickHouse, Hive, MySQL) to DuckDB automatically.  Returns "
        "per-statement verification results."
    ),
)
async def verify_ddl(payload: DDLVerifyRequest) -> DDLVerifyResponse:
    """Verify DDL/SQL in the DuckDB sandbox."""
    sandbox = DuckDBSandbox()
    sandbox.open()

    try:
        # -- Pipeline mode ------------------------------------------------- #
        if payload.verify_pipeline:
            steps: list[PipelineStep] = []

            # DDL steps
            for ddl in payload.ddl_statements:
                steps.append(PipelineStep(step_type="ddl", sql=ddl))

            # Insert sample data steps
            if payload.sample_data:
                for table_name, rows in payload.sample_data.items():
                    steps.append(
                        PipelineStep(
                            step_type="insert_sample",
                            table_name=table_name,
                            num_sample_rows=len(rows),
                        )
                    )
            else:
                # Auto-generate sample data for each DDL-created table
                for ddl in payload.ddl_statements:
                    table_name = DuckDBSandbox._extract_table_name(ddl)
                    if table_name:
                        steps.append(
                            PipelineStep(
                                step_type="insert_sample",
                                table_name=table_name,
                                num_sample_rows=100,
                            )
                        )

            # Computation SQL steps
            if payload.computation_sql:
                for sql in payload.computation_sql:
                    steps.append(PipelineStep(step_type="computation_sql", sql=sql))

            pipeline_result = sandbox.verify_pipeline(steps)

            # Collect table names
            tables_created = sandbox.list_tables()

            return DDLVerifyResponse(
                pipeline_result=pipeline_result,
                tables_created=tables_created,
            )

        # -- Standard mode ------------------------------------------------- #
        ddl_results: list[DDLVerifyResult] = []
        for ddl in payload.ddl_statements:
            result = sandbox.verify_ddl(ddl)
            ddl_results.append(result)

        # Insert sample data if provided
        if payload.sample_data:
            for table_name, rows in payload.sample_data.items():
                try:
                    sandbox.insert_from_dicts(table_name, rows)
                except Exception as exc:
                    logger.warning(
                        "Failed to insert sample data for '%s': %s",
                        table_name,
                        exc,
                    )

        # Run computation SQL
        sql_results: list[SQLVerifyResult] = []
        if payload.computation_sql:
            for sql in payload.computation_sql:
                sql_result = sandbox.verify_computation_sql(sql)
                sql_results.append(sql_result)

        # Collect table names
        tables_created = [
            r.table_name for r in ddl_results if r.success and r.table_name
        ]

        return DDLVerifyResponse(
            ddl_results=ddl_results,
            sql_results=sql_results,
            tables_created=tables_created,
        )

    finally:
        sandbox.close()


# ---------------------------------------------------------------------------
# POST /ddl/convention/validate — Validate a convention file
# ---------------------------------------------------------------------------


@router.post(
    "/convention/validate",
    response_model=ConventionValidateResponse,
    summary="Validate a convention file",
    description=(
        "Parse and validate a convention YAML or Markdown file.  Returns "
        "warnings, errors, a completeness score, and lists of present and "
        "missing sections."
    ),
)
async def validate_convention(
    payload: ConventionValidateRequest,
) -> ConventionValidateResponse:
    """Validate a convention file and return the result."""
    loader = ConventionLoader()
    errors: list[str] = []
    warnings: list[str] = []

    convention_path = payload.convention_path
    if not Path(convention_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Convention file not found: '{convention_path}'.",
        )

    try:
        convention = loader.load_auto(convention_path)
    except Exception as exc:
        return ConventionValidateResponse(
            is_valid=False,
            errors=[str(exc)],
            completeness_score=0,
        )

    # Validate the loaded convention
    warnings = loader.validate_convention(convention)

    # Determine completeness
    expected_sections = [
        "naming",
        "data_types",
        "partition",
        "comments",
        "quality",
        "storage",
    ]
    conv_dict = convention.model_dump()
    sections_present: list[str] = []
    sections_missing: list[str] = []

    for section in expected_sections:
        section_data = conv_dict.get(section)
        if section_data and section_data != {} and section_data is not None:
            # Check if the section has meaningful content (not all defaults)
            sections_present.append(section)
        else:
            sections_missing.append(section)

    # Compute completeness score
    completeness_score = int(len(sections_present) / len(expected_sections) * 100)

    # Treat validation warnings with "error" keywords as errors
    for w in warnings:
        if "error" in w.lower() or "fail" in w.lower():
            errors.append(w)

    return ConventionValidateResponse(
        is_valid=len(errors) == 0,
        version=convention.version,
        description=convention.description,
        warnings=warnings,
        errors=errors,
        completeness_score=completeness_score,
        sections_present=sections_present,
        sections_missing=sections_missing,
    )


# ---------------------------------------------------------------------------
# GET /ddl/convention/template — Download a convention YAML template
# ---------------------------------------------------------------------------

_CONVENTION_TEMPLATE_YAML = """\
# DataForge AI -- Convention Template
# Copy this file, rename it (e.g. my_convention.yaml), and customise the values.
# Load it via the /ddl/build endpoint (convention_path parameter) or the
# /ddl/convention/validate endpoint to check correctness.

version: "1.0.0"
description: "My data warehouse table creation convention"

naming:
  table_pattern: "{prefix}{domain}_{description}{suffix}"
  case_style: "snake_case"
  prefix_rules:
    ODS: "ods_"
    DWD: "dwd_"
    DWS: "dws_"
    ADS: "ads_"
    DIM: "dim_"
  suffix_rules:
    daily_increment: "_di"
    full_snapshot: "_df"
    dimension: "_dim"
    fact: "_fact"
    aggregation: "_agg"
  reserved_words:
    - "order"
    - "group"
    - "select"
    - "table"
    - "index"
    - "user"
    - "key"

data_types:
  logical_to_physical:
    STRING:
      clickhouse: "String"
      hive: "STRING"
      mysql: "VARCHAR(255)"
      postgresql: "VARCHAR(255)"
      doris: "VARCHAR(255)"
      duckdb: "VARCHAR"
    INTEGER:
      clickhouse: "Int32"
      hive: "INT"
      mysql: "INT"
      postgresql: "INTEGER"
      doris: "INT"
      duckdb: "INTEGER"
    BIGINT:
      clickhouse: "Int64"
      hive: "BIGINT"
      mysql: "BIGINT"
      postgresql: "BIGINT"
      doris: "BIGINT"
      duckdb: "BIGINT"
    DECIMAL:
      clickhouse: "Decimal(18, 2)"
      hive: "DECIMAL(18, 2)"
      mysql: "DECIMAL(18, 2)"
      postgresql: "NUMERIC(18, 2)"
      doris: "DECIMAL(18, 2)"
      duckdb: "DECIMAL(18, 2)"
    BOOLEAN:
      clickhouse: "UInt8"
      hive: "BOOLEAN"
      mysql: "TINYINT(1)"
      postgresql: "BOOLEAN"
      doris: "BOOLEAN"
      duckdb: "BOOLEAN"
    DATE:
      clickhouse: "Date"
      hive: "DATE"
      mysql: "DATE"
      postgresql: "DATE"
      doris: "DATE"
      duckdb: "DATE"
    TIMESTAMP:
      clickhouse: "DateTime"
      hive: "TIMESTAMP"
      mysql: "DATETIME"
      postgresql: "TIMESTAMP"
      doris: "DATETIME"
      duckdb: "TIMESTAMP"
  preferred_types:
    id_column: "BIGINT"
    amount: "DECIMAL(18, 2)"
    quantity: "INTEGER"
    flag: "BOOLEAN"
    code: "VARCHAR(50)"
    description: "VARCHAR(500)"
  forbidden_types:
    - "TEXT"
    - "BLOB"
    - "MEDIUMTEXT"

partition:
  default_partition_column: "dt"
  partition_by_layer:
    ODS: "dt"
    DWD: "dt"
    DWS: "stat_date"
    ADS: "stat_date"
  retention_days_by_layer:
    ODS: 90
    DWD: 365
    DWS: 730
    ADS: 1095
  granularity: "daily"

comments:
  table_comment_required: true
  column_comment_required: true
  table_comment_pattern: "[{layer}] {description}"
  column_comment_min_length: 3

quality:
  primary_key_required: true
  not_null_columns:
    - "*_id"
    - "*_key"
    - "dt"
    - "etl_time"
  unique_constraints:
    - "*_sk"
  check_constraints:
    - column_pattern: "amount"
      rule: ">= 0"
    - column_pattern: "quantity"
      rule: ">= 0"

storage:
  default_format_by_engine:
    clickhouse: "MergeTree"
    hive: "ORC"
    doris: ""
    mysql: "InnoDB"
    postgresql: ""
    duckdb: ""
  compression_by_engine:
    clickhouse: "LZ4"
    hive: "SNAPPY"
  index_strategy:
    bitmap: "for low-cardinality string columns (< 1000 distinct values)"
    btree: "for primary keys and foreign keys"
    minmax: "for ClickHouse partition/order key columns"
"""


@router.get(
    "/convention/template",
    response_class=PlainTextResponse,
    summary="Download a convention YAML template",
    description=(
        "Return a comprehensive example convention YAML file that "
        "demonstrates all available settings.  Use this as a starting "
        "point for creating your own convention."
    ),
)
async def get_convention_template() -> PlainTextResponse:
    """Return the convention YAML template as a downloadable file."""
    return PlainTextResponse(
        content=_CONVENTION_TEMPLATE_YAML,
        media_type="text/yaml; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="convention_template.yaml"',
        },
    )


# ---------------------------------------------------------------------------
# POST /ddl/convention/check-table — Check a table against conventions
# ---------------------------------------------------------------------------


@router.post(
    "/convention/check-table",
    response_model=ConventionCheckTableResponse,
    summary="Check a table against conventions",
    description=(
        "Validate a table schema against a loaded convention file.  Returns "
        "a compliance score (0-100), a list of violations with severity "
        "levels, and advisory warnings."
    ),
)
async def check_table_against_convention(
    payload: ConventionCheckTableRequest,
) -> ConventionCheckTableResponse:
    """Check a table schema against a convention file."""
    convention_path = payload.convention_path
    if not Path(convention_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Convention file not found: '{convention_path}'.",
        )

    # Load the convention
    loader = ConventionLoader()
    try:
        convention = loader.load_auto(convention_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to load convention from '{convention_path}': {exc}",
        ) from exc

    # Validate the table
    validator = ConventionValidator()
    result: ValidationResult = validator.validate_table(
        schema=payload.table_schema,
        convention=convention,
        target_engine=payload.target_engine,
    )

    # Serialise violations to dicts
    violation_dicts: list[dict[str, Any]] = [
        {
            "severity": v.severity,
            "rule": v.rule,
            "message": v.message,
            "location": v.location,
            "suggestion": v.suggestion,
        }
        for v in result.violations
    ]

    return ConventionCheckTableResponse(
        is_valid=result.is_valid,
        score=result.score,
        violations=violation_dicts,
        warnings=result.warnings,
    )
