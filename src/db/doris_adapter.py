"""
Apache Doris adapter for DataForge AI.

Doris speaks the MySQL protocol, so this adapter re-uses the MySQL driver
(``aiomysql``) but overrides metadata queries to use Doris-specific
``INFORMATION_SCHEMA`` columns and ``SHOW`` syntax.

Doris is commonly used as the serving layer (ADS) in Chinese data-warehouse
architectures due to its real-time ingestion and sub-second query latency.
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

# Doris uses MySQL-compatible types; reuse the same mapping
_DORIS_TYPE_MAP: dict[str, ColumnDataType] = {
    "tinyint": ColumnDataType.INTEGER,
    "smallint": ColumnDataType.INTEGER,
    "int": ColumnDataType.INTEGER,
    "bigint": ColumnDataType.BIGINT,
    "largeint": ColumnDataType.BIGINT,
    "float": ColumnDataType.FLOAT,
    "double": ColumnDataType.DOUBLE,
    "decimal": ColumnDataType.DECIMAL,
    "decimalv3": ColumnDataType.DECIMAL,
    "date": ColumnDataType.DATE,
    "datev2": ColumnDataType.DATE,
    "datetime": ColumnDataType.TIMESTAMP,
    "datetimev2": ColumnDataType.TIMESTAMP,
    "char": ColumnDataType.STRING,
    "varchar": ColumnDataType.STRING,
    "string": ColumnDataType.TEXT,
    "text": ColumnDataType.TEXT,
    "boolean": ColumnDataType.BOOLEAN,
    "bitmap": ColumnDataType.BINARY,
    "hll": ColumnDataType.BINARY,
    "json": ColumnDataType.JSON,
    "jsonb": ColumnDataType.JSON,
    "array": ColumnDataType.ARRAY,
    "map": ColumnDataType.MAP,
    "struct": ColumnDataType.STRUCT,
}


def _map_doris_type(native_type: str) -> ColumnDataType:
    base = native_type.lower().split("(")[0].strip()
    return _DORIS_TYPE_MAP.get(base, ColumnDataType.STRING)


class DorisAdapter(AbstractBaseAdapter):
    """Adapter for Apache Doris (MySQL protocol compatible)."""

    db_type = "doris"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SET time_zone = '+00:00'"))
                await conn.commit()
            logger.info("Doris session configured for '%s'.", self.config.host)
        except Exception as exc:
            raise ConnectionError(
                message=f"Doris session setup failed: {exc}",
                details={"host": self.config.host},
            ) from exc

    async def disconnect(self) -> None:
        logger.debug("Doris adapter disconnect (no-op).")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                # Doris supports SHOW FRONTENDS for version info
                result = await conn.execute(text("SELECT current_version()"))
                row = result.fetchone()
                version = row[0] if row else "unknown"
            latency = (time.perf_counter() - start) * 1000
            return ConnectionTestResult(
                success=True,
                message="Connection successful.",
                latency_ms=round(latency, 2),
                server_version=version,
            )
        except Exception:
            # Fallback if current_version() is not available
            try:
                async with self.engine.connect() as conn:
                    result = await conn.execute(text("SELECT 1"))
                    result.fetchone()
                latency = (time.perf_counter() - start) * 1000
                return ConnectionTestResult(
                    success=True,
                    message="Connection successful (version unavailable).",
                    latency_ms=round(latency, 2),
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
            FROM information_schema.tables
            WHERE TABLE_SCHEMA = :db
            ORDER BY TABLE_NAME
        """
        rows = await self.execute_query(sql, {"db": db})
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

        # Doris-specific: use SHOW TABLE STATUS for metadata
        try:
            status_rows = await self.execute_query(
                f"SHOW TABLE STATUS FROM `{db}` LIKE :tbl", {"tbl": table_name}
            )
            info = status_rows[0] if status_rows else {}
        except Exception:
            info = {}

        return TableSchema(
            connection_id=self.config.connection_id,
            database_name=db,
            schema_name=None,
            table_name=table_name,
            table_type=info.get("Type", "TABLE") or "TABLE",
            comment=info.get("Comment") or None,
            columns=columns,
            indexes=indexes,
            row_count_estimate=info.get("Rows"),
            size_bytes=info.get("Data_length"),
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
                NUMERIC_SCALE
            FROM information_schema.columns
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl
            ORDER BY ORDINAL_POSITION
        """
        rows = await self.execute_query(sql, {"db": db, "tbl": table_name})
        columns: list[ColumnInfo] = []
        for row in rows:
            columns.append(
                ColumnInfo(
                    name=row["COLUMN_NAME"],
                    data_type=row.get("COLUMN_TYPE") or row["DATA_TYPE"],
                    logical_type=_map_doris_type(row["DATA_TYPE"]),
                    nullable=row["IS_NULLABLE"] == "YES",
                    is_primary_key=row.get("COLUMN_KEY") == "PRI",
                    default_value=row.get("COLUMN_DEFAULT"),
                    comment=row.get("COLUMN_COMMENT") or None,
                    ordinal_position=row["ORDINAL_POSITION"] - 1,
                    character_max_length=row.get("CHARACTER_MAXIMUM_LENGTH"),
                    numeric_precision=row.get("NUMERIC_PRECISION"),
                    numeric_scale=row.get("NUMERIC_SCALE"),
                )
            )
        return columns

    async def get_indexes(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> list[IndexInfo]:
        # Doris does not support traditional secondary indexes.
        # It uses bloom filters and bitmap indexes defined at table creation.
        # We attempt to extract any defined indexes via SHOW INDEX.
        db = self._default_database(database)
        try:
            rows = await self.execute_query(
                f"SHOW INDEX FROM `{db}`.`{table_name}`"
            )
        except Exception:
            return []

        index_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = row.get("Key_name", "")
            if name not in index_map:
                if name == "PRIMARY":
                    idx_type = IndexType.PRIMARY
                elif not row.get("Non_unique", True):
                    idx_type = IndexType.UNIQUE
                else:
                    idx_type = IndexType.NORMAL
                index_map[name] = {
                    "name": name,
                    "index_type": idx_type,
                    "columns": [],
                    "is_unique": not row.get("Non_unique", True),
                }
            col = row.get("Column_name", "")
            if col:
                index_map[name]["columns"].append(col)

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
                message=f"Doris query failed: {exc}",
                sql=sql,
            ) from exc

    async def execute_ddl(self, sql: str) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            raise QueryExecutionError(
                message=f"Doris DDL execution failed: {exc}",
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
        try:
            rows = await self.execute_query(f"SHOW CREATE TABLE {qualified}")
        except Exception:
            # Doris also supports SHOW CREATE TABLE without db qualifier
            rows = await self.execute_query(f"SHOW CREATE TABLE `{table_name}`")
        if rows:
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
        try:
            rows = await self.execute_query(
                f"SHOW TABLE STATUS FROM `{db}` LIKE :tbl", {"tbl": table_name}
            )
        except Exception:
            return TableStats(table_name=table_name)

        if not rows:
            return TableStats(table_name=table_name)
        info = rows[0]
        return TableStats(
            table_name=table_name,
            row_count=info.get("Rows", 0),
            size_bytes=info.get("Data_length"),
            avg_row_size_bytes=info.get("Avg_row_length"),
        )

    async def get_server_version(self) -> str:
        try:
            rows = await self.execute_query("SELECT current_version()")
            return rows[0].get("current_version()", "unknown") if rows else "unknown"
        except Exception:
            return "unknown"
