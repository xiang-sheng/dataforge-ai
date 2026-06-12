"""
MySQL adapter for DataForge AI.

Implements :class:`AbstractBaseAdapter` for MySQL 5.7+ / 8.0+ using the
``aiomysql`` async driver.  All metadata queries use ``INFORMATION_SCHEMA``
for portability across MySQL-compatible forks (MariaDB, Percona, etc.).
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

# Mapping from MySQL native types to logical ColumnDataType
_MYSQL_TYPE_MAP: dict[str, ColumnDataType] = {
    "tinyint": ColumnDataType.INTEGER,
    "smallint": ColumnDataType.INTEGER,
    "mediumint": ColumnDataType.INTEGER,
    "int": ColumnDataType.INTEGER,
    "integer": ColumnDataType.INTEGER,
    "bigint": ColumnDataType.BIGINT,
    "float": ColumnDataType.FLOAT,
    "double": ColumnDataType.DOUBLE,
    "decimal": ColumnDataType.DECIMAL,
    "numeric": ColumnDataType.DECIMAL,
    "char": ColumnDataType.STRING,
    "varchar": ColumnDataType.STRING,
    "tinytext": ColumnDataType.TEXT,
    "text": ColumnDataType.TEXT,
    "mediumtext": ColumnDataType.TEXT,
    "longtext": ColumnDataType.TEXT,
    "enum": ColumnDataType.STRING,
    "set": ColumnDataType.STRING,
    "date": ColumnDataType.DATE,
    "datetime": ColumnDataType.TIMESTAMP,
    "timestamp": ColumnDataType.TIMESTAMP,
    "time": ColumnDataType.STRING,
    "year": ColumnDataType.INTEGER,
    "bit": ColumnDataType.BINARY,
    "binary": ColumnDataType.BINARY,
    "varbinary": ColumnDataType.BINARY,
    "tinyblob": ColumnDataType.BINARY,
    "blob": ColumnDataType.BINARY,
    "mediumblob": ColumnDataType.BINARY,
    "longblob": ColumnDataType.BINARY,
    "json": ColumnDataType.JSON,
    "boolean": ColumnDataType.BOOLEAN,
    "bool": ColumnDataType.BOOLEAN,
}


def _map_mysql_type(native_type: str) -> ColumnDataType:
    """Map a MySQL column type string to a logical ColumnDataType."""
    base_type = native_type.lower().split("(")[0].strip()
    return _MYSQL_TYPE_MAP.get(base_type, ColumnDataType.STRING)


class MySQLAdapter(AbstractBaseAdapter):
    """Adapter for MySQL 5.7+ / 8.0+ databases."""

    db_type = "mysql"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        """Set session-level variables for consistent behaviour."""
        try:
            async with self.engine.connect() as conn:
                # Use UTC timestamps consistently
                await conn.execute(text("SET time_zone = '+00:00'"))
                # Increase GROUP_CONCAT limit for metadata queries
                await conn.execute(text("SET SESSION group_concat_max_len = 65536"))
                # Ensure we get proper column metadata
                await conn.execute(text("SET SESSION sql_mode = 'ANSI_QUOTES'"))
                await conn.commit()
            logger.info("MySQL session configured for '%s'.", self.config.host)
        except Exception as exc:
            raise ConnectionError(
                message=f"MySQL session setup failed: {exc}",
                details={"host": self.config.host},
            ) from exc

    async def disconnect(self) -> None:
        """No adapter-internal state to clean up."""
        logger.debug("MySQL adapter disconnect (no-op, engine managed externally).")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text("SELECT VERSION() AS ver"))
                row = result.mappings().fetchone()
                version = row["ver"] if row else "unknown"
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
        rows = await self.execute_query("SHOW DATABASES")
        return [row.get("Database", "") for row in rows if row.get("Database")]

    async def get_schemas(self, database: str | None = None) -> list[str]:
        # MySQL does not have a separate schema namespace
        return [""]

    # ------------------------------------------------------------------ #
    # Metadata — tables
    # ------------------------------------------------------------------ #

    async def get_tables(
        self,
        database: str | None = None,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> list[str]:
        db = self._default_database(database)
        sql = """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :db
        """
        params: dict[str, Any] = {"db": db}
        if table_type:
            sql += " AND TABLE_TYPE = :ttype"
            params["ttype"] = table_type
        sql += " ORDER BY TABLE_NAME"
        rows = await self.execute_query(sql, params)
        return [row["TABLE_NAME"] for row in rows]

    async def get_table_schema(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> TableSchema:
        db = self._default_database(database)
        columns = await self.get_columns(table_name, database=db)
        indexes = await self.get_indexes(table_name, database=db)

        # Fetch table-level metadata
        sql = """
            SELECT TABLE_TYPE, TABLE_COMMENT, TABLE_ROWS,
                   DATA_LENGTH + INDEX_LENGTH AS SIZE_BYTES,
                   CREATE_TIME, UPDATE_TIME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        info = rows[0] if rows else {}

        return TableSchema(
            connection_id=self.config.connection_id,
            database_name=db,
            schema_name=None,
            table_name=table_name,
            table_type=info.get("TABLE_TYPE", "TABLE"),
            comment=info.get("TABLE_COMMENT") or None,
            columns=columns,
            indexes=indexes,
            row_count_estimate=info.get("TABLE_ROWS"),
            size_bytes=info.get("SIZE_BYTES"),
            created_at=info.get("CREATE_TIME"),
            updated_at=info.get("UPDATE_TIME"),
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
        db = self._default_database(database)
        sql = """
            SELECT
                COLUMN_NAME,
                DATA_TYPE,
                COLUMN_TYPE,
                IS_NULLABLE,
                COLUMN_KEY,
                COLUMN_DEFAULT,
                COLUMN_COMMENT,
                ORDINAL_POSITION,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                EXTRA
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl
            ORDER BY ORDINAL_POSITION
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        columns: list[ColumnInfo] = []
        for row in rows:
            columns.append(
                ColumnInfo(
                    name=row["COLUMN_NAME"],
                    data_type=row["COLUMN_TYPE"],
                    logical_type=_map_mysql_type(row["DATA_TYPE"]),
                    nullable=row["IS_NULLABLE"] == "YES",
                    is_primary_key=row["COLUMN_KEY"] == "PRI",
                    default_value=row.get("COLUMN_DEFAULT"),
                    comment=row.get("COLUMN_COMMENT") or None,
                    ordinal_position=row["ORDINAL_POSITION"] - 1,
                    character_max_length=row.get("CHARACTER_MAXIMUM_LENGTH"),
                    numeric_precision=row.get("NUMERIC_PRECISION"),
                    numeric_scale=row.get("NUMERIC_SCALE"),
                    extra={"extra": row.get("EXTRA", "")},
                )
            )
        return columns

    async def get_indexes(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> list[IndexInfo]:
        db = self._default_database(database)
        sql = """
            SELECT
                INDEX_NAME,
                NON_UNIQUE,
                COLUMN_NAME,
                SEQ_IN_INDEX,
                INDEX_TYPE
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})

        # Group by index name
        index_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = row["INDEX_NAME"]
            if name not in index_map:
                if name == "PRIMARY":
                    idx_type = IndexType.PRIMARY
                elif row["NON_UNIQUE"] == 0:
                    idx_type = IndexType.UNIQUE
                elif row.get("INDEX_TYPE") == "FULLTEXT":
                    idx_type = IndexType.FULLTEXT
                elif row.get("INDEX_TYPE") == "SPATIAL":
                    idx_type = IndexType.SPATIAL
                else:
                    idx_type = IndexType.NORMAL

                index_map[name] = {
                    "name": name,
                    "index_type": idx_type,
                    "columns": [],
                    "is_unique": row["NON_UNIQUE"] == 0,
                }
            index_map[name]["columns"].append(row["COLUMN_NAME"])

        return [IndexInfo(**v) for v in index_map.values()]

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
                message=f"MySQL query failed: {exc}",
                sql=sql,
            ) from exc

    async def execute_ddl(self, sql: str) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            raise QueryExecutionError(
                message=f"MySQL DDL execution failed: {exc}",
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
        db = self._default_database(database)
        qualified = f"`{db}`.`{table_name}`" if db else f"`{table_name}`"
        rows = await self.execute_query(f"SHOW CREATE TABLE {qualified}")
        if rows:
            # SHOW CREATE TABLE returns (Table, Create Table) columns
            return rows[0].get("Create Table", "")
        raise QueryExecutionError(
            message=f"Could not retrieve CREATE TABLE for '{qualified}'.",
            sql=f"SHOW CREATE TABLE {qualified}",
        )

    async def get_table_ddl(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> str:
        return await self.get_create_table_sql(table_name, database=database)

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #

    async def get_table_stats(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> TableStats:
        db = self._default_database(database)
        sql = """
            SELECT
                TABLE_ROWS,
                DATA_LENGTH,
                INDEX_LENGTH,
                AVG_ROW_LENGTH,
                UPDATE_TIME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        if not rows:
            return TableStats(table_name=table_name)
        info = rows[0]
        size = (info.get("DATA_LENGTH") or 0) + (info.get("INDEX_LENGTH") or 0)
        return TableStats(
            table_name=table_name,
            row_count=info.get("TABLE_ROWS", 0),
            size_bytes=size,
            avg_row_size_bytes=info.get("AVG_ROW_LENGTH"),
            last_analyzed=info.get("UPDATE_TIME"),
        )

    # ------------------------------------------------------------------ #
    # Version override
    # ------------------------------------------------------------------ #

    async def get_server_version(self) -> str:
        try:
            rows = await self.execute_query("SELECT VERSION() AS ver")
            return rows[0]["ver"] if rows else "unknown"
        except Exception:
            return "unknown"
