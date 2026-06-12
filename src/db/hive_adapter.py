"""
Apache Hive adapter for DataForge AI.

Implements :class:`AbstractBaseAdapter` for Hive 2.x / 3.x using the
PyHive thrift driver.  Hive is commonly used in the ODS layer for
batch-ingestion on Hadoop ecosystems.

Caveats:
    - Hive does not support traditional transactions; ``execute_ddl``
      commits are implicit.
    - Index support was removed in Hive 3; the adapter still attempts
      to query index metadata for Hive 2 compatibility.
    - Row counts come from table properties (``numRows``) populated by
      ``ANALYZE TABLE``.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from sqlalchemy import text

from src.core.exceptions import QueryExecutionError
from src.core.schemas import (
    ColumnDataType,
    ColumnInfo,
    ConnectionTestResult,
    IndexInfo,
    TableSchema,
    TableStats,
)
from src.db.base import AbstractBaseAdapter

logger = logging.getLogger(__name__)

_HIVE_TYPE_MAP: dict[str, ColumnDataType] = {
    "tinyint": ColumnDataType.INTEGER,
    "smallint": ColumnDataType.INTEGER,
    "int": ColumnDataType.INTEGER,
    "bigint": ColumnDataType.BIGINT,
    "float": ColumnDataType.FLOAT,
    "double": ColumnDataType.DOUBLE,
    "decimal": ColumnDataType.DECIMAL,
    "string": ColumnDataType.STRING,
    "varchar": ColumnDataType.STRING,
    "char": ColumnDataType.STRING,
    "boolean": ColumnDataType.BOOLEAN,
    "date": ColumnDataType.DATE,
    "timestamp": ColumnDataType.TIMESTAMP,
    "binary": ColumnDataType.BINARY,
    "array": ColumnDataType.ARRAY,
    "map": ColumnDataType.MAP,
    "struct": ColumnDataType.STRUCT,
    "uniontype": ColumnDataType.STRUCT,
}


def _map_hive_type(native_type: str) -> ColumnDataType:
    base = native_type.lower().split("(")[0].split("<")[0].strip()
    return _HIVE_TYPE_MAP.get(base, ColumnDataType.STRING)


class HiveAdapter(AbstractBaseAdapter):
    """Adapter for Apache Hive via Thrift."""

    db_type = "hive"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        """Set Hive session properties."""
        try:
            async with self.engine.connect() as conn:
                # Use Tez execution engine if available
                await conn.execute(text("SET hive.execution.engine=tez"))
                await conn.commit()
            logger.info("Hive session configured for '%s'.", self.config.host)
        except Exception as exc:
            # Hive may not support SET in all drivers; log and continue
            logger.warning("Hive session setup warning: %s", exc)

    async def disconnect(self) -> None:
        logger.debug("Hive adapter disconnect (no-op).")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text("SELECT version()"))
                row = result.fetchone()
                version = str(row[0]) if row else "unknown"
            latency = (time.perf_counter() - start) * 1000
            return ConnectionTestResult(
                success=True,
                message="Connection successful.",
                latency_ms=round(latency, 2),
                server_version=version,
            )
        except Exception:
            # Many Hive drivers do not support SELECT version();
            # fall back to a simpler probe
            try:
                async with self.engine.connect() as conn:
                    result = await conn.execute(text("SHOW DATABASES"))
                    result.fetchall()
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
        # SHOW DATABASES returns a single column; the key varies by driver
        return [next(iter(row.values())) for row in rows if row]

    async def get_schemas(self, database: str | None = None) -> list[str]:
        # Hive uses databases as the top-level namespace (no schemas)
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
        db = self._default_database(database) or "default"
        sql = f"SHOW TABLES IN `{db}`"
        rows = await self.execute_query(sql)
        tables = [next(iter(row.values())) for row in rows if row]

        # Filter by type if requested (requires DESCRIBE FORMATTED per table)
        # For efficiency, we skip filtering and return all tables
        return tables

    async def get_table_schema(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> TableSchema:
        db = self._default_database(database) or "default"
        columns = await self.get_columns(table_name, database=db)
        indexes = await self.get_indexes(table_name, database=db)

        # Try to get table properties via DESCRIBE FORMATTED
        comment = None
        table_type = "TABLE"
        try:
            rows = await self.execute_query(
                f"DESCRIBE FORMATTED `{db}`.`{table_name}`"
            )
            for row in rows:
                vals = list(row.values())
                if len(vals) >= 2:
                    key = str(vals[0]).strip().lower() if vals[0] else ""
                    value = str(vals[1]).strip() if vals[1] else ""
                    if key == "comment":
                        comment = value
                    if key in ("tabletype:", "type:"):
                        table_type = value.upper()
                    if "view" in key:
                        table_type = "VIEW"
        except Exception:
            pass

        return TableSchema(
            connection_id=self.config.connection_id,
            database_name=db,
            schema_name=None,
            table_name=table_name,
            table_type=table_type,
            comment=comment,
            columns=columns,
            indexes=indexes,
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
        db = self._default_database(database) or "default"
        sql = f"DESCRIBE `{db}`.`{table_name}`"
        rows = await self.execute_query(sql)

        columns: list[ColumnInfo] = []
        position = 0
        in_partition_section = False

        for row in rows:
            vals = list(row.values())
            if not vals or not vals[0]:
                continue

            col_name = str(vals[0]).strip()

            # Skip partition info header
            if col_name.startswith("# Partition") or col_name.startswith("# "):
                in_partition_section = True
                continue

            if in_partition_section:
                # Partition columns are still valid columns
                pass

            data_type = str(vals[1]).strip() if len(vals) > 1 and vals[1] else "string"
            col_comment = str(vals[2]).strip() if len(vals) > 2 and vals[2] else None

            if col_comment == "None" or col_comment == "":
                col_comment = None

            columns.append(
                ColumnInfo(
                    name=col_name,
                    data_type=data_type,
                    logical_type=_map_hive_type(data_type),
                    nullable=True,  # Hive columns are nullable by default
                    is_primary_key=False,  # Hive has no PK concept
                    comment=col_comment,
                    ordinal_position=position,
                    extra={"partition_column": in_partition_section},
                )
            )
            position += 1

        return columns

    async def get_indexes(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> list[IndexInfo]:
        # Hive 2 had limited index support; Hive 3 removed it entirely.
        # Return empty list as Hive does not use traditional indexes.
        return []

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
                message=f"Hive query failed: {exc}",
                sql=sql,
            ) from exc

    async def execute_ddl(self, sql: str) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            raise QueryExecutionError(
                message=f"Hive DDL execution failed: {exc}",
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
        db = self._default_database(database) or "default"
        try:
            rows = await self.execute_query(
                f"SHOW CREATE TABLE `{db}`.`{table_name}`"
            )
        except Exception:
            rows = await self.execute_query(f"SHOW CREATE TABLE `{table_name}`")

        if rows:
            # SHOW CREATE TABLE returns a single column with the DDL
            ddl_parts = [next(iter(row.values())) for row in rows if row]
            return "\n".join(ddl_parts)
        raise QueryExecutionError(
            message=f"Could not retrieve CREATE TABLE for '{db}.{table_name}'.",
            sql=f"SHOW CREATE TABLE `{db}`.`{table_name}`",
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
        db = self._default_database(database) or "default"
        row_count = 0
        size_bytes = None

        try:
            # Hive stores stats in table properties; use DESCRIBE FORMATTED
            rows = await self.execute_query(
                f"DESCRIBE FORMATTED `{db}`.`{table_name}`"
            )
            for row in rows:
                vals = list(row.values())
                if len(vals) >= 2:
                    key = str(vals[0]).strip().lower() if vals[0] else ""
                    value = str(vals[1]).strip() if vals[1] else ""
                    if key == "numrows" or key == "numfiles":
                        with contextlib.suppress(ValueError):
                            row_count = int(value)
                    if key == "totalsize":
                        with contextlib.suppress(ValueError):
                            size_bytes = int(value)
        except Exception:
            pass

        return TableStats(
            table_name=table_name,
            row_count=row_count,
            size_bytes=size_bytes,
        )

    async def get_server_version(self) -> str:
        try:
            rows = await self.execute_query("SET hive.version")
            if rows:
                return str(next(iter(rows[0].values())))
            return "unknown"
        except Exception:
            return "unknown"
