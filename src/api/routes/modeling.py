"""
DataForge AI - AI modeling API routes.

Endpoints for AI-driven data modeling including dimensional modelling,
model review, partitioning advice, and general modelling suggestions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.deps import get_ai_provider

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ModelingType(StrEnum):
    """Supported modelling paradigms."""

    DIMENSIONAL = "dimensional"
    DATA_VAULT = "data_vault"
    ONE_BIG_TABLE = "one_big_table"
    ACTIVITY_SCHEMA = "activity_schema"


class ReviewSeverity(StrEnum):
    """Severity level for model review findings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class TableSchemaInput(BaseModel):
    """Schema of a single table provided as modelling input."""

    table_name: str = Field(..., description="Table name.")
    columns: list[dict[str, Any]] = Field(..., description="List of column definitions with name, data_type, nullable, etc.")
    primary_keys: list[str] = Field(default_factory=list, description="Primary key column names.")
    foreign_keys: list[dict[str, str]] = Field(default_factory=list, description="Foreign key relationships: column, ref_table, ref_column.")
    sample_row_count: int | None = Field(None, description="Approximate row count for sizing context.")
    comment: str | None = Field(None, description="Table-level documentation or description.")


class ModelingSuggestRequest(BaseModel):
    """Request for AI modelling suggestions."""

    tables: list[TableSchemaInput] = Field(..., min_length=1, description="Source tables to analyse.")
    business_context: str | None = Field(None, description="Natural-language description of the business domain.")
    modeling_type: ModelingType = Field(ModelingType.DIMENSIONAL, description="Preferred modelling paradigm.")
    target_platform: str | None = Field(None, description="Target platform (e.g. 'ClickHouse', 'Snowflake').")


class ModelingSuggestion(BaseModel):
    """A single modelling suggestion."""

    category: str = Field(..., description="Category (e.g. 'naming', 'normalization', 'denormalization').")
    description: str = Field(..., description="Human-readable suggestion.")
    affected_tables: list[str] = Field(default_factory=list, description="Tables impacted by this suggestion.")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="AI confidence score.")


class ModelingSuggestResponse(BaseModel):
    """Response from the modelling suggestion endpoint."""

    suggestions: list[ModelingSuggestion] = Field(default_factory=list)
    proposed_ddl: list[str] = Field(default_factory=list, description="Proposed DDL statements implementing the suggestions.")
    summary: str = Field("", description="High-level summary of the modelling proposal.")


class DimensionalModelRequest(BaseModel):
    """Request for designing a dimensional (star/snowflake) model."""

    tables: list[TableSchemaInput] = Field(..., min_length=1, description="Source tables.")
    business_process: str = Field(..., description="The business process being modelled (e.g. 'order fulfilment').")
    grain: str | None = Field(None, description="Desired grain of the fact table (e.g. 'one row per order line').")
    preferred_schema: str = Field("star", description="Schema type: 'star' or 'snowflake'.")
    target_platform: str | None = Field(None, description="Target warehouse platform.")


class FactTable(BaseModel):
    """A proposed fact table."""

    name: str
    grain: str
    measures: list[dict[str, str]] = Field(default_factory=list, description="Measure columns with name and aggregation type.")
    foreign_keys: list[dict[str, str]] = Field(default_factory=list, description="FK references to dimension tables.")
    ddl: str = ""


class DimensionTable(BaseModel):
    """A proposed dimension table."""

    name: str
    attributes: list[dict[str, str]] = Field(default_factory=list, description="Dimension attributes with name and data type.")
    surrogate_key: str = ""
    ddl: str = ""


class DimensionalModelResponse(BaseModel):
    """AI-designed dimensional model."""

    fact_tables: list[FactTable] = Field(default_factory=list)
    dimension_tables: list[DimensionTable] = Field(default_factory=list)
    rationale: str = Field("", description="Explanation of design decisions.")
    warnings: list[str] = Field(default_factory=list, description="Potential issues or caveats.")


class ModelReviewRequest(BaseModel):
    """Request to review an existing data model."""

    tables: list[TableSchemaInput] = Field(..., min_length=1, description="Tables to review.")
    warehouse_layer: str | None = Field(None, description="Warehouse layer context (e.g. 'dwd', 'dws').")
    standards: str | None = Field(None, description="Team-specific modelling standards to check against.")


class ReviewFinding(BaseModel):
    """A single review finding."""

    severity: ReviewSeverity
    table: str
    message: str
    recommendation: str | None = None


class ModelReviewResponse(BaseModel):
    """Response from the model review endpoint."""

    findings: list[ReviewFinding] = Field(default_factory=list)
    score: float = Field(0.0, ge=0.0, le=100.0, description="Overall model quality score (0-100).")
    summary: str = ""


class PartitionAdviceRequest(BaseModel):
    """Request for partitioning advice."""

    table_name: str = Field(..., description="Table to partition.")
    columns: list[dict[str, Any]] = Field(..., description="Column definitions.")
    row_count: int = Field(..., gt=0, description="Approximate total row count.")
    query_patterns: list[str] = Field(default_factory=list, description="Common query WHERE clauses or patterns.")
    target_platform: str | None = Field(None, description="Target platform (e.g. 'ClickHouse').")


class PartitionAdviceResponse(BaseModel):
    """Partitioning recommendations."""

    recommended_partition_keys: list[str] = Field(default_factory=list)
    sort_keys: list[str] = Field(default_factory=list)
    rationale: str = ""
    estimated_partition_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    ddl_snippet: str | None = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "/suggest",
    response_model=ModelingSuggestResponse,
    summary="Get AI modelling suggestions",
    description="Analyse the provided table schemas and return AI-generated modelling suggestions including proposed DDL changes.",
)
async def suggest_modeling(
    payload: ModelingSuggestRequest,
    ai_provider=Depends(get_ai_provider),
) -> ModelingSuggestResponse:
    """Return AI-driven modelling suggestions."""
    context = {
        "tables": [t.model_dump() for t in payload.tables],
        "business_context": payload.business_context,
        "modeling_type": payload.modeling_type.value,
        "target_platform": payload.target_platform,
    }

    result = await ai_provider.suggest_modeling(context)
    return ModelingSuggestResponse(**result)


@router.post(
    "/dimensional",
    response_model=DimensionalModelResponse,
    summary="Design a dimensional model",
    description="Use AI to design a star or snowflake dimensional model from source table schemas, including fact tables, dimension tables, and DDL.",
)
async def design_dimensional_model(
    payload: DimensionalModelRequest,
    ai_provider=Depends(get_ai_provider),
) -> DimensionalModelResponse:
    """Design a dimensional model using AI."""
    context = {
        "tables": [t.model_dump() for t in payload.tables],
        "business_process": payload.business_process,
        "grain": payload.grain,
        "preferred_schema": payload.preferred_schema,
        "target_platform": payload.target_platform,
    }

    result = await ai_provider.design_dimensional_model(context)
    return DimensionalModelResponse(**result)


@router.post(
    "/review",
    response_model=ModelReviewResponse,
    summary="Review an existing data model",
    description="Run an AI-powered review of existing table schemas and receive findings with severity levels, recommendations, and an overall quality score.",
)
async def review_model(
    payload: ModelReviewRequest,
    ai_provider=Depends(get_ai_provider),
) -> ModelReviewResponse:
    """Review a data model and return findings."""
    context = {
        "tables": [t.model_dump() for t in payload.tables],
        "warehouse_layer": payload.warehouse_layer,
        "standards": payload.standards,
    }

    result = await ai_provider.review_model(context)
    return ModelReviewResponse(**result)


@router.post(
    "/partition",
    response_model=PartitionAdviceResponse,
    summary="Get partitioning advice",
    description="Analyse query patterns and table statistics to recommend optimal partition keys, sort keys, and provide DDL snippets.",
)
async def get_partition_advice(
    payload: PartitionAdviceRequest,
    ai_provider=Depends(get_ai_provider),
) -> PartitionAdviceResponse:
    """Return partitioning recommendations for a table."""
    context = {
        "table_name": payload.table_name,
        "columns": payload.columns,
        "row_count": payload.row_count,
        "query_patterns": payload.query_patterns,
        "target_platform": payload.target_platform,
    }

    result = await ai_provider.get_partition_advice(context)
    return PartitionAdviceResponse(**result)
