"""
SQL Server adapter for DataForge AI.

Implements :class:`AbstractBaseAdapter` for Microsoft SQL Server 2016+
using the ``aioodbc`` async driver.  Metadata queries use
``INFORMATION_SCHEMA`` and ``sys.*`` catalog views.

SQL Server is common in enterprise environments where the data warehouse
lives on-premises or in Azure SQL.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import text

from src.core.exceptions import ConnectionError, QueryExecutionError
from src.core.schemas import (
    ColumnDataType,
    ColumnInfo,
    ConnectionTestResult,
    IndexInfo,
    IndexType,
    TableSchema,
    TableStats,
)
from src.db.base import AbstractBaseAdapter

logger = logging.getLogger(__name__)

_MSSQL_TYPE_MAP: dict[str, ColumnDataType] = {
    "tinyint": ColumnDataType.INTEGER,
    "smallint": ColumnDataType.INTEGER,
    "int": ColumnDataType.INTEGER,
    "bigint": ColumnDataType.BIGINT,
    "float": ColumnDataType.FLOAT,
    "real": ColumnDataType.FLOAT,
    "decimal": ColumnDataType.DECIMAL,
    "numeric": ColumnDataType.DECIMAL,
    "money": ColumnDataType.DECIMAL,
    "smallmoney": ColumnDataType.DECIMAL,
    "bit": ColumnDataType.BOOLEAN,
    "char": ColumnDataType.STRING,
    "varchar": ColumnDataType.STRING,
    "nchar": ColumnDataType.STRING,
    "nvarchar": ColumnDataType.STRING,
    "text": ColumnDataType.TEXT,
    "ntext": ColumnDataType.TEXT,
    "date": ColumnDataType.DATE,
    "datetime": ColumnDataType.TIMESTAMP,
    "datetime2": ColumnDataType.TIMESTAMP,
    "smalldatetime": ColumnDataType.TIMESTAMP,
    "datetimeoffset": ColumnDataType.TIMESTAMP,
    "time": ColumnDataType.STRING,
    "binary": ColumnDataType.BINARY,
    "varbinary": ColumnDataType.BINARY,
    "image": ColumnDataType.BINARY,
    "xml": ColumnDataType.TEXT,
    "uniqueidentifier": ColumnDataType.STRING,
    "sql_variant": ColumnDataType.STRING,
    "hierarchyid": ColumnDataType.STRING,
    "geography": ColumnDataType.BINARY,
    "geometry": ColumnDataType.BINARY,
}


def _map_mssql_type(native_type: str) -> ColumnDataType:
    base = native_type.lower().split("(")[0].strip()
    return _MSSQL_TYPE_MAP.get(base, ColumnDataType.STRING)


class SQLServerAdapter(AbstractBaseAdapter):
    """Adapter for Microsoft SQL Server 2016+."""

    db_type = "sqlserver"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        try:
            async with self.engine.connect() as conn:
                # Set session options
                await conn.execute(text("SET DATEFORMAT ymd"))
                await conn.execute(text("SET DATEFIRST 1"))  # Monday = first day
                await conn.commit()
            logger.info("SQL Server session configured for '%s'.", self.config.host)
        except Exception as exc:
            raise ConnectionError(
                message=f"SQL Server session setup failed: {exc}",
                details={"host": self.config.host},
            ) from exc

    async def disconnect(self) -> None:
        logger.debug("SQL Server adapter disconnect (no-op).")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT SERVERPROPERTY('ProductVersion') AS ver")
                )
                row = result.fetchone()
                version = str(row[0]) if row else "unknown"
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

    async def get_databases(self) -> list[str]:
        rows = await self.execute_query(
            "SELECT name FROM sys.databases WHERE state = 0 ORDER BY name"
        )
        return [row["name"] for row in rows]

    async def get_schemas(self, database: str | None = None) -> list[str]:
        rows = await self.execute_query(
            """
            SELECT s.name
            FROM sys.schemas s
            WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
            ORDER BY s.name
            """
        )
        return [row["name"] for row in rows]

    # ------------------------------------------------------------------ #
    # Metadata — tables
    # ------------------------------------------------------------------ #

    async def get_tables(
        self,
        database: str | None = None,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> list[str]:
        sch = self._default_schema(schema) or "dbo"
        sql = """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :schema
        """
        params: dict[str, Any] = {"schema": sch}
        if table_type:
            sql += " AND TABLE_TYPE = :ttype"
            params["ttype"] = table_type
        else:
            sql += " AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')"
        sql += " ORDER BY TABLE_NAME"
        rows = await self.execute_query(sql, params)
        return [row["TABLE_NAME"] for row in rows]

    async def get_table_schema(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> TableSchema:
        sch = self._default_schema(schema) or "dbo"
        db = self._default_database(database)
        columns = await self.get_columns(table_name, schema=sch)
        indexes = await self.get_indexes(table_name, schema=sch)

        # Table-level metadata from sys views
        sql = """
            SELECT
                t.type_desc,
                ep.value AS table_comment,
                p.rows AS row_count,
                SUM(a.total_pages) * 8 * 1024 AS total_bytes
            FROM sys.tables t
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            LEFT JOIN sys.partitions p ON t.object_id = p.object_id AND p.index_id <= 1
            LEFT JOIN sys.allocation_units a ON p.partition_id = a.container_id
            WHERE s.name = :schema AND t.name = :tbl
            GROUP BY t.type_desc, ep.value, p.rows
        """
        rows = await self.execute_query(sql, {"schema": sch, "tbl": table_name})
        info = rows[0] if rows else {}

        return TableSchema(
            connection_id=self.config.connection_id,
            database_name=db,
            schema_name=sch,
            table_name=table_name,
            table_type=info.get("type_desc", "USER_TABLE"),
            comment=info.get("table_comment"),
            columns=columns,
            indexes=indexes,
            row_count_estimate=info.get("row_count"),
            size_bytes=info.get("total_bytes"),
        )

    # ------------------------------------------------------------------ #
    # Metadata — columns & indexes
    # ------------------------------------------------------------------ #

    async def get_columns(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> list[ColumnInfo]:
        sch = self._default_schema(schema) or "dbo"
        sql = """
            SELECT
                c.name AS column_name,
                t.name AS data_type,
                c.is_nullable,
                dc.definition AS default_value,
                c.max_length,
                c.precision,
                c.scale,
                c.column_id,
                CASE WHEN ic.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_identity,
                ep.value AS column_comment,
                CASE WHEN pk.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key
            FROM sys.columns c
            JOIN sys.types t ON c.user_type_id = t.user_type_id
            JOIN sys.tables tbl ON c.object_id = tbl.object_id
            JOIN sys.schemas s ON tbl.schema_id = s.schema_id
            LEFT JOIN sys.default_constraints dc ON c.default_object_id = dc.object_id
            LEFT JOIN sys.identity_columns ic ON c.object_id = ic.object_id AND c.column_id = ic.column_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
            LEFT JOIN (
                SELECT ic2.object_id, ic2.column_id
                FROM sys.index_columns ic2
                JOIN sys.indexes i2 ON ic2.object_id = i2.object_id AND ic2.index_id = i2.index_id
                WHERE i2.is_primary_key = 1
            ) pk ON pk.object_id = c.object_id AND pk.column_id = c.column_id
            WHERE s.name = :schema AND tbl.name = :tbl
            ORDER BY c.column_id
        """
        rows = await self.execute_query(sql, {"schema": sch, "tbl": table_name})
        columns: list[ColumnInfo] = []
        for row in rows:
            data_type = row["data_type"]
            # Build a display type with length/precision
            display_type = data_type
            if row.get("max_length") and data_type in ("varchar", "nvarchar", "char", "nchar", "varbinary", "binary"):
                max_len = row["max_length"]
                if data_type.startswith("n"):
                    max_len = max_len // 2  # nvarchar stores 2 bytes per char
                display_type = f"{data_type}({max_len})" if max_len != -1 else f"{data_type}(MAX)"
            elif row.get("precision") is not None and data_type in ("decimal", "numeric"):
                display_type = f"{data_type}({row['precision']},{row.get('scale', 0)})"

            columns.append(
                ColumnInfo(
                    name=row["column_name"],
                    data_type=display_type,
                    logical_type=_map_mssql_type(data_type),
                    nullable=bool(row["is_nullable"]),
                    is_primary_key=bool(row.get("is_primary_key")),
                    default_value=row.get("default_value"),
                    comment=row.get("column_comment"),
                    ordinal_position=row["column_id"] - 1,
                    character_max_length=row.get("max_length") if data_type in ("varchar", "nvarchar", "char", "nchar") else None,
                    numeric_precision=row.get("precision"),
                    numeric_scale=row.get("scale"),
                    extra={"is_identity": bool(row.get("is_identity"))},
                )
            )
        return columns

    async def get_indexes(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> list[IndexInfo]:
        sch = self._default_schema(schema) or "dbo"
        sql = """
            SELECT
                i.name AS index_name,
                i.type_desc,
                i.is_unique,
                i.is_primary_key,
                i.is_clustered,
                STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS columns
            FROM sys.indexes i
            JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
            JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
            JOIN sys.tables t ON i.object_id = t.object_id
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = :schema AND t.name = :tbl AND i.name IS NOT NULL
            GROUP BY i.name, i.type_desc, i.is_unique, i.is_primary_key, i.is_clustered
            ORDER BY i.name
        """
        rows = await self.execute_query(sql, {"schema": sch, "tbl": table_name})
        indexes: list[IndexInfo] = []
        for row in rows:
            if row["is_primary_key"]:
                idx_type = IndexType.PRIMARY
            elif row["is_clustered"]:
                idx_type = IndexType.CLUSTERED
            elif row["is_unique"]:
                idx_type = IndexType.UNIQUE
            else:
                idx_type = IndexType.NORMAL

            col_str = row.get("columns", "")
            cols = [c.strip() for c in col_str.split(",")] if col_str else []

            indexes.append(
                IndexInfo(
                    name=row["index_name"],
                    index_type=idx_type,
                    columns=cols,
                    is_unique=bool(row["is_unique"]),
                    comment=row.get("type_desc"),
                )
            )
        return indexes

    # ------------------------------------------------------------------ #
    # Query execution
    # ------------------------------------------------------------------ #

    async def execute_query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        max_rows: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text(sql), parameters or {})
                if result.returns_rows:
                    columns = list(result.keys())
                    rows = result.fetchmany(max_rows) if max_rows else result.fetchall()
                    return [dict(zip(columns, row, strict=False)) for row in rows]
                return []
        except Exception as exc:
            raise QueryExecutionError(
                message=f"SQL Server query failed: {exc}",
                sql=sql,
            ) from exc

    async def execute_ddl(self, sql: str) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            raise QueryExecutionError(
                message=f"SQL Server DDL execution failed: {exc}",
                sql=sql,
            ) from exc

    # ------------------------------------------------------------------ #
    # DDL introspection
    # ------------------------------------------------------------------ #

    async def get_create_table_sql(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> str:
        """
        SQL Server has no built-in SHOW CREATE TABLE.  We reconstruct DDL
        from sys catalog views.
        """
        sch = self._default_schema(schema) or "dbo"
        columns = await self.get_columns(table_name, schema=sch)
        indexes = await self.get_indexes(table_name, schema=sch)

        lines: list[str] = []
        for col in columns:
            parts = [f"    [{col.name}] {col.data_type}"]
            if not col.nullable:
                parts.append("NOT NULL")
            else:
                parts.append("NULL")
            if col.default_value is not None:
                parts.append(f"DEFAULT ({col.default_value})")
            if col.extra.get("is_identity"):
                parts.append("IDENTITY(1,1)")
            lines.append(" ".join(parts))

        # Primary key constraint
        pk_cols = [c.name for c in columns if c.is_primary_key]
        if pk_cols:
            pk_list = ", ".join(f"[{c}]" for c in pk_cols)
            lines.append(f"    CONSTRAINT [PK_{table_name}] PRIMARY KEY CLUSTERED ({pk_list})")

        col_defs = ",\n".join(lines)
        ddl = f"CREATE TABLE [{sch}].[{table_name}] (\n{col_defs}\n);"

        # Secondary indexes
        for idx in indexes:
            if idx.index_type == IndexType.PRIMARY:
                continue
            unique = "UNIQUE " if idx.is_unique else ""
            clustered = "CLUSTERED " if idx.index_type == IndexType.CLUSTERED else "NONCLUSTERED "
            idx_cols = ", ".join(f"[{c}]" for c in idx.columns)
            ddl += f"\nCREATE {unique}{clustered}INDEX [{idx.name}] ON [{sch}].[{table_name}] ({idx_cols});"

        return ddl

    async def get_table_ddl(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> str:
        return await self.get_create_table_sql(table_name, database=database, schema=schema)

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #

    async def get_table_stats(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> TableStats:
        sch = self._default_schema(schema) or "dbo"
        sql = """
            SELECT
                p.rows AS row_count,
                SUM(a.total_pages) * 8 * 1024 AS total_bytes,
                SUM(a.used_pages) * 8 * 1024 AS used_bytes,
                CASE WHEN p.rows > 0
                     THEN (SUM(a.total_pages) * 8.0 * 1024) / p.rows
                     ELSE NULL
                END AS avg_row_size
            FROM sys.tables t
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            JOIN sys.partitions p ON t.object_id = p.object_id AND p.index_id <= 1
            JOIN sys.allocation_units a ON p.partition_id = a.container_id
            WHERE s.name = :schema AND t.name = :tbl
            GROUP BY p.rows
        """
        rows = await self.execute_query(sql, {"schema": sch, "tbl": table_name})
        if not rows:
            return TableStats(table_name=table_name)
        info = rows[0]
        return TableStats(
            table_name=table_name,
            row_count=info.get("row_count", 0),
            size_bytes=info.get("total_bytes"),
            avg_row_size_bytes=info.get("avg_row_size"),
        )

    async def get_server_version(self) -> str:
        try:
            rows = await self.execute_query(
                "SELECT SERVERPROPERTY('ProductVersion') AS ver"
            )
            return rows[0].get("ver", "unknown") if rows else "unknown"
        except Exception:
            return "unknown"
