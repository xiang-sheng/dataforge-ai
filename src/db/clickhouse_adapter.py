# -*- coding: utf-8 -*-
"""
ClickHouse adapter for DataForge AI.

Implements :class:`AbstractBaseAdapter` for ClickHouse 21+ using an async
driver.  ClickHouse is the preferred OLAP engine for DWS / ADS layers
because of its columnar storage and extreme analytical query performance.

Metadata queries use ``system.*`` tables which are the canonical source
of truth in ClickHouse.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.core.exceptions import ConnectionError, QueryExecutionError
from src.core.schemas import (
    ColumnDataType,
    ColumnInfo,
    ConnectionConfig,
    ConnectionTestResult,
    IndexInfo,
    IndexType,
    TableSchema,
    TableStats,
)
from src.db.base import AbstractBaseAdapter

logger = logging.getLogger(__name__)

_CH_TYPE_MAP: Dict[str, ColumnDataType] = {
    "int8": ColumnDataType.INTEGER,
    "int16": ColumnDataType.INTEGER,
    "int32": ColumnDataType.INTEGER,
    "int64": ColumnDataType.BIGINT,
    "int128": ColumnDataType.BIGINT,
    "int256": ColumnDataType.BIGINT,
    "uint8": ColumnDataType.INTEGER,
    "uint16": ColumnDataType.INTEGER,
    "uint32": ColumnDataType.INTEGER,
    "uint64": ColumnDataType.BIGINT,
    "uint128": ColumnDataType.BIGINT,
    "uint256": ColumnDataType.BIGINT,
    "float32": ColumnDataType.FLOAT,
    "float64": ColumnDataType.DOUBLE,
    "decimal": ColumnDataType.DECIMAL,
    "string": ColumnDataType.STRING,
    "fixedstring": ColumnDataType.STRING,
    "uuid": ColumnDataType.STRING,
    "date": ColumnDataType.DATE,
    "date32": ColumnDataType.DATE,
    "datetime": ColumnDataType.TIMESTAMP,
    "datetime64": ColumnDataType.TIMESTAMP,
    "bool": ColumnDataType.BOOLEAN,
    "boolean": ColumnDataType.BOOLEAN,
    "enum8": ColumnDataType.STRING,
    "enum16": ColumnDataType.STRING,
    "array": ColumnDataType.ARRAY,
    "map": ColumnDataType.MAP,
    "tuple": ColumnDataType.STRUCT,
    "nested": ColumnDataType.STRUCT,
    "json": ColumnDataType.JSON,
}


def _map_ch_type(native_type: str) -> ColumnDataType:
    """Map a ClickHouse type string to a logical ColumnDataType."""
    lower = native_type.lower().strip()
    # Handle Nullable(...) wrapper
    if lower.startswith("nullable("):
        lower = lower[len("nullable("):-1]
    # Handle LowCardinality(...)
    if lower.startswith("lowcardinality("):
        lower = lower[len("lowcardinality("):-1]
    # Strip parameters for base type lookup
    base = lower.split("(")[0].strip()
    return _CH_TYPE_MAP.get(base, ColumnDataType.STRING)


class ClickHouseAdapter(AbstractBaseAdapter):
    """Adapter for ClickHouse OLAP databases."""

    db_type = "clickhouse"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        try:
            async with self.engine.connect() as conn:
                # Use consistent date/time formats
                await conn.execute(text("SET date_time_input_format = 'basic'"))
                await conn.commit()
            logger.info("ClickHouse session configured for '%s'.", self.config.host)
        except Exception as exc:
            # Some ClickHouse builds do not support SET; log but don't fail
            logger.warning("ClickHouse session setup warning: %s", exc)

    async def disconnect(self) -> None:
        logger.debug("ClickHouse adapter disconnect (no-op).")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text("SELECT version() AS ver"))
                row = result.fetchone()
                version = row[0] if row else "unknown"
            latency = (time.perf_counter() - start) * 1000
            return ConnectionTestResult(
                success=True,
                message="Connection successful.",
                latency_ms=round(latency, 2),
                server_version=version,
            )
        except Exception as exc:
            latency = (time.perf_counter() - start) * 1000
            return ConnectionTestResult(
                success=False,
                message=f"Connection failed: {exc}",
                latency_ms=round(latency, 2),
            )

    # ------------------------------------------------------------------ #
    # Metadata — databases & schemas
    # ------------------------------------------------------------------ #

    async def get_databases(self) -> List[str]:
        rows = await self.execute_query(
            "SELECT name FROM system.databases ORDER BY name"
        )
        return [row["name"] for row in rows]

    async def get_schemas(self, database: Optional[str] = None) -> List[str]:
        # ClickHouse has no schema namespace; databases are the top level
        return [""]

    # ------------------------------------------------------------------ #
    # Metadata — tables
    # ------------------------------------------------------------------ #

    async def get_tables(
        self,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        table_type: Optional[str] = None,
    ) -> List[str]:
        db = self._default_database(database) or "default"
        sql = """
            SELECT name
            FROM system.tables
            WHERE database = :db
        """
        params: Dict[str, Any] = {"db": db}
        if table_type:
            sql += " AND engine = :engine"
            params["engine"] = table_type
        sql += " ORDER BY name"
        rows = await self.execute_query(sql, params)
        return [row["name"] for row in rows]

    async def get_table_schema(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> TableSchema:
        db = self._default_database(database) or "default"
        columns = await self.get_columns(table_name, database=db)
        indexes = await self.get_indexes(table_name, database=db)

        sql = """
            SELECT
                engine,
                comment,
                total_rows,
                total_bytes,
                create_table_query,
                metadata_modification_time
            FROM system.tables
            WHERE database = :db AND name = :tbl
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        info = rows[0] if rows else {}

        return TableSchema(
            connection_id=self.config.connection_id,
            database_name=db,
            schema_name=None,
            table_name=table_name,
            table_type=info.get("engine", "MergeTree"),
            comment=info.get("comment") or None,
            columns=columns,
            indexes=indexes,
            row_count_estimate=info.get("total_rows"),
            size_bytes=info.get("total_bytes"),
            updated_at=info.get("metadata_modification_time"),
        )

    # ------------------------------------------------------------------ #
    # Metadata — columns & indexes
    # ------------------------------------------------------------------ #

    async def get_columns(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> List[ColumnInfo]:
        db = self._default_database(database) or "default"
        sql = """
            SELECT
                name,
                type,
                position,
                default_kind,
                default_expression,
                comment,
                is_in_primary_key,
                numeric_precision,
                numeric_scale
            FROM system.columns
            WHERE database = :db AND table = :tbl
            ORDER BY position
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        columns: List[ColumnInfo] = []
        for row in rows:
            default_val = row.get("default_expression") or None
            default_kind = row.get("default_kind") or ""
            if default_val and default_kind:
                default_val = f"{default_kind} {default_val}"

            columns.append(
                ColumnInfo(
                    name=row["name"],
                    data_type=row["type"],
                    logical_type=_map_ch_type(row["type"]),
                    nullable="Nullable" in row["type"],
                    is_primary_key=bool(row.get("is_in_primary_key", 0)),
                    default_value=default_val,
                    comment=row.get("comment") or None,
                    ordinal_position=row["position"] - 1,
                    numeric_precision=row.get("numeric_precision"),
                    numeric_scale=row.get("numeric_scale"),
                    extra={"engine": "clickhouse"},
                )
            )
        return columns

    async def get_indexes(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> List[IndexInfo]:
        db = self._default_database(database) or "default"
        # ClickHouse uses data skipping indexes, not traditional B-tree indexes
        sql = """
            SELECT
                name,
                type,
                expr,
                granularity
            FROM system.data_skipping_indices
            WHERE database = :db AND table = :tbl
            ORDER BY name
        """
        try:
            rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        except QueryExecutionError:
            # Older ClickHouse versions may not have this table
            return []

        indexes: List[IndexInfo] = []
        for row in rows:
            indexes.append(
                IndexInfo(
                    name=row["name"],
                    index_type=IndexType.NORMAL,
                    columns=[row.get("expr", "")],
                    is_unique=False,
                    comment=f"Type: {row.get('type', 'unknown')}, Granularity: {row.get('granularity', 'N/A')}",
                )
            )
        return indexes

    # ------------------------------------------------------------------ #
    # Query execution
    # ------------------------------------------------------------------ #

    async def execute_query(
        self,
        sql: str,
        parameters: Optional[Dict[str, Any]] = None,
        max_rows: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text(sql), parameters or {})
                if result.returns_rows:
                    columns = list(result.keys())
                    rows = result.fetchmany(max_rows) if max_rows else result.fetchall()
                    return [dict(zip(columns, row)) for row in rows]
                return []
        except Exception as exc:
            raise QueryExecutionError(
                message=f"ClickHouse query failed: {exc}",
                sql=sql,
            ) from exc

    async def execute_ddl(self, sql: str) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            raise QueryExecutionError(
                message=f"ClickHouse DDL execution failed: {exc}",
                sql=sql,
            ) from exc

    # ------------------------------------------------------------------ #
    # DDL introspection
    # ------------------------------------------------------------------ #

    async def get_create_table_sql(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> str:
        db = self._default_database(database) or "default"
        sql = """
            SELECT create_table_query
            FROM system.tables
            WHERE database = :db AND name = :tbl
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        if rows and rows[0].get("create_table_query"):
            return rows[0]["create_table_query"]
        raise QueryExecutionError(
            message=f"Could not retrieve CREATE TABLE for '{db}.{table_name}'.",
            sql=sql,
        )

    async def get_table_ddl(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> str:
        return await self.get_create_table_sql(table_name, database=database)

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #

    async def get_table_stats(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> TableStats:
        db = self._default_database(database) or "default"
        sql = """
            SELECT
                total_rows,
                total_bytes,
                CASE WHEN total_rows > 0
                     THEN total_bytes / total_rows
                     ELSE NULL
                END AS avg_row_size,
                metadata_modification_time
            FROM system.tables
            WHERE database = :db AND name = :tbl
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        if not rows:
            return TableStats(table_name=table_name)
        info = rows[0]
        return TableStats(
            table_name=table_name,
            row_count=info.get("total_rows", 0),
            size_bytes=info.get("total_bytes"),
            avg_row_size_bytes=info.get("avg_row_size"),
            last_analyzed=info.get("metadata_modification_time"),
        )

    # ------------------------------------------------------------------ #
    # Version override
    # ------------------------------------------------------------------ #

    async def get_server_version(self) -> str:
        try:
            rows = await self.execute_query("SELECT version() AS ver")
            return rows[0].get("ver", "unknown") if rows else "unknown"
        except Exception:
            return "unknown"
