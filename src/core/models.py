"""
SQLAlchemy ORM models for the DataForge AI internal metadata store.

These models back the platform's persistent state: saved connections,
ETL pipeline definitions, execution history, the data catalog, and
lineage graph data.

All models use the SQLAlchemy 2.0 declarative style with ``Mapped``
type annotations and are mapped to the ``dataforge_`` table namespace.

Usage::

    from src.core.models import Base, ConnectionRecord
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# ------------------------------------------------------------------ #
# Base
# ------------------------------------------------------------------ #

class Base(DeclarativeBase):
    """Shared declarative base for all DataForge ORM models."""


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _new_uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.utcnow()


# ------------------------------------------------------------------ #
# Connection records
# ------------------------------------------------------------------ #

class ConnectionRecord(Base):
    """Persisted database connection configuration.

    Mirrors :class:`src.core.schemas.ConnectionConfig` but stores the
    password separately (encrypted at rest in a future iteration).
    """

    __tablename__ = "dataforge_connections"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_new_uuid,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    db_type: Mapped[str] = mapped_column(String(50), nullable=False)
    host: Mapped[str] = mapped_column(String(500), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(200), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    database: Mapped[str | None] = mapped_column(String(200))
    schema_name: Mapped[str | None] = mapped_column(String(200))
    extra_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connection_timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_connections_db_type", "db_type"),
    )

    def __repr__(self) -> str:
        return f"<ConnectionRecord {self.name!r} ({self.db_type})>"


# ------------------------------------------------------------------ #
# ETL pipeline & task records
# ------------------------------------------------------------------ #

class PipelineRecord(Base):
    """Persisted ETL pipeline definition."""

    __tablename__ = "dataforge_pipelines"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_new_uuid,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schedule_cron: Mapped[str | None] = mapped_column(String(100))
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    failure_strategy: Mapped[str] = mapped_column(String(30), nullable=False, default="stop")
    notification_channels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now(),
    )

    # Relationship
    tasks: Mapped[list[TaskRecord]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (
        Index("ix_pipelines_schedule", "schedule_cron"),
    )

    def __repr__(self) -> str:
        return f"<PipelineRecord {self.name!r}>"


class TaskRecord(Base):
    """A single ETL task within a pipeline."""

    __tablename__ = "dataforge_tasks"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_new_uuid,
    )
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dataforge_pipelines.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_connection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_connection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_query: Mapped[str] = mapped_column(Text, nullable=False)
    target_table: Mapped[str] = mapped_column(String(500), nullable=False)
    write_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="append")
    warehouse_layer: Mapped[str | None] = mapped_column(String(10))
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )

    # Relationship
    pipeline: Mapped[PipelineRecord] = relationship(back_populates="tasks")

    __table_args__ = (
        Index("ix_tasks_pipeline", "pipeline_id"),
    )

    def __repr__(self) -> str:
        return f"<TaskRecord {self.name!r} (pipeline={self.pipeline_id})>"


class TaskRunRecord(Base):
    """Execution result of a single ETL task run."""

    __tablename__ = "dataforge_task_runs"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_new_uuid,
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dataforge_tasks.id", ondelete="CASCADE"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    rows_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_task_runs_task", "task_id"),
        Index("ix_task_runs_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<TaskRunRecord task={self.task_id} status={self.status}>"


# ------------------------------------------------------------------ #
# Catalog records
# ------------------------------------------------------------------ #

class CatalogRecord(Base):
    """Persisted table metadata catalog entry."""

    __tablename__ = "dataforge_catalog"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_new_uuid,
    )
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    table_name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="other")
    layer: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    columns_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    primary_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    foreign_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partition_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    owner: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    sensitivity: Mapped[str] = mapped_column(String(30), nullable=False, default="internal")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    custom_properties: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    upstream_tables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    downstream_tables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now(),
    )
    last_profiled_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_catalog_connection", "connection_id"),
        Index("ix_catalog_layer", "layer"),
        Index("ix_catalog_table_name", "table_name"),
    )

    def __repr__(self) -> str:
        return f"<CatalogRecord {self.table_name!r} ({self.layer})>"


# ------------------------------------------------------------------ #
# Lineage records
# ------------------------------------------------------------------ #

class LineageNodeRecord(Base):
    """A node in the data-lineage graph."""

    __tablename__ = "dataforge_lineage_nodes"

    id: Mapped[str] = mapped_column(
        String(128), primary_key=True, default=_new_uuid,
    )
    node_type: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_lineage_nodes_type", "node_type"),
    )

    def __repr__(self) -> str:
        return f"<LineageNodeRecord {self.label!r} ({self.node_type})>"


class LineageEdgeRecord(Base):
    """A directed edge in the data-lineage graph."""

    __tablename__ = "dataforge_lineage_edges"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_new_uuid,
    )
    source_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("dataforge_lineage_nodes.id", ondelete="CASCADE"), nullable=False,
    )
    target_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("dataforge_lineage_nodes.id", ondelete="CASCADE"), nullable=False,
    )
    edge_type: Mapped[str] = mapped_column(String(50), nullable=False, default="DATA_FLOW")
    transformation: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_lineage_edges_source", "source_id"),
        Index("ix_lineage_edges_target", "target_id"),
    )

    def __repr__(self) -> str:
        return f"<LineageEdgeRecord {self.source_id} -> {self.target_id}>"
