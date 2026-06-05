"""
DataForge AI - ETL pipeline API routes.

Endpoints for creating, listing, and managing ETL pipelines, as well
as generating Airflow DAGs and validating pipeline configurations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.deps import get_ai_provider, get_connection_manager

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PipelineStatus(str, Enum):
    """ETL pipeline lifecycle status."""

    DRAFT = "draft"
    VALIDATED = "validated"
    DEPLOYED = "deployed"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"


class TaskType(str, Enum):
    """Types of EL tasks within a pipeline."""

    EXTRACT = "extract"
    TRANSFORM = "transform"
    LOAD = "load"
    SQL_EXECUTE = "sql_execute"
    DATA_QUALITY_CHECK = "data_quality_check"
    CUSTOM_PYTHON = "custom_python"


class ScheduleType(str, Enum):
    """Pipeline schedule types."""

    MANUAL = "manual"
    CRON = "cron"
    EVENT_DRIVEN = "event_driven"
    INTERVAL = "interval"


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class TaskDefinition(BaseModel):
    """Definition of a single task within an ETL pipeline."""

    task_id: str = Field(..., description="Unique task identifier within the pipeline.")
    task_type: TaskType = Field(..., description="Type of task.")
    name: str = Field(..., description="Human-readable task name.")
    description: Optional[str] = Field(None, description="Task description.")
    config: dict[str, Any] = Field(default_factory=dict, description="Task-specific configuration.")
    sql: Optional[str] = Field(None, description="SQL to execute (for sql_execute tasks).")
    connection_id: Optional[str] = Field(None, description="Database connection to use.")
    database: Optional[str] = Field(None, description="Target database/schema.")
    dependencies: list[str] = Field(default_factory=list, description="IDs of tasks that must complete before this one.")
    retries: int = Field(0, ge=0, le=10, description="Number of retry attempts on failure.")
    timeout_seconds: int = Field(3600, ge=60, description="Task timeout in seconds.")


class PipelineCreateRequest(BaseModel):
    """Request body for creating an ETL pipeline."""

    name: str = Field(..., min_length=1, max_length=256, description="Pipeline name.")
    description: Optional[str] = Field(None, description="Pipeline description.")
    source_connection_id: str = Field(..., description="Source database connection ID.")
    target_connection_id: str = Field(..., description="Target database connection ID.")
    schedule_type: ScheduleType = Field(ScheduleType.MANUAL, description="How the pipeline is triggered.")
    schedule_expression: Optional[str] = Field(None, description="Cron expression or interval (e.g. '0 2 * * *' or '3600s').")
    tasks: list[TaskDefinition] = Field(..., min_length=1, description="Ordered list of pipeline tasks.")
    tags: list[str] = Field(default_factory=list, description="User-defined tags.")
    config: dict[str, Any] = Field(default_factory=dict, description="Pipeline-level configuration.")


class PipelineUpdateRequest(BaseModel):
    """Request body for updating a pipeline (all fields optional)."""

    name: Optional[str] = Field(None, min_length=1, max_length=256)
    description: Optional[str] = None
    schedule_type: Optional[ScheduleType] = None
    schedule_expression: Optional[str] = None
    tasks: Optional[list[TaskDefinition]] = None
    tags: Optional[list[str]] = None
    config: Optional[dict[str, Any]] = None


class PipelineResponse(BaseModel):
    """Public representation of an ETL pipeline."""

    id: str
    name: str
    description: Optional[str] = None
    source_connection_id: str
    target_connection_id: str
    status: PipelineStatus = PipelineStatus.DRAFT
    schedule_type: ScheduleType
    schedule_expression: Optional[str] = None
    task_count: int
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PipelineDetailResponse(PipelineResponse):
    """Detailed view including full task definitions."""

    tasks: list[TaskDefinition] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class DAGGenerateResponse(BaseModel):
    """Generated Airflow DAG code."""

    pipeline_id: str
    dag_code: str = Field(..., description="Python source code for the Airflow DAG.")
    dag_id: str = Field(..., description="Airflow DAG identifier.")
    instructions: list[str] = Field(default_factory=list, description="Deployment instructions.")
    warnings: list[str] = Field(default_factory=list, description="Potential issues with the generated DAG.")


class ValidationIssue(BaseModel):
    """A single validation issue."""

    severity: str = Field(..., description="'error', 'warning', or 'info'.")
    task_id: Optional[str] = Field(None, description="Task ID if the issue is task-specific.")
    message: str


class PipelineValidationResponse(BaseModel):
    """Result of pipeline validation."""

    pipeline_id: str
    is_valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: str = ""


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "/pipelines",
    response_model=PipelineResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an ETL pipeline",
    description="Register a new ETL pipeline with its task definitions, schedule, and connection mappings.",
)
async def create_pipeline(
    payload: PipelineCreateRequest,
    manager=Depends(get_connection_manager),
) -> PipelineResponse:
    """Create a new ETL pipeline."""
    # Validate connections exist
    source = await manager.get(payload.source_connection_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source connection '{payload.source_connection_id}' not found.",
        )
    target = await manager.get(payload.target_connection_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target connection '{payload.target_connection_id}' not found.",
        )

    # Validate task dependency graph (no cycles)
    _validate_task_dependencies(payload.tasks)

    pipeline_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    pipeline_data = {
        "id": pipeline_id,
        "name": payload.name,
        "description": payload.description,
        "source_connection_id": payload.source_connection_id,
        "target_connection_id": payload.target_connection_id,
        "status": PipelineStatus.DRAFT.value,
        "schedule_type": payload.schedule_type.value,
        "schedule_expression": payload.schedule_expression,
        "tasks": [t.model_dump() for t in payload.tasks],
        "tags": payload.tags,
        "config": payload.config,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    await manager.save_pipeline(pipeline_id, pipeline_data)

    return PipelineResponse(
        id=pipeline_id,
        name=payload.name,
        description=payload.description,
        source_connection_id=payload.source_connection_id,
        target_connection_id=payload.target_connection_id,
        status=PipelineStatus.DRAFT,
        schedule_type=payload.schedule_type,
        schedule_expression=payload.schedule_expression,
        task_count=len(payload.tasks),
        tags=payload.tags,
        created_at=now,
        updated_at=now,
    )


@router.get(
    "/pipelines",
    response_model=list[PipelineResponse],
    summary="List ETL pipelines",
    description="Return all registered ETL pipelines with optional filtering by status and tags.",
)
async def list_pipelines(
    status_filter: Optional[PipelineStatus] = Query(None, alias="status", description="Filter by pipeline status."),
    tag: Optional[str] = Query(None, description="Filter by tag."),
    manager=Depends(get_connection_manager),
) -> list[PipelineResponse]:
    """List all ETL pipelines."""
    pipelines = await manager.list_pipelines(status=status_filter, tag=tag)

    results: list[PipelineResponse] = []
    for p in pipelines:
        results.append(
            PipelineResponse(
                id=p["id"],
                name=p["name"],
                description=p.get("description"),
                source_connection_id=p["source_connection_id"],
                target_connection_id=p["target_connection_id"],
                status=PipelineStatus(p["status"]),
                schedule_type=ScheduleType(p["schedule_type"]),
                schedule_expression=p.get("schedule_expression"),
                task_count=len(p.get("tasks", [])),
                tags=p.get("tags", []),
                created_at=datetime.fromisoformat(p["created_at"]),
                updated_at=datetime.fromisoformat(p["updated_at"]),
            )
        )
    return results


@router.get(
    "/pipelines/{pipeline_id}",
    response_model=PipelineDetailResponse,
    summary="Get pipeline details",
    description="Retrieve full pipeline configuration including all task definitions.",
)
async def get_pipeline(
    pipeline_id: str,
    manager=Depends(get_connection_manager),
) -> PipelineDetailResponse:
    """Return detailed pipeline information."""
    pipeline = await manager.get_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline '{pipeline_id}' not found.",
        )

    return PipelineDetailResponse(
        id=pipeline["id"],
        name=pipeline["name"],
        description=pipeline.get("description"),
        source_connection_id=pipeline["source_connection_id"],
        target_connection_id=pipeline["target_connection_id"],
        status=PipelineStatus(pipeline["status"]),
        schedule_type=ScheduleType(pipeline["schedule_type"]),
        schedule_expression=pipeline.get("schedule_expression"),
        task_count=len(pipeline.get("tasks", [])),
        tags=pipeline.get("tags", []),
        created_at=datetime.fromisoformat(pipeline["created_at"]),
        updated_at=datetime.fromisoformat(pipeline["updated_at"]),
        tasks=[TaskDefinition(**t) for t in pipeline.get("tasks", [])],
        config=pipeline.get("config", {}),
    )


@router.post(
    "/pipelines/{pipeline_id}/generate-dag",
    response_model=DAGGenerateResponse,
    summary="Generate Airflow DAG",
    description="Generate an Apache Airflow DAG Python file from the pipeline task definitions and dependency graph.",
)
async def generate_dag(
    pipeline_id: str,
    ai_provider=Depends(get_ai_provider),
    manager=Depends(get_connection_manager),
) -> DAGGenerateResponse:
    """Generate an Airflow DAG for the pipeline."""
    pipeline = await manager.get_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline '{pipeline_id}' not found.",
        )

    context = {
        "pipeline_id": pipeline_id,
        "pipeline_name": pipeline["name"],
        "schedule_expression": pipeline.get("schedule_expression"),
        "tasks": pipeline.get("tasks", []),
        "config": pipeline.get("config", {}),
    }

    result = await ai_provider.generate_airflow_dag(context)

    return DAGGenerateResponse(
        pipeline_id=pipeline_id,
        dag_code=result["dag_code"],
        dag_id=result["dag_id"],
        instructions=result.get("instructions", []),
        warnings=result.get("warnings", []),
    )


@router.post(
    "/pipelines/{pipeline_id}/validate",
    response_model=PipelineValidationResponse,
    summary="Validate a pipeline",
    description="Run comprehensive validation checks on the pipeline configuration, task definitions, connections, and dependency graph.",
)
async def validate_pipeline(
    pipeline_id: str,
    manager=Depends(get_connection_manager),
) -> PipelineValidationResponse:
    """Validate a pipeline configuration."""
    pipeline = await manager.get_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline '{pipeline_id}' not found.",
        )

    issues: list[ValidationIssue] = []

    # Check connections
    source = await manager.get(pipeline["source_connection_id"])
    if source is None:
        issues.append(ValidationIssue(
            severity="error",
            message=f"Source connection '{pipeline['source_connection_id']}' does not exist.",
        ))

    target = await manager.get(pipeline["target_connection_id"])
    if target is None:
        issues.append(ValidationIssue(
            severity="error",
            message=f"Target connection '{pipeline['target_connection_id']}' does not exist.",
        ))

    # Check tasks
    tasks = pipeline.get("tasks", [])
    if not tasks:
        issues.append(ValidationIssue(
            severity="error",
            message="Pipeline has no tasks defined.",
        ))

    task_ids = {t["task_id"] for t in tasks}
    for task in tasks:
        # Check for dangling dependencies
        for dep in task.get("dependencies", []):
            if dep not in task_ids:
                issues.append(ValidationIssue(
                    severity="error",
                    task_id=task["task_id"],
                    message=f"Dependency '{dep}' does not exist.",
                ))

        # Check SQL tasks have SQL
        if task["task_type"] == TaskType.SQL_EXECUTE.value and not task.get("sql"):
            issues.append(ValidationIssue(
                severity="error",
                task_id=task["task_id"],
                message="SQL task has no SQL defined.",
            ))

    # Check schedule
    if pipeline["schedule_type"] == ScheduleType.CRON.value and not pipeline.get("schedule_expression"):
        issues.append(ValidationIssue(
            severity="warning",
            message="Cron schedule type is set but no schedule expression provided.",
        ))

    # Check for cyclic dependencies
    try:
        task_defs = [TaskDefinition(**t) for t in tasks]
        _validate_task_dependencies(task_defs)
    except HTTPException as exc:
        issues.append(ValidationIssue(
            severity="error",
            message=str(exc.detail),
        ))

    is_valid = not any(i.severity == "error" for i in issues)

    return PipelineValidationResponse(
        pipeline_id=pipeline_id,
        is_valid=is_valid,
        issues=issues,
        summary=f"Validation {'passed' if is_valid else 'failed'}: {len(issues)} issue(s) found.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_task_dependencies(tasks: list[TaskDefinition]) -> None:
    """Validate the task dependency graph has no cycles using topological sort.

    Args:
        tasks: List of task definitions.

    Raises:
        HTTPException: If a circular dependency is detected.
    """
    graph: dict[str, list[str]] = {t.task_id: list(t.dependencies) for t in tasks}
    visited: set[str] = set()
    in_stack: set[str] = set()

    def _dfs(node: str) -> bool:
        if node in in_stack:
            return True  # cycle detected
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in graph.get(node, []):
            if _dfs(dep):
                return True
        in_stack.discard(node)
        return False

    for task_id in graph:
        if _dfs(task_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Circular dependency detected in task graph.",
            )
