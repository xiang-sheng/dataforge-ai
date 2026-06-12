"""
PostgreSQL adapter for DataForge AI.

Implements :class:`AbstractBaseAdapter` for PostgreSQL 12+ using the
``asyncpg`` async driver.  Metadata is extracted from
``information_schema`` and ``pg_catalog`` system tables.
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

_PG_TYPE_MAP: dict[str, ColumnDataType] = {
    "smallint": ColumnDataType.INTEGER,
    "integer": ColumnDataType.INTEGER,
    "int": ColumnDataType.INTEGER,
    "int2": ColumnDataType.INTEGER,
    "int4": ColumnDataType.INTEGER,
    "bigint": ColumnDataType.BIGINT,
    "int8": ColumnDataType.BIGINT,
    "real": ColumnDataType.FLOAT,
    "float4": ColumnDataType.FLOAT,
    "double precision": ColumnDataType.DOUBLE,
    "float8": ColumnDataType.DOUBLE,
    "numeric": ColumnDataType.DECIMAL,
    "decimal": ColumnDataType.DECIMAL,
    "money": ColumnDataType.DECIMAL,
    "character varying": ColumnDataType.STRING,
    "varchar": ColumnDataType.STRING,
    "character": ColumnDataType.STRING,
    "char": ColumnDataType.STRING,
    "text": ColumnDataType.TEXT,
    "citext": ColumnDataType.TEXT,
    "boolean": ColumnDataType.BOOLEAN,
    "bool": ColumnDataType.BOOLEAN,
    "date": ColumnDataType.DATE,
    "timestamp without time zone": ColumnDataType.TIMESTAMP,
    "timestamp with time zone": ColumnDataType.TIMESTAMP,
    "timestamptz": ColumnDataType.TIMESTAMP,
    "time without time zone": ColumnDataType.STRING,
    "time with time zone": ColumnDataType.STRING,
    "bytea": ColumnDataType.BINARY,
    "json": ColumnDataType.JSON,
    "jsonb": ColumnDataType.JSON,
    "uuid": ColumnDataType.STRING,
    "xml": ColumnDataType.TEXT,
    "inet": ColumnDataType.STRING,
    "cidr": ColumnDataType.STRING,
    "macaddr": ColumnDataType.STRING,
    "interval": ColumnDataType.STRING,
    "array": ColumnDataType.ARRAY,
}


def _map_pg_type(native_type: str) -> ColumnDataType:
    base = native_type.lower().strip()
    # Handle array types like "integer[]"
    if base.endswith("[]"):
        return ColumnDataType.ARRAY
    # Handle parameterised types like "numeric(10,2)"
    base_no_params = base.split("(")[0].strip()
    return _PG_TYPE_MAP.get(base_no_params, ColumnDataType.STRING)


class PostgreSQLAdapter(AbstractBaseAdapter):
    """Adapter for PostgreSQL 12+ databases."""

    db_type = "postgresql"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        try:
            async with self.engine.connect() as conn:
                # Ensure UTC timestamps
                await conn.execute(text("SET TIME ZONE 'UTC'"))
                # Set search_path if a schema is configured
                if self.config.schema_name:
                    await conn.execute(
                        text(f"SET search_path TO {self.config.schema_name}, public")
                    )
                await conn.commit()
            logger.info("PostgreSQL session configured for '%s'.", self.config.host)
        except Exception as exc:
            raise ConnectionError(
                message=f"PostgreSQL session setup failed: {exc}",
                details={"host": self.config.host},
            ) from exc

    async def disconnect(self) -> None:
        logger.debug("PostgreSQL adapter disconnect (no-op).")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text("SHOW server_version"))
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

    async def get_databases(self) -> list[str]:
        rows = await self.execute_query(
            "SELECT datname FROM pg_catalog.pg_database WHERE datistemplate = false ORDER BY datname"
        )
        return [row["datname"] for row in rows]

    async def get_schemas(self, database: str | None = None) -> list[str]:
        rows = await self.execute_query(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY schema_name
            """
        )
        return [row["schema_name"] for row in rows]

    # ------------------------------------------------------------------ #
    # Metadata — tables
    # ------------------------------------------------------------------ #

    async def get_tables(
        self,
        database: str | None = None,
        schema: str | None = None,
        table_type: str | None = None,
    ) -> list[str]:
        sch = self._default_schema(schema) or "public"
        sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = :schema
        """
        params: dict[str, Any] = {"schema": sch}
        if table_type:
            sql += " AND table_type = :ttype"
            params["ttype"] = table_type
        else:
            sql += " AND table_type IN ('BASE TABLE', 'VIEW')"
        sql += " ORDER BY table_name"
        rows = await self.execute_query(sql, params)
        return [row["table_name"] for row in rows]

    async def get_table_schema(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> TableSchema:
        sch = self._default_schema(schema) or "public"
        db = self._default_database(database)
        columns = await self.get_columns(table_name, schema=sch)
        indexes = await self.get_indexes(table_name, schema=sch)

        # Table-level comment & estimated row count
        sql = """
            SELECT
                c.relkind,
                obj_description(c.oid) AS table_comment,
                c.reltuples::bigint AS estimated_rows,
                pg_total_relation_size(c.oid) AS total_size
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = :tbl AND n.nspname = :schema
        """
        rows = await self.execute_query(sql, {"tbl": table_name, "schema": sch})
        info = rows[0] if rows else {}

        table_type_map = {"r": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "p": "TABLE"}
        relkind = info.get("relkind", "r")

        return TableSchema(
            connection_id=self.config.connection_id,
            database_name=db,
            schema_name=sch,
            table_name=table_name,
            table_type=table_type_map.get(relkind, "TABLE"),
            comment=info.get("table_comment"),
            columns=columns,
            indexes=indexes,
            row_count_estimate=max(info.get("estimated_rows", 0), 0),
            size_bytes=info.get("total_size"),
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
        sch = self._default_schema(schema) or "public"
        sql = """
            SELECT
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.column_default,
                c.ordinal_position,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale,
                pgd.description AS column_comment,
                CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_primary_key
            FROM information_schema.columns c
            LEFT JOIN pg_catalog.pg_statio_all_tables st
                ON st.schemaname = c.table_schema AND st.relname = c.table_name
            LEFT JOIN pg_catalog.pg_description pgd
                ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
            LEFT JOIN (
                SELECT kcu.column_name, kcu.table_schema, kcu.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
            ) pk
                ON pk.column_name = c.column_name
                AND pk.table_schema = c.table_schema
                AND pk.table_name = c.table_name
            WHERE c.table_schema = :schema AND c.table_name = :tbl
            ORDER BY c.ordinal_position
        """
        rows = await self.execute_query(sql, {"schema": sch, "tbl": table_name})
        columns: list[ColumnInfo] = []
        for row in rows:
            native = row.get("data_type", "")
            udt = row.get("udt_name", "")
            columns.append(
                ColumnInfo(
                    name=row["column_name"],
                    data_type=native if native else udt,
                    logical_type=_map_pg_type(native if native else udt),
                    nullable=row["is_nullable"] == "YES",
                    is_primary_key=bool(row.get("is_primary_key")),
                    default_value=row.get("column_default"),
                    comment=row.get("column_comment"),
                    ordinal_position=row["ordinal_position"] - 1,
                    character_max_length=row.get("character_maximum_length"),
                    numeric_precision=row.get("numeric_precision"),
                    numeric_scale=row.get("numeric_scale"),
                )
            )
        return columns

    async def get_indexes(
        self,
        table_name: str,
        database: str | None = None,
        schema: str | None = None,
    ) -> list[IndexInfo]:
        sch = self._default_schema(schema) or "public"
        sql = """
            SELECT
                i.relname AS index_name,
                ix.indisunique AS is_unique,
                ix.indisprimary AS is_primary,
                array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS columns
            FROM pg_catalog.pg_index ix
            JOIN pg_catalog.pg_class t ON t.oid = ix.indrelid
            JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
            WHERE t.relname = :tbl AND n.nspname = :schema
            GROUP BY i.relname, ix.indisunique, ix.indisprimary
            ORDER BY i.relname
        """
        rows = await self.execute_query(sql, {"tbl": table_name, "schema": sch})
        indexes: list[IndexInfo] = []
        for row in rows:
            if row["is_primary"]:
                idx_type = IndexType.PRIMARY
            elif row["is_unique"]:
                idx_type = IndexType.UNIQUE
            else:
                idx_type = IndexType.NORMAL
            # array_agg returns a Python list via asyncpg
            cols = row.get("columns", [])
            if isinstance(cols, str):
                cols = [c.strip().strip('"') for c in cols.strip("{}").split(",")]
            indexes.append(
                IndexInfo(
                    name=row["index_name"],
                    index_type=idx_type,
                    columns=cols,
                    is_unique=bool(row["is_unique"]),
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
                message=f"PostgreSQL query failed: {exc}",
                sql=sql,
            ) from exc

    async def execute_ddl(self, sql: str) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            raise QueryExecutionError(
                message=f"PostgreSQL DDL execution failed: {exc}",
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
        PostgreSQL does not have ``SHOW CREATE TABLE``.
        We reconstruct the DDL from catalog metadata.
        """
        sch = self._default_schema(schema) or "public"
        columns = await self.get_columns(table_name, schema=sch)
        indexes = await self.get_indexes(table_name, schema=sch)

        lines: list[str] = []
        for col in columns:
            parts = [f'    "{col.name}" {col.data_type}']
            if not col.nullable:
                parts.append("NOT NULL")
            if col.default_value is not None:
                parts.append(f"DEFAULT {col.default_value}")
            lines.append(" ".join(parts))

        # Primary key constraint
        pk_cols = [c.name for c in columns if c.is_primary_key]
        if pk_cols:
            pk_list = ", ".join(f'"{c}"' for c in pk_cols)
            lines.append(f"    PRIMARY KEY ({pk_list})")

        col_defs = ",\n".join(lines)
        ddl = f'CREATE TABLE "{sch}"."{table_name}" (\n{col_defs}\n);'

        # Append index DDL
        for idx in indexes:
            if idx.index_type == IndexType.PRIMARY:
                continue
            unique = "UNIQUE " if idx.is_unique else ""
            idx_cols = ", ".join(f'"{c}"' for c in idx.columns)
            ddl += f'\nCREATE {unique}INDEX "{idx.name}" ON "{sch}"."{table_name}" ({idx_cols});'

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
        sch = self._default_schema(schema) or "public"
        sql = """
            SELECT
                c.reltuples::bigint AS row_count,
                pg_total_relation_size(c.oid) AS total_size,
                pg_relation_size(c.oid) AS table_size,
                CASE WHEN c.reltuples > 0
                     THEN pg_relation_size(c.oid)::float / c.reltuples
                     ELSE NULL
                END AS avg_row_size
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = :tbl AND n.nspname = :schema
        """
        rows = await self.execute_query(sql, {"tbl": table_name, "schema": sch})
        if not rows:
            return TableStats(table_name=table_name)
        info = rows[0]
        return TableStats(
            table_name=table_name,
            row_count=max(info.get("row_count", 0), 0),
            size_bytes=info.get("total_size"),
            avg_row_size_bytes=info.get("avg_row_size"),
        )

    # ------------------------------------------------------------------ #
    # Version override
    # ------------------------------------------------------------------ #

    async def get_server_version(self) -> str:
        try:
            rows = await self.execute_query("SHOW server_version")
            return rows[0].get("server_version", "unknown") if rows else "unknown"
        except Exception:
            return "unknown"
