"""
DataForge AI - Warehouse layer API routes.

Endpoints for managing data-warehouse layers (ODS, DWD, DWS, ADS),
AI-assisted warehouse design, lineage inspection, and migration scripting.
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


class WarehouseLayer(StrEnum):
    """Standard data-warehouse layers."""

    ODS = "ods"
    DWD = "dwd"
    DWS = "dws"
    ADS = "ads"
    DIM = "dim"
    TMP = "tmp"


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class ColumnDefinition(BaseModel):
    """A single column definition within a warehouse table."""

    name: str = Field(..., description="Column name.")
    data_type: str = Field(..., description="SQL data type (e.g. VARCHAR(255), BIGINT).")
    nullable: bool = Field(True, description="Whether the column allows NULL values.")
    comment: str | None = Field(None, description="Column-level documentation.")
    is_partition_key: bool = Field(False, description="Whether this column is part of the partition key.")
    is_primary_key: bool = Field(False, description="Whether this column is (part of) the primary key.")


class TableCreateRequest(BaseModel):
    """Request body for creating a table in a warehouse layer."""

    table_name: str = Field(..., min_length=1, max_length=256, description="Fully-qualified or short table name.")
    connection_id: str = Field(..., description="Target connection ID.")
    database: str = Field(..., description="Target database/schema name.")
    columns: list[ColumnDefinition] = Field(..., min_length=1, description="Column definitions.")
    partition_by: list[str] | None = Field(None, description="Partition-by column names.")
    engine: str | None = Field(None, description="Storage engine (e.g. MergeTree, InnoDB).")
    table_comment: str | None = Field(None, description="Table-level documentation.")
    properties: dict[str, Any] = Field(default_factory=dict, description="Engine-specific table properties.")


class TableResponse(BaseModel):
    """Summary of a table within a warehouse layer."""

    layer: WarehouseLayer
    table_name: str
    database: str
    column_count: int
    engine: str | None = None
    table_comment: str | None = None
    created_sql: str = Field(..., description="The DDL statement that was executed.")


class WarehouseDesignRequest(BaseModel):
    """Request body for AI-assisted warehouse design."""

    source_connection_id: str = Field(..., description="Connection to the source database.")
    source_database: str = Field(..., description="Source database/schema name.")
    source_tables: list[str] = Field(default_factory=list, description="Tables to include (empty = all).")
    target_layer: WarehouseLayer = Field(WarehouseLayer.DWD, description="Target warehouse layer.")
    business_domain: str | None = Field(None, description="Business domain context (e.g. 'e-commerce', 'finance').")
    requirements: str | None = Field(None, description="Natural-language requirements describing the data model.")


class WarehouseDesignResponse(BaseModel):
    """AI-generated warehouse design proposal."""

    design_sql: list[str] = Field(..., description="Ordered list of DDL statements.")
    layer_rationale: str = Field(..., description="Explanation of layer assignments.")
    naming_conventions: dict[str, str] = Field(default_factory=dict, description="Mapping of source tables to warehouse table names.")
    recommendations: list[str] = Field(default_factory=list, description="Additional design recommendations.")


class LineageEdge(BaseModel):
    """A single edge in a lineage graph."""

    source_table: str
    target_table: str
    transformation: str | None = None


class LineageResponse(BaseModel):
    """Table lineage graph."""

    table: str
    upstream: list[LineageEdge] = Field(default_factory=list)
    downstream: list[LineageEdge] = Field(default_factory=list)


class MigrationRequest(BaseModel):
    """Request body for generating a migration script."""

    source_ddl: str = Field(..., description="Current DDL or schema definition.")
    target_ddl: str = Field(..., description="Desired DDL or schema definition.")
    dialect: str = Field("clickhouse", description="SQL dialect for the migration script.")
    connection_id: str | None = Field(None, description="Connection to execute the migration on (optional).")


class MigrationResponse(BaseModel):
    """Generated migration script."""

    migration_sql: str = Field(..., description="Executable migration DDL.")
    rollback_sql: str = Field(..., description="Rollback DDL to undo the migration.")
    warnings: list[str] = Field(default_factory=list, description="Potential issues detected.")
    estimated_impact: str | None = Field(None, description="Human-readable impact assessment.")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "/layers/{layer}/tables",
    response_model=TableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create table in warehouse layer",
    description="Create a new table in the specified data-warehouse layer. The DDL is generated based on the layer conventions and executed on the target connection.",
)
async def create_layer_table(
    layer: WarehouseLayer,
    payload: TableCreateRequest,
    manager=Depends(get_connection_manager),
) -> TableResponse:
    """Create a table in the given warehouse layer."""
    adapter = await manager.get_adapter(payload.connection_id)

    ddl = _build_create_table_ddl(layer, payload)

    try:
        await adapter.execute(ddl)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to create table: {exc}",
        ) from exc

    return TableResponse(
        layer=layer,
        table_name=payload.table_name,
        database=payload.database,
        column_count=len(payload.columns),
        engine=payload.engine,
        table_comment=payload.table_comment,
        created_sql=ddl,
    )


@router.get(
    "/layers/{layer}/tables",
    response_model=list[TableResponse],
    summary="List tables in a warehouse layer",
    description="Return all tables that belong to the specified warehouse layer on a given connection.",
)
async def list_layer_tables(
    layer: WarehouseLayer,
    connection_id: str,
    database: str,
    manager=Depends(get_connection_manager),
) -> list[TableResponse]:
    """List tables in a warehouse layer."""
    adapter = await manager.get_adapter(connection_id)
    tables = await adapter.list_tables(database)

    prefix = f"{layer.value}_"
    results: list[TableResponse] = []
    for t in tables:
        if t.get("table_name", "").startswith(prefix) or layer == WarehouseLayer.ODS:
            cols = await adapter.get_columns(database, t["table_name"])
            results.append(
                TableResponse(
                    layer=layer,
                    table_name=t["table_name"],
                    database=database,
                    column_count=len(cols),
                    engine=t.get("engine"),
                    table_comment=t.get("comment"),
                    created_sql="",
                )
            )

    return results


@router.post(
    "/design",
    response_model=WarehouseDesignResponse,
    summary="AI-assisted warehouse design",
    description="Analyse the source database schema and use AI to propose a warehouse layer design including DDL, naming conventions, and rationale.",
)
async def design_warehouse(
    payload: WarehouseDesignRequest,
    ai_provider=Depends(get_ai_provider),
    manager=Depends(get_connection_manager),
) -> WarehouseDesignResponse:
    """Generate an AI-assisted warehouse design."""
    adapter = await manager.get_adapter(payload.source_connection_id)

    source_tables_info: list[dict[str, Any]] = []
    tables_to_scan = payload.source_tables
    if not tables_to_scan:
        all_tables = await adapter.list_tables(payload.source_database)
        tables_to_scan = [t["table_name"] for t in all_tables]

    for tbl in tables_to_scan:
        columns = await adapter.get_columns(payload.source_database, tbl)
        source_tables_info.append({"table": tbl, "columns": columns})

    prompt_context = {
        "source_tables": source_tables_info,
        "target_layer": payload.target_layer.value,
        "business_domain": payload.business_domain,
        "requirements": payload.requirements,
    }

    result = await ai_provider.design_warehouse(prompt_context)

    return WarehouseDesignResponse(**result)


@router.get(
    "/lineage/{table_name}",
    response_model=LineageResponse,
    summary="Get table lineage",
    description="Return upstream and downstream lineage edges for a specific warehouse table.",
)
async def get_table_lineage(
    table_name: str,
    connection_id: str,
    database: str,
    manager=Depends(get_connection_manager),
) -> LineageResponse:
    """Retrieve the lineage graph for a table."""
    adapter = await manager.get_adapter(connection_id)
    lineage_data = await adapter.get_table_lineage(database, table_name)
    return LineageResponse(
        table=table_name,
        upstream=[LineageEdge(**e) for e in lineage_data.get("upstream", [])],
        downstream=[LineageEdge(**e) for e in lineage_data.get("downstream", [])],
    )


@router.post(
    "/migration",
    response_model=MigrationResponse,
    summary="Generate migration script",
    description="Compare two DDL definitions and generate a forward migration script, a rollback script, and an impact assessment.",
)
async def generate_migration(
    payload: MigrationRequest,
    ai_provider=Depends(get_ai_provider),
) -> MigrationResponse:
    """Generate migration and rollback SQL from DDL diff."""
    result = await ai_provider.generate_migration(
        source_ddl=payload.source_ddl,
        target_ddl=payload.target_ddl,
        dialect=payload.dialect,
    )
    return MigrationResponse(**result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_create_table_ddl(layer: WarehouseLayer, payload: TableCreateRequest) -> str:
    """Build a CREATE TABLE DDL statement from the request payload."""
    col_defs: list[str] = []
    for col in payload.columns:
        parts = [f"    {col.name} {col.data_type}"]
        if not col.nullable:
            parts.append("NOT NULL")
        if col.default_value:
            parts.append(f"DEFAULT {col.default_value}")
        if col.comment:
            parts.append(f"COMMENT '{col.comment}'")
        col_defs.append(" ".join(parts))

    pk_cols = [c.name for c in payload.columns if c.is_primary_key]
    if pk_cols:
        col_defs.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

    columns_sql = ",\n".join(col_defs)
    engine_clause = f"ENGINE = {payload.engine}" if payload.engine else ""
    partition_clause = ""
    if payload.partition_by:
        partition_clause = f"PARTITION BY ({', '.join(payload.partition_by)})"
    comment_clause = f"COMMENT '{payload.table_comment}'" if payload.table_comment else ""

    full_name = f"{payload.database}.{payload.table_name}"

    parts = [f"CREATE TABLE IF NOT EXISTS {full_name} (\n{columns_sql}\n)"]
    if engine_clause:
        parts.append(engine_clause)
    if partition_clause:
        parts.append(partition_clause)
    if comment_clause:
        parts.append(comment_clause)

    return "\n".join(parts) + ";"
