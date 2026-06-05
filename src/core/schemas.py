# -*- coding: utf-8 -*-
"""
Pydantic schemas and data models for DataForge AI.

These models are used across the application — in API request/response
validation, internal service logic, and serialisation to/from the metadata
store.  Every field is documented with a ``Field(description=...)`` so that
the auto-generated OpenAPI spec is self-documenting.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ====================================================================== #
# Enums
# ====================================================================== #


class DatabaseType(str, enum.Enum):
    """Supported database engine types."""

    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    CLICKHOUSE = "clickhouse"
    DORIS = "doris"
    HIVE = "hive"
    SQLSERVER = "sqlserver"
    ORACLE = "oracle"


class WarehouseLayer(str, enum.Enum):
    """
    Standard data-warehouse layering taxonomy.

    Layers follow the Alibaba OneData methodology:
      - **ODS** (Operational Data Store): raw ingestion layer
      - **DWD** (Data Warehouse Detail): cleaned, standardised fact/dimension tables
      - **DWS** (Data Warehouse Summary): aggregated, wide tables
      - **ADS** (Application Data Store): application-facing result sets
    """

    ODS = "ODS"
    DWD = "DWD"
    DWS = "DWS"
    ADS = "ADS"


class ColumnDataType(str, enum.Enum):
    """Logical (engine-agnostic) column data types used in the modelling layer."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    JSON = "JSON"
    BINARY = "BINARY"
    TEXT = "TEXT"
    ARRAY = "ARRAY"
    MAP = "MAP"
    STRUCT = "STRUCT"


class IndexType(str, enum.Enum):
    """Types of database indexes."""

    PRIMARY = "PRIMARY"
    UNIQUE = "UNIQUE"
    NORMAL = "NORMAL"
    FULLTEXT = "FULLTEXT"
    SPATIAL = "SPATIAL"
    CLUSTERED = "CLUSTERED"


class ETLTaskStatus(str, enum.Enum):
    """Lifecycle states for an ETL task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class LineageNodeType(str, enum.Enum):
    """Types of nodes that can appear in a data-lineage graph."""

    DATABASE = "DATABASE"
    SCHEMA = "SCHEMA"
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    VIEW = "VIEW"
    ETL_TASK = "ETL_TASK"


# ====================================================================== #
# Connection schemas
# ====================================================================== #


class ConnectionConfig(BaseModel):
    """
    Configuration block for establishing a database connection.

    The same structure is used when a user registers a new connection via the
    API and when the system serialises it into the metadata store (passwords
    are encrypted at rest).
    """

    model_config = ConfigDict(use_enum_values=True)

    connection_id: Optional[str] = Field(
        default=None,
        description="Unique identifier for this connection. Auto-generated on creation.",
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Human-friendly display name for the connection (e.g. 'Prod MySQL Cluster').",
    )

    db_type: DatabaseType = Field(
        ...,
        description="Target database engine type.",
    )

    host: str = Field(
        ...,
        min_length=1,
        description="Hostname or IP address of the database server.",
    )

    port: int = Field(
        ...,
        ge=1,
        le=65535,
        description="TCP port the database server listens on.",
    )

    username: str = Field(
        ...,
        min_length=1,
        description="Database user for authentication.",
    )

    password: str = Field(
        ...,
        min_length=0,
        description="Password for the database user. Stored encrypted at rest.",
    )

    database: Optional[str] = Field(
        default=None,
        description="Default database / catalog to use. Omit to connect without selecting one.",
    )

    schema_name: Optional[str] = Field(
        default=None,
        description="Default schema within the database (PostgreSQL / SQL Server / Oracle).",
    )

    extra_params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Engine-specific connection parameters (e.g. SSL options, "
            "connection timeout, charset). Passed through to the driver."
        ),
    )

    use_ssl: bool = Field(
        default=False,
        description="Whether to establish an SSL/TLS encrypted connection.",
    )

    connection_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Timeout in seconds when establishing the connection.",
    )

    tags: List[str] = Field(
        default_factory=list,
        description="User-defined tags for filtering and grouping connections.",
    )

    created_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the connection was created.",
    )

    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the last update to this connection config.",
    )


class ConnectionTestResult(BaseModel):
    """Result of testing a database connection."""

    success: bool = Field(
        ...,
        description="Whether the connection test passed.",
    )

    message: str = Field(
        default="",
        description="Human-readable status message.",
    )

    latency_ms: Optional[float] = Field(
        default=None,
        description="Round-trip latency of the test query in milliseconds.",
    )

    server_version: Optional[str] = Field(
        default=None,
        description="Version string reported by the database server.",
    )


# ====================================================================== #
# Table / column / index schemas
# ====================================================================== #


class ColumnInfo(BaseModel):
    """Metadata for a single column in a database table."""

    name: str = Field(
        ...,
        description="Column name as defined in the database.",
    )

    data_type: str = Field(
        ...,
        description="Native data type string from the database (e.g. 'VARCHAR(255)', 'INT').",
    )

    logical_type: Optional[ColumnDataType] = Field(
        default=None,
        description="Mapped logical data type used by the modelling layer.",
    )

    nullable: bool = Field(
        default=True,
        description="Whether the column allows NULL values.",
    )

    is_primary_key: bool = Field(
        default=False,
        description="Whether this column is (part of) the primary key.",
    )

    default_value: Optional[str] = Field(
        default=None,
        description="Default value expression, if any.",
    )

    comment: Optional[str] = Field(
        default=None,
        description="Column comment / description stored in the database.",
    )

    ordinal_position: int = Field(
        default=0,
        description="Zero-based position of the column within the table.",
    )

    character_max_length: Optional[int] = Field(
        default=None,
        description="Maximum character length for string-type columns.",
    )

    numeric_precision: Optional[int] = Field(
        default=None,
        description="Total number of significant digits for numeric columns.",
    )

    numeric_scale: Optional[int] = Field(
        default=None,
        description="Number of digits after the decimal point for numeric columns.",
    )

    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="Engine-specific column attributes (e.g. auto_increment, unsigned).",
    )


class IndexInfo(BaseModel):
    """Metadata for a database index."""

    name: str = Field(
        ...,
        description="Index name.",
    )

    index_type: IndexType = Field(
        default=IndexType.NORMAL,
        description="Type of the index (PRIMARY, UNIQUE, NORMAL, etc.).",
    )

    columns: List[str] = Field(
        default_factory=list,
        description="Ordered list of column names that make up the index.",
    )

    is_unique: bool = Field(
        default=False,
        description="Whether the index enforces uniqueness.",
    )

    comment: Optional[str] = Field(
        default=None,
        description="Optional comment attached to the index.",
    )


class TableSchema(BaseModel):
    """Full metadata representation of a database table."""

    connection_id: Optional[str] = Field(
        default=None,
        description="ID of the connection this table belongs to.",
    )

    database_name: str = Field(
        ...,
        description="Name of the database / catalog containing this table.",
    )

    schema_name: Optional[str] = Field(
        default=None,
        description="Schema name (PostgreSQL / SQL Server / Oracle).",
    )

    table_name: str = Field(
        ...,
        description="Table name.",
    )

    table_type: str = Field(
        default="TABLE",
        description="Type of the table object: TABLE, VIEW, MATERIALIZED VIEW, etc.",
    )

    comment: Optional[str] = Field(
        default=None,
        description="Table-level comment / description.",
    )

    columns: List[ColumnInfo] = Field(
        default_factory=list,
        description="Ordered list of columns in the table.",
    )

    indexes: List[IndexInfo] = Field(
        default_factory=list,
        description="Indexes defined on the table.",
    )

    row_count_estimate: Optional[int] = Field(
        default=None,
        description="Approximate row count (from engine statistics, not a live COUNT).",
    )

    size_bytes: Optional[int] = Field(
        default=None,
        description="Total size of the table on disk in bytes.",
    )

    warehouse_layer: Optional[WarehouseLayer] = Field(
        default=None,
        description="Data-warehouse layer this table belongs to (ODS / DWD / DWS / ADS).",
    )

    created_at: Optional[datetime] = Field(
        default=None,
        description="When the table was first discovered / created.",
    )

    updated_at: Optional[datetime] = Field(
        default=None,
        description="When the metadata was last refreshed.",
    )


class TableStats(BaseModel):
    """Runtime statistics for a table (live queries, not cached metadata)."""

    table_name: str = Field(..., description="Table name.")
    row_count: int = Field(default=0, description="Exact or estimated row count.")
    size_bytes: Optional[int] = Field(default=None, description="Table size on disk in bytes.")
    avg_row_size_bytes: Optional[float] = Field(default=None, description="Average row size.")
    last_analyzed: Optional[datetime] = Field(default=None, description="When statistics were last computed.")


# ====================================================================== #
# SQL generation schemas
# ====================================================================== #


class SQLGenerationRequest(BaseModel):
    """Request payload for AI-powered SQL generation."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="Natural-language description of the desired SQL operation.",
    )

    db_type: DatabaseType = Field(
        ...,
        description="Target database engine (affects SQL dialect).",
    )

    context_tables: List[TableSchema] = Field(
        default_factory=list,
        description=(
            "Relevant table schemas that the model should consider. "
            "Providing schemas dramatically improves generation accuracy."
        ),
    )

    target_table: Optional[str] = Field(
        default=None,
        description="If generating an INSERT / MERGE, the target table name.",
    )

    warehouse_layer: Optional[WarehouseLayer] = Field(
        default=None,
        description="Warehouse layer context — helps the model follow naming conventions.",
    )

    include_comments: bool = Field(
        default=True,
        description="Ask the model to include SQL comments explaining the logic.",
    )

    max_results: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of SQL variants to generate.",
    )

    model_override: Optional[str] = Field(
        default=None,
        description="Override the default LLM model for this request.",
    )


class SQLGenerationResult(BaseModel):
    """A single generated SQL candidate."""

    sql: str = Field(
        ...,
        description="The generated SQL statement.",
    )

    explanation: str = Field(
        default="",
        description="Natural-language explanation of what the SQL does.",
    )

    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Model's self-assessed confidence in the correctness of the SQL.",
    )

    warnings: List[str] = Field(
        default_factory=list,
        description="Potential issues or assumptions the model identified.",
    )

    referenced_tables: List[str] = Field(
        default_factory=list,
        description="Table names referenced in the generated SQL.",
    )

    referenced_columns: List[str] = Field(
        default_factory=list,
        description="Column names referenced in the generated SQL.",
    )


class SQLGenerationResponse(BaseModel):
    """Response payload for an SQL generation request."""

    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this generation request.",
    )

    results: List[SQLGenerationResult] = Field(
        default_factory=list,
        description="List of generated SQL candidates, ordered by confidence.",
    )

    model_used: str = Field(
        default="",
        description="LLM model identifier that produced the results.",
    )

    tokens_used: int = Field(
        default=0,
        description="Total tokens consumed by the generation call.",
    )

    latency_ms: float = Field(
        default=0.0,
        description="Wall-clock time for the generation in milliseconds.",
    )


# ====================================================================== #
# Data modelling schemas
# ====================================================================== #


class DataModelingRequest(BaseModel):
    """Request payload for AI-assisted data-warehouse modelling."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="Natural-language description of the business requirement.",
    )

    source_tables: List[TableSchema] = Field(
        default_factory=list,
        description="Source table schemas available for the model to reference.",
    )

    target_layer: WarehouseLayer = Field(
        ...,
        description="Target warehouse layer for the modelled output.",
    )

    naming_convention: Optional[str] = Field(
        default=None,
        description=(
            "Naming convention template (e.g. '{layer}_{domain}_{table_desc}'). "
            "If omitted, the system default is used."
        ),
    )

    db_type: DatabaseType = Field(
        default=DatabaseType.CLICKHOUSE,
        description="Target database engine for DDL generation.",
    )


class DimensionModel(BaseModel):
    """A dimension table produced by the modelling step."""

    table_name: str = Field(..., description="Generated dimension table name.")
    columns: List[ColumnInfo] = Field(default_factory=list, description="Column definitions.")
    description: str = Field(default="", description="Business description of the dimension.")
    grain: Optional[str] = Field(default=None, description="Grain / granularity of the dimension.")


class FactModel(BaseModel):
    """A fact table produced by the modelling step."""

    table_name: str = Field(..., description="Generated fact table name.")
    columns: List[ColumnInfo] = Field(default_factory=list, description="Column definitions.")
    description: str = Field(default="", description="Business description of the fact table.")
    grain: Optional[str] = Field(default=None, description="Grain / granularity of the fact table.")
    measures: List[str] = Field(default_factory=list, description="Column names that are measures / metrics.")
    foreign_keys: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of FK column name -> referenced dimension table name.",
    )


class DataModelingResponse(BaseModel):
    """Response payload for a data-modelling request."""

    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this modelling request.",
    )

    dimensions: List[DimensionModel] = Field(
        default_factory=list,
        description="Proposed dimension tables.",
    )

    facts: List[FactModel] = Field(
        default_factory=list,
        description="Proposed fact tables.",
    )

    ddl_statements: List[str] = Field(
        default_factory=list,
        description="Generated DDL CREATE TABLE statements ready for execution.",
    )

    explanation: str = Field(
        default="",
        description="Narrative explanation of the modelling decisions.",
    )

    model_used: str = Field(default="", description="LLM model that produced the design.")
    tokens_used: int = Field(default=0, description="Total tokens consumed.")


# ====================================================================== #
# Lineage schemas
# ====================================================================== #


class LineageNode(BaseModel):
    """
    A single node in a data-lineage graph.

    Nodes represent data assets: databases, schemas, tables, columns,
    views, or ETL tasks.
    """

    node_id: str = Field(
        ...,
        description="Unique identifier for the node (e.g. 'db.schema.table').",
    )

    node_type: LineageNodeType = Field(
        ...,
        description="Type of the data asset this node represents.",
    )

    label: str = Field(
        ...,
        description="Display label for the node.",
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata (warehouse layer, row count, owner, etc.).",
    )


class LineageEdge(BaseModel):
    """
    A directed edge in a data-lineage graph representing data flow.

    The edge goes from ``source_id`` to ``target_id``, meaning data flows
    from the source into the target.
    """

    edge_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for the edge.",
    )

    source_id: str = Field(
        ...,
        description="ID of the source node (upstream).",
    )

    target_id: str = Field(
        ...,
        description="ID of the target node (downstream).",
    )

    edge_type: str = Field(
        default="DATA_FLOW",
        description="Type of relationship (DATA_FLOW, DERIVED_FROM, FK_REFERENCE, etc.).",
    )

    transformation: Optional[str] = Field(
        default=None,
        description="Brief description or SQL snippet that transforms source into target.",
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional edge metadata.",
    )


class LineageGraph(BaseModel):
    """
    Complete data-lineage graph for a given scope.

    Can be rendered as a DAG (directed acyclic graph) in the front-end.
    """

    graph_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this lineage graph.",
    )

    scope: str = Field(
        default="full",
        description="Scope of the graph (e.g. 'full', 'table:orders', 'layer:DWD').",
    )

    nodes: List[LineageNode] = Field(
        default_factory=list,
        description="All nodes in the graph.",
    )

    edges: List[LineageEdge] = Field(
        default_factory=list,
        description="All directed edges in the graph.",
    )

    generated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the lineage graph was generated.",
    )


# ====================================================================== #
# ETL / pipeline schemas
# ====================================================================== #


class ETLTaskConfig(BaseModel):
    """
    Configuration for a single ETL task (one step in a pipeline).

    A task typically represents one SQL transformation: extracting from
    upstream tables, applying transformations, and loading into a target.
    """

    task_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique task identifier.",
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Human-readable task name.",
    )

    description: str = Field(
        default="",
        description="Detailed description of what this task does.",
    )

    source_connection_id: str = Field(
        ...,
        description="Connection ID for the source database.",
    )

    target_connection_id: str = Field(
        ...,
        description="Connection ID for the target database.",
    )

    source_query: str = Field(
        ...,
        min_length=1,
        description="SQL query that extracts / transforms data from the source.",
    )

    target_table: str = Field(
        ...,
        description="Fully qualified target table name.",
    )

    write_mode: str = Field(
        default="append",
        description="Write mode: 'append', 'overwrite', 'upsert', or 'merge'.",
    )

    warehouse_layer: Optional[WarehouseLayer] = Field(
        default=None,
        description="Warehouse layer of the target table.",
    )

    depends_on: List[str] = Field(
        default_factory=list,
        description="Task IDs that must complete before this task can start.",
    )

    retry_count: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Number of automatic retries on failure.",
    )

    retry_delay_seconds: int = Field(
        default=60,
        ge=0,
        description="Delay between retries in seconds.",
    )

    timeout_seconds: int = Field(
        default=3600,
        ge=1,
        description="Maximum execution time before the task is killed.",
    )

    enabled: bool = Field(
        default=True,
        description="Whether this task is active in the pipeline.",
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary metadata (owner, SLA, tags, etc.).",
    )


class ETLPipeline(BaseModel):
    """
    An ordered collection of ETL tasks forming a data pipeline.

    Pipelines define the DAG of transformations that move data through
    the warehouse layers (ODS -> DWD -> DWS -> ADS).
    """

    pipeline_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique pipeline identifier.",
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Human-readable pipeline name.",
    )

    description: str = Field(
        default="",
        description="High-level description of the pipeline's purpose.",
    )

    schedule_cron: Optional[str] = Field(
        default=None,
        description="Cron expression for scheduling (e.g. '0 2 * * *' for daily at 2 AM).",
    )

    tasks: List[ETLTaskConfig] = Field(
        default_factory=list,
        description="Ordered list of tasks in the pipeline.",
    )

    max_concurrency: int = Field(
        default=4,
        ge=1,
        description="Maximum number of tasks that can run in parallel.",
    )

    failure_strategy: str = Field(
        default="stop",
        description="What to do when a task fails: 'stop', 'continue', or 'retry_all'.",
    )

    notification_channels: List[str] = Field(
        default_factory=list,
        description="Channels to notify on pipeline completion or failure.",
    )

    tags: List[str] = Field(
        default_factory=list,
        description="User-defined tags for categorising pipelines.",
    )

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the pipeline was created.",
    )

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the pipeline was last modified.",
    )


class ETLTaskRunResult(BaseModel):
    """Result of a single ETL task execution."""

    task_id: str = Field(..., description="Task that was executed.")
    status: ETLTaskStatus = Field(..., description="Final status of the execution.")
    rows_processed: int = Field(default=0, description="Number of rows read from source.")
    rows_written: int = Field(default=0, description="Number of rows written to target.")
    started_at: Optional[datetime] = Field(default=None, description="When execution started.")
    finished_at: Optional[datetime] = Field(default=None, description="When execution finished.")
    duration_seconds: Optional[float] = Field(default=None, description="Total wall-clock duration.")
    error_message: Optional[str] = Field(default=None, description="Error details if the task failed.")


# ====================================================================== #
# Generic API response wrappers
# ====================================================================== #


class APIResponse(BaseModel):
    """Standard API response envelope."""

    success: bool = Field(default=True, description="Whether the request was successful.")
    message: str = Field(default="ok", description="Human-readable status message.")
    data: Optional[Any] = Field(default=None, description="Response payload.")


class PaginatedResponse(BaseModel):
    """Paginated list response."""

    items: List[Any] = Field(default_factory=list, description="Page of items.")
    total: int = Field(default=0, description="Total number of items across all pages.")
    page: int = Field(default=1, ge=1, description="Current page number (1-indexed).")
    page_size: int = Field(default=20, ge=1, le=200, description="Number of items per page.")
    total_pages: int = Field(default=0, description="Total number of pages.")
