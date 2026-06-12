"""
DataForge AI - SQL generation and manipulation API routes.

Endpoints for AI-powered SQL generation from natural language, SQL
explanation, optimization, cross-dialect translation, and execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import get_ai_provider, get_connection_manager

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SQLDialect(StrEnum):
    """Supported SQL dialects."""

    CLICKHOUSE = "clickhouse"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    REDSHIFT = "redshift"
    DORIS = "doris"
    STARROCKS = "starrocks"
    SPARK_SQL = "spark_sql"
    HIVE = "hive"


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class SQLGenerateRequest(BaseModel):
    """Request body for generating SQL from natural language."""

    prompt: str = Field(..., min_length=1, description="Natural-language description of the desired query.")
    dialect: SQLDialect = Field(SQLDialect.CLICKHOUSE, description="Target SQL dialect.")
    schema_context: str | None = Field(
        None,
        description="DDL or schema description to ground the generation (if omitted, the AI uses the connection metadata).",
    )
    connection_id: str | None = Field(
        None,
        description="Connection ID whose schema should be used as context.",
    )
    database: str | None = Field(None, description="Database/schema to introspect for context.")
    max_results: int | None = Field(None, ge=1, description="Desired LIMIT clause value.")
    additional_instructions: str | None = Field(None, description="Extra instructions (e.g. 'use CTEs', 'avoid subqueries').")


class SQLGenerateResponse(BaseModel):
    """AI-generated SQL with explanation."""

    sql: str = Field(..., description="Generated SQL statement.")
    explanation: str = Field("", description="Human-readable explanation of the query logic.")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="AI confidence score.")
    warnings: list[str] = Field(default_factory=list, description="Potential issues with the generated SQL.")
    alternative_queries: list[str] = Field(default_factory=list, description="Alternative formulations of the same query.")


class SQLExplainRequest(BaseModel):
    """Request body for explaining a SQL statement."""

    sql: str = Field(..., min_length=1, description="SQL statement to explain.")
    dialect: SQLDialect = Field(SQLDialect.CLICKHOUSE, description="Dialect of the provided SQL.")
    detail_level: str = Field("standard", description="Detail level: 'brief', 'standard', or 'detailed'.")


class SQLExplainResponse(BaseModel):
    """Structured explanation of a SQL statement."""

    summary: str = Field(..., description="One-sentence summary.")
    step_by_step: list[str] = Field(default_factory=list, description="Step-by-step breakdown of the query logic.")
    tables_used: list[str] = Field(default_factory=list, description="Tables referenced in the query.")
    estimated_complexity: str = Field("medium", description="Estimated complexity: 'low', 'medium', 'high'.")
    performance_notes: list[str] = Field(default_factory=list, description="Potential performance considerations.")


class SQLOptimizeRequest(BaseModel):
    """Request body for optimizing a SQL statement."""

    sql: str = Field(..., min_length=1, description="SQL statement to optimize.")
    dialect: SQLDialect = Field(SQLDialect.CLICKHOUSE, description="Target dialect.")
    schema_context: str | None = Field(None, description="DDL or schema information for optimization context.")
    optimization_goals: list[str] = Field(
        default_factory=lambda: ["performance"],
        description="Optimization priorities: 'performance', 'readability', 'cost'.",
    )


class SQLOptimizeResponse(BaseModel):
    """Optimized SQL with analysis."""

    original_sql: str
    optimized_sql: str = Field(..., description="The optimized SQL statement.")
    changes: list[str] = Field(default_factory=list, description="List of changes made and why.")
    estimated_improvement: str | None = Field(None, description="Estimated performance improvement description.")
    warnings: list[str] = Field(default_factory=list, description="Things to be aware of with the optimized query.")


class SQLTranslateRequest(BaseModel):
    """Request body for translating SQL between dialects."""

    sql: str = Field(..., min_length=1, description="Source SQL statement.")
    source_dialect: SQLDialect = Field(..., description="Dialect of the source SQL.")
    target_dialect: SQLDialect = Field(..., description="Desired target dialect.")
    preserve_comments: bool = Field(True, description="Whether to preserve SQL comments during translation.")


class SQLTranslateResponse(BaseModel):
    """Translated SQL with notes."""

    original_sql: str
    translated_sql: str = Field(..., description="SQL translated to the target dialect.")
    source_dialect: SQLDialect
    target_dialect: SQLDialect
    translation_notes: list[str] = Field(default_factory=list, description="Notes about dialect-specific adaptations.")
    unsupported_features: list[str] = Field(default_factory=list, description="Features that could not be translated.")


class SQLExecuteRequest(BaseModel):
    """Request body for executing SQL against a connection."""

    sql: str = Field(..., min_length=1, description="SQL statement to execute.")
    connection_id: str = Field(..., description="Target connection ID.")
    database: str | None = Field(None, description="Database/schema context.")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Parameterized query values.")
    max_rows: int = Field(1000, ge=1, le=100000, description="Maximum number of rows to return.")
    timeout_seconds: int = Field(30, ge=1, le=600, description="Query timeout in seconds.")


class SQLExecuteResponse(BaseModel):
    """Result of a SQL execution."""

    columns: list[str] = Field(default_factory=list, description="Column names in the result set.")
    rows: list[list[Any]] = Field(default_factory=list, description="Result rows (list of lists).")
    row_count: int = Field(0, description="Number of rows returned.")
    execution_time_ms: float = Field(0.0, description="Server-side execution time in milliseconds.")
    truncated: bool = Field(False, description="Whether the result was truncated due to max_rows.")
    statement_type: str | None = Field(None, description="Type of statement (SELECT, INSERT, DDL, etc.).")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "/generate",
    response_model=SQLGenerateResponse,
    summary="Generate SQL from natural language",
    description="Convert a natural-language prompt into a SQL statement using AI. Optionally grounded by a live database schema or user-supplied DDL.",
)
async def generate_sql(
    payload: SQLGenerateRequest,
    ai_provider=Depends(get_ai_provider),
    manager=Depends(get_connection_manager),
) -> SQLGenerateResponse:
    """Generate SQL from a natural-language prompt."""
    schema_context = payload.schema_context

    if payload.connection_id and not schema_context:
        adapter = await manager.get_adapter(payload.connection_id)
        db = payload.database or ""
        tables = await adapter.list_tables(db)
        schema_parts: list[str] = []
        for t in tables[:20]:  # limit introspection to 20 tables
            cols = await adapter.get_columns(db, t["table_name"])
            col_defs = ", ".join(f"{c['name']} {c['data_type']}" for c in cols)
            schema_parts.append(f"CREATE TABLE {t['table_name']} ({col_defs});")
        schema_context = "\n".join(schema_parts)

    context = {
        "prompt": payload.prompt,
        "dialect": payload.dialect.value,
        "schema_context": schema_context,
        "max_results": payload.max_results,
        "additional_instructions": payload.additional_instructions,
    }

    result = await ai_provider.generate_sql(context)
    return SQLGenerateResponse(**result)


@router.post(
    "/explain",
    response_model=SQLExplainResponse,
    summary="Explain a SQL statement",
    description="Use AI to produce a structured, step-by-step explanation of a SQL query, including complexity assessment and performance notes.",
)
async def explain_sql(
    payload: SQLExplainRequest,
    ai_provider=Depends(get_ai_provider),
) -> SQLExplainResponse:
    """Explain a SQL statement."""
    context = {
        "sql": payload.sql,
        "dialect": payload.dialect.value,
        "detail_level": payload.detail_level,
    }

    result = await ai_provider.explain_sql(context)
    return SQLExplainResponse(**result)


@router.post(
    "/optimize",
    response_model=SQLOptimizeResponse,
    summary="Optimize a SQL statement",
    description="Analyse and optimize a SQL query for the specified goals (performance, readability, cost), returning the improved query with a change log.",
)
async def optimize_sql(
    payload: SQLOptimizeRequest,
    ai_provider=Depends(get_ai_provider),
) -> SQLOptimizeResponse:
    """Optimize a SQL statement."""
    context = {
        "sql": payload.sql,
        "dialect": payload.dialect.value,
        "schema_context": payload.schema_context,
        "optimization_goals": payload.optimization_goals,
    }

    result = await ai_provider.optimize_sql(context)
    return SQLOptimizeResponse(original_sql=payload.sql, **result)


@router.post(
    "/translate",
    response_model=SQLTranslateResponse,
    summary="Translate SQL between dialects",
    description="Translate a SQL statement from one dialect to another, handling syntax differences, function mappings, and unsupported features.",
)
async def translate_sql(
    payload: SQLTranslateRequest,
    ai_provider=Depends(get_ai_provider),
) -> SQLTranslateResponse:
    """Translate SQL between dialects."""
    if payload.source_dialect == payload.target_dialect:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and target dialects must be different.",
        )

    context = {
        "sql": payload.sql,
        "source_dialect": payload.source_dialect.value,
        "target_dialect": payload.target_dialect.value,
        "preserve_comments": payload.preserve_comments,
    }

    result = await ai_provider.translate_sql(context)
    return SQLTranslateResponse(
        original_sql=payload.sql,
        source_dialect=payload.source_dialect,
        target_dialect=payload.target_dialect,
        **result,
    )


@router.post(
    "/execute",
    response_model=SQLExecuteResponse,
    summary="Execute SQL on a connection",
    description="Execute a SQL statement against a live database connection and return the result set with execution metadata.",
)
async def execute_sql(
    payload: SQLExecuteRequest,
    manager=Depends(get_connection_manager),
) -> SQLExecuteResponse:
    """Execute SQL on a connection and return results."""
    try:
        adapter = await manager.get_adapter(payload.connection_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cannot connect to '{payload.connection_id}': {exc}",
        ) from exc

    try:
        result = await adapter.execute_query(
            sql=payload.sql,
            database=payload.database,
            parameters=payload.parameters,
            max_rows=payload.max_rows,
            timeout_seconds=payload.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Query execution failed: {exc}",
        ) from exc

    return SQLExecuteResponse(**result)
