"""
DataForge AI - Data lineage API routes.

Endpoints for table-level and column-level lineage tracking,
SQL-based lineage analysis, and impact analysis.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import get_ai_provider, get_connection_manager

# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class LineageNode(BaseModel):
    """A node in a lineage graph."""

    id: str = Field(..., description="Unique node identifier (usually schema.table).")
    table_name: str = Field(..., description="Fully qualified table name.")
    node_type: str = Field("table", description="Node type: 'table', 'view', 'cte', 'subquery'.")
    layer: str | None = Field(None, description="Warehouse layer (ods, dwd, dws, ads).")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata.")


class LineageEdge(BaseModel):
    """A directed edge in a lineage graph."""

    source_id: str = Field(..., description="Source node ID.")
    target_id: str = Field(..., description="Target node ID.")
    edge_type: str = Field("data_flow", description="Edge type: 'data_flow', 'derivation', 'reference'.")
    transformation: str | None = Field(None, description="Description of the transformation applied.")
    sql_snippet: str | None = Field(None, description="SQL fragment that creates this relationship.")


class LineageGraph(BaseModel):
    """Complete lineage graph for a table."""

    root: str = Field(..., description="Root table ID.")
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    depth: int = Field(0, description="Maximum depth of the lineage traversal.")


class ColumnLineageNode(BaseModel):
    """A node in column-level lineage."""

    table_name: str
    column_name: str
    data_type: str | None = None


class ColumnLineageEdge(BaseModel):
    """A directed edge in column-level lineage."""

    source_table: str
    source_column: str
    target_table: str
    target_column: str
    transformation: str | None = None


class ColumnLineageResponse(BaseModel):
    """Column-level lineage for a specific column."""

    table_name: str
    column_name: str
    upstream: list[ColumnLineageEdge] = Field(default_factory=list)
    downstream: list[ColumnLineageEdge] = Field(default_factory=list)


class LineageAnalyzeRequest(BaseModel):
    """Request to analyse SQL for lineage extraction."""

    sql: str = Field(..., min_length=1, description="SQL statement(s) to analyse for lineage.")
    dialect: str = Field("clickhouse", description="SQL dialect of the provided statements.")
    default_schema: str | None = Field(None, description="Default schema name for unqualified table references.")


class LineageAnalyzeResponse(BaseModel):
    """Result of SQL lineage analysis."""

    source_tables: list[str] = Field(default_factory=list, description="Tables read by the SQL.")
    target_tables: list[str] = Field(default_factory=list, description="Tables written to by the SQL.")
    edges: list[LineageEdge] = Field(default_factory=list)
    column_mappings: list[ColumnLineageEdge] = Field(default_factory=list, description="Column-level mappings detected.")


class ImpactAnalysisResponse(BaseModel):
    """Impact analysis for a table change."""

    table_id: str
    direct_downstream: list[str] = Field(default_factory=list, description="Tables directly dependent on this table.")
    indirect_downstream: list[str] = Field(default_factory=list, description="Tables transitively dependent on this table.")
    total_affected_count: int = Field(0, description="Total number of affected downstream tables.")
    affected_pipelines: list[str] = Field(default_factory=list, description="ETL pipelines that would be impacted.")
    risk_level: str = Field("low", description="Risk level: 'low', 'medium', 'high', 'critical'.")
    recommendations: list[str] = Field(default_factory=list, description="Mitigation recommendations.")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get(
    "/table/{table_id}",
    response_model=LineageGraph,
    summary="Get table lineage graph",
    description="Return the full upstream and downstream lineage graph for a table, traversing all warehouse layers up to the specified depth.",
)
async def get_table_lineage(
    table_id: str,
    connection_id: str,
    database: str,
    max_depth: int = 5,
    direction: str = "both",
    manager=Depends(get_connection_manager),
) -> LineageGraph:
    """Retrieve the lineage graph for a table."""
    adapter = await manager.get_adapter(connection_id)

    try:
        lineage_data = await adapter.get_lineage_graph(
            database=database,
            table_name=table_id,
            max_depth=max_depth,
            direction=direction,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve lineage for '{table_id}': {exc}",
        ) from exc

    return LineageGraph(
        root=table_id,
        nodes=[LineageNode(**n) for n in lineage_data.get("nodes", [])],
        edges=[LineageEdge(**e) for e in lineage_data.get("edges", [])],
        depth=lineage_data.get("depth", 0),
    )


@router.get(
    "/column/{table_id}/{column_name}",
    response_model=ColumnLineageResponse,
    summary="Get column-level lineage",
    description="Trace the origin and downstream usage of a specific column across the data warehouse.",
)
async def get_column_lineage(
    table_id: str,
    column_name: str,
    connection_id: str,
    database: str,
    max_depth: int = 5,
    manager=Depends(get_connection_manager),
) -> ColumnLineageResponse:
    """Retrieve column-level lineage."""
    adapter = await manager.get_adapter(connection_id)

    try:
        lineage_data = await adapter.get_column_lineage(
            database=database,
            table_name=table_id,
            column_name=column_name,
            max_depth=max_depth,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve column lineage for '{table_id}.{column_name}': {exc}",
        ) from exc

    return ColumnLineageResponse(
        table_name=table_id,
        column_name=column_name,
        upstream=[ColumnLineageEdge(**e) for e in lineage_data.get("upstream", [])],
        downstream=[ColumnLineageEdge(**e) for e in lineage_data.get("downstream", [])],
    )


@router.post(
    "/analyze",
    response_model=LineageAnalyzeResponse,
    summary="Analyse SQL for lineage",
    description="Parse SQL statement(s) and extract table-level and column-level lineage relationships without executing the queries.",
)
async def analyze_sql_lineage(
    payload: LineageAnalyzeRequest,
    ai_provider=Depends(get_ai_provider),
) -> LineageAnalyzeResponse:
    """Analyse SQL to extract lineage information."""
    context = {
        "sql": payload.sql,
        "dialect": payload.dialect,
        "default_schema": payload.default_schema,
    }

    result = await ai_provider.analyze_lineage(context)

    return LineageAnalyzeResponse(
        source_tables=result.get("source_tables", []),
        target_tables=result.get("target_tables", []),
        edges=[LineageEdge(**e) for e in result.get("edges", [])],
        column_mappings=[ColumnLineageEdge(**m) for m in result.get("column_mappings", [])],
    )


@router.get(
    "/impact/{table_id}",
    response_model=ImpactAnalysisResponse,
    summary="Impact analysis",
    description="Assess the downstream impact of modifying or dropping a table. Identifies direct and transitive dependents, affected pipelines, and provides a risk rating.",
)
async def get_impact_analysis(
    table_id: str,
    connection_id: str,
    database: str,
    manager=Depends(get_connection_manager),
) -> ImpactAnalysisResponse:
    """Perform impact analysis on a table."""
    adapter = await manager.get_adapter(connection_id)

    try:
        impact_data = await adapter.get_impact_analysis(
            database=database,
            table_name=table_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to perform impact analysis for '{table_id}': {exc}",
        ) from exc

    return ImpactAnalysisResponse(
        table_id=table_id,
        direct_downstream=impact_data.get("direct_downstream", []),
        indirect_downstream=impact_data.get("indirect_downstream", []),
        total_affected_count=impact_data.get("total_affected_count", 0),
        affected_pipelines=impact_data.get("affected_pipelines", []),
        risk_level=impact_data.get("risk_level", "low"),
        recommendations=impact_data.get("recommendations", []),
    )
