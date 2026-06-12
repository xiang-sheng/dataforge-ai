"""
DataForge AI - Connection management API routes.

Provides CRUD endpoints for database connections as well as helpers for
introspecting remote schemas (databases, tables, columns).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, SecretStr

from src.api.deps import get_connection_manager, get_db_adapter

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DatabaseType(StrEnum):
    """Supported database engines."""

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    CLICKHOUSE = "clickhouse"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    REDSHIFT = "redshift"
    DORIS = "doris"
    STARROCKS = "starrocks"


class ConnectionStatus(StrEnum):
    """Connection health status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class ConnectionCreate(BaseModel):
    """Payload for creating a new database connection."""

    name: str = Field(..., min_length=1, max_length=128, description="Human-readable connection name.")
    db_type: DatabaseType = Field(..., description="Target database engine.")
    host: str = Field(..., description="Database host address.")
    port: int = Field(..., gt=0, lt=65536, description="Database port.")
    username: str = Field(..., description="Authentication username.")
    password: SecretStr = Field(..., description="Authentication password.")
    default_database: str | None = Field(None, description="Default database/schema to use.")
    extra_params: dict[str, Any] = Field(default_factory=dict, description="Additional driver-specific parameters.")
    tags: list[str] = Field(default_factory=list, description="User-defined tags for grouping.")


class ConnectionUpdate(BaseModel):
    """Payload for updating an existing connection (all fields optional)."""

    name: str | None = Field(None, min_length=1, max_length=128)
    host: str | None = None
    port: int | None = Field(None, gt=0, lt=65536)
    username: str | None = None
    password: SecretStr | None = None
    default_database: str | None = None
    extra_params: dict[str, Any] | None = None
    tags: list[str] | None = None


class ConnectionResponse(BaseModel):
    """Public representation of a connection (passwords redacted)."""

    id: str
    name: str
    db_type: DatabaseType
    host: str
    port: int
    username: str
    default_database: str | None = None
    status: ConnectionStatus = ConnectionStatus.INACTIVE
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConnectionTestResult(BaseModel):
    """Result of a connection test."""

    success: bool
    latency_ms: float | None = None
    server_version: str | None = None
    message: str = ""


class DatabaseInfo(BaseModel):
    """Basic information about a database/schema."""

    name: str
    size_mb: float | None = None
    table_count: int | None = None


class TableInfo(BaseModel):
    """Basic information about a table."""

    schema_name: str
    table_name: str
    table_type: str = "BASE TABLE"
    row_count: int | None = None
    size_mb: float | None = None
    comment: str | None = None


class ColumnInfo(BaseModel):
    """Detailed column metadata."""

    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    default_value: str | None = None
    comment: str | None = None
    ordinal_position: int = 0


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new database connection",
    description="Register a new database connection with the platform. The password is stored securely and never returned in API responses.",
)
async def create_connection(
    payload: ConnectionCreate,
    manager=Depends(get_connection_manager),
) -> ConnectionResponse:
    """Create and persist a new database connection."""
    connection_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    connection_data = {
        "id": connection_id,
        "name": payload.name,
        "db_type": payload.db_type,
        "host": payload.host,
        "port": payload.port,
        "username": payload.username,
        "password": payload.password.get_secret_value(),
        "default_database": payload.default_database,
        "status": ConnectionStatus.INACTIVE,
        "extra_params": payload.extra_params,
        "tags": payload.tags,
        "created_at": now,
        "updated_at": now,
    }

    await manager.save(connection_id, connection_data)

    return ConnectionResponse(**{
        k: v for k, v in connection_data.items() if k != "password"
    })


@router.get(
    "",
    response_model=list[ConnectionResponse],
    summary="List all connections",
    description="Return every registered connection. Passwords are never included.",
)
async def list_connections(
    db_type: DatabaseType | None = Query(None, description="Filter by database type."),
    tag: str | None = Query(None, description="Filter by tag."),
    manager=Depends(get_connection_manager),
) -> list[ConnectionResponse]:
    """Return all connections, optionally filtered."""
    connections = await manager.list_all(db_type=db_type, tag=tag)
    return [
        ConnectionResponse(**{k: v for k, v in c.items() if k != "password"})
        for c in connections
    ]


@router.get(
    "/{connection_id}",
    response_model=ConnectionResponse,
    summary="Get connection details",
    description="Retrieve full details of a single connection by its unique ID.",
)
async def get_connection(
    connection_id: str,
    manager=Depends(get_connection_manager),
) -> ConnectionResponse:
    """Return a single connection or 404."""
    conn = await manager.get(connection_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )
    return ConnectionResponse(**{k: v for k, v in conn.items() if k != "password"})


@router.put(
    "/{connection_id}",
    response_model=ConnectionResponse,
    summary="Update a connection",
    description="Partially or fully update a connection's configuration. Only supplied fields are modified.",
)
async def update_connection(
    connection_id: str,
    payload: ConnectionUpdate,
    manager=Depends(get_connection_manager),
) -> ConnectionResponse:
    """Update an existing connection."""
    conn = await manager.get(connection_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if "password" in update_data and update_data["password"] is not None:
        update_data["password"] = update_data["password"].get_secret_value()

    conn.update(update_data)
    conn["updated_at"] = datetime.now(UTC)
    await manager.save(connection_id, conn)

    return ConnectionResponse(**{k: v for k, v in conn.items() if k != "password"})


@router.delete(
    "/{connection_id}",
    response_model=MessageResponse,
    summary="Remove a connection",
    description="Permanently delete a connection from the platform.",
)
async def delete_connection(
    connection_id: str,
    manager=Depends(get_connection_manager),
) -> MessageResponse:
    """Delete a connection."""
    conn = await manager.get(connection_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    await manager.delete(connection_id)
    return MessageResponse(message=f"Connection '{connection_id}' deleted.")


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionTestResult,
    summary="Test a connection",
    description="Attempt to connect to the target database and report success/failure along with latency and server version.",
)
async def test_connection(
    connection_id: str,
    manager=Depends(get_connection_manager),
) -> ConnectionTestResult:
    """Test connectivity for a given connection."""
    conn = await manager.get(connection_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    try:
        adapter = await manager.get_adapter(connection_id)
        result = await adapter.test()
        return ConnectionTestResult(**result)
    except Exception as exc:
        return ConnectionTestResult(
            success=False,
            message=f"Connection test failed: {exc}",
        )


@router.get(
    "/{connection_id}/databases",
    response_model=list[DatabaseInfo],
    summary="List databases",
    description="Introspect the remote server and return all accessible databases/schemas.",
)
async def list_databases(
    connection_id: str,
    adapter=Depends(get_db_adapter),
) -> list[DatabaseInfo]:
    """Return databases visible through this connection."""
    databases = await adapter.list_databases()
    return [DatabaseInfo(**db) for db in databases]


@router.get(
    "/{connection_id}/tables",
    response_model=list[TableInfo],
    summary="List tables",
    description="Return all tables in the specified database/schema on the remote server.",
)
async def list_tables(
    connection_id: str,
    database: str = Query(..., description="Database or schema name to introspect."),
    adapter=Depends(get_db_adapter),
) -> list[TableInfo]:
    """Return tables for a given database/schema."""
    tables = await adapter.list_tables(database)
    return [TableInfo(**t) for t in tables]


@router.get(
    "/{connection_id}/tables/{table_name}/columns",
    response_model=list[ColumnInfo],
    summary="Get table columns",
    description="Return detailed column metadata for the specified table.",
)
async def get_table_columns(
    connection_id: str,
    table_name: str,
    database: str = Query(..., description="Database or schema the table belongs to."),
    adapter=Depends(get_db_adapter),
) -> list[ColumnInfo]:
    """Return column-level metadata for a table."""
    columns = await adapter.get_columns(database, table_name)
    return [ColumnInfo(**c) for c in columns]
