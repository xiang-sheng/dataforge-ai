# -*- coding: utf-8 -*-
"""
Abstract base adapter for database engines.

Every concrete adapter (MySQL, PostgreSQL, ClickHouse, ...) inherits from
:class:`AbstractBaseAdapter` and implements the full interface.  The ABC
guarantees a uniform API regardless of the underlying engine, which is
critical for the adapter factory, lineage extraction, and SQL generation
pipelines.

Design notes:
    - All public methods are **async** to integrate cleanly with
      FastAPI's event loop and SQLAlchemy's async engines.
    - Adapters are *stateless* between calls; connection state lives in
      the :class:`ConnectionManager`.
    - Methods return Pydantic models defined in ``src.core.schemas``
      so that results are serialisable and validated.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.schemas import (
    ColumnInfo,
    ConnectionConfig,
    ConnectionTestResult,
    IndexInfo,
    TableSchema,
    TableStats,
)


class AbstractBaseAdapter(abc.ABC):
    """
    Interface contract that every database adapter must fulfil.

    Adapters are the *only* layer that contains engine-specific SQL.
    Everything above them works with Pydantic models and logical types.

    Parameters
    ----------
    config:
        The :class:`ConnectionConfig` describing the target database.
    engine:
        A pre-created SQLAlchemy :class:`AsyncEngine` obtained from the
        :class:`ConnectionManager`.  Adapters never create their own engines.
    """

    # Subclasses should set this to their ``DatabaseType`` enum value.
    db_type: str = ""

    def __init__(self, config: ConnectionConfig, engine: AsyncEngine) -> None:
        self.config = config
        self.engine = engine

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"db_type={self.db_type!r} "
            f"host={self.config.host}:{self.config.port} "
            f"db={self.config.database!r}>"
        )

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def connect(self) -> None:
        """
        Perform any adapter-specific initialisation after the engine is
        created (e.g. setting session variables, timezone, search_path).

        The engine itself is already connected — this hook is for
        *session-level* configuration.
        """

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """
        Release adapter-specific resources.

        The engine is disposed by the :class:`ConnectionManager`, so this
        method should only clean up adapter-internal state.
        """

    @abc.abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """
        Execute a lightweight probe and return connectivity metadata.

        Implementations should measure latency and attempt to retrieve
        the server version string.
        """

    # ------------------------------------------------------------------ #
    # Metadata discovery — databases & schemas
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def get_databases(self) -> List[str]:
        """
        Return the list of databases (catalogs) visible to the current user.

        For engines that do not have a catalog concept (e.g. SQLite),
        return a single-element list.
        """

    @abc.abstractmethod
    async def get_schemas(self, database: Optional[str] = None) -> List[str]:
        """
        Return schemas within the given database (or the default database).

        Engines without a schema namespace (MySQL) should return ``[""]``
        or ``["default"]``.
        """

    # ------------------------------------------------------------------ #
    # Metadata discovery — tables
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def get_tables(
        self,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        table_type: Optional[str] = None,
    ) -> List[str]:
        """
        Return table names in the given database / schema.

        Parameters
        ----------
        database:
            Catalog / database name.  ``None`` = use default.
        schema:
            Schema name (PostgreSQL, SQL Server, Oracle).
        table_type:
            Optional filter: ``'TABLE'``, ``'VIEW'``, ``'MATERIALIZED VIEW'``, etc.
        """

    @abc.abstractmethod
    async def get_table_schema(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> TableSchema:
        """
        Return full metadata for a single table, including columns and indexes.
        """

    # ------------------------------------------------------------------ #
    # Metadata discovery — columns & indexes
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def get_columns(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> List[ColumnInfo]:
        """Return column metadata for the specified table."""

    @abc.abstractmethod
    async def get_indexes(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> List[IndexInfo]:
        """Return index metadata for the specified table."""

    # ------------------------------------------------------------------ #
    # Query execution
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def execute_query(
        self,
        sql: str,
        parameters: Optional[Dict[str, Any]] = None,
        max_rows: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a read-only SQL query and return the result set as a list
        of dicts.

        Parameters
        ----------
        sql:
            The SQL SELECT statement.
        parameters:
            Bind parameters (named style).
        max_rows:
            Cap on returned rows.  ``None`` = use adapter default.
        """

    @abc.abstractmethod
    async def execute_ddl(self, sql: str) -> None:
        """
        Execute a DDL statement (CREATE TABLE, ALTER TABLE, DROP, ...).

        This method does **not** return rows.  It raises
        :class:`QueryExecutionError` on failure.
        """

    # ------------------------------------------------------------------ #
    # DDL introspection
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def get_create_table_sql(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> str:
        """
        Return the ``CREATE TABLE`` DDL that would reproduce the table's
        current structure.

        Some engines provide ``SHOW CREATE TABLE``; others require the
        adapter to reconstruct the DDL from metadata.
        """

    @abc.abstractmethod
    async def get_table_ddl(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> str:
        """
        Alias / convenience wrapper around :meth:`get_create_table_sql`.

        Adapters may override this to provide a normalised, cross-engine
        DDL format in addition to the engine-native one.
        """

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def get_table_stats(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> TableStats:
        """
        Return live or cached statistics for a table (row count, size, ...).
        """

    # ------------------------------------------------------------------ #
    # Utility helpers (concrete — may be overridden)
    # ------------------------------------------------------------------ #

    def _default_database(self, database: Optional[str] = None) -> str:
        """Resolve the effective database name."""
        return database or self.config.database or ""

    def _default_schema(self, schema: Optional[str] = None) -> str:
        """Resolve the effective schema name."""
        return schema or self.config.schema_name or ""

    async def get_server_version(self) -> str:
        """
        Return the database server version string.

        Default implementation runs ``SELECT version()`` — subclasses
        should override for engine-specific queries.
        """
        try:
            rows = await self.execute_query("SELECT version()")
            return str(rows[0].get("version", "unknown")) if rows else "unknown"
        except Exception:
            return "unknown"
