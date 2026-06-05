# -*- coding: utf-8 -*-
"""
Oracle adapter for DataForge AI.

Implements :class:`AbstractBaseAdapter` for Oracle 12c+ using the
``oracledb`` async driver (thin mode).  Metadata queries use the
``ALL_*`` / ``DBA_*`` data dictionary views.

Oracle is prevalent in large enterprises with complex OLTP systems that
feed into the data warehouse via CDC or batch ETL.
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

_ORACLE_TYPE_MAP: Dict[str, ColumnDataType] = {
    "number": ColumnDataType.DECIMAL,
    "integer": ColumnDataType.INTEGER,
    "float": ColumnDataType.FLOAT,
    "binary_float": ColumnDataType.FLOAT,
    "binary_double": ColumnDataType.DOUBLE,
    "char": ColumnDataType.STRING,
    "nchar": ColumnDataType.STRING,
    "varchar2": ColumnDataType.STRING,
    "nvarchar2": ColumnDataType.STRING,
    "clob": ColumnDataType.TEXT,
    "nclob": ColumnDataType.TEXT,
    "long": ColumnDataType.TEXT,
    "raw": ColumnDataType.BINARY,
    "long raw": ColumnDataType.BINARY,
    "blob": ColumnDataType.BINARY,
    "bfile": ColumnDataType.BINARY,
    "date": ColumnDataType.DATE,
    "timestamp": ColumnDataType.TIMESTAMP,
    "timestamp with time zone": ColumnDataType.TIMESTAMP,
    "timestamp with local time zone": ColumnDataType.TIMESTAMP,
    "interval year to month": ColumnDataType.STRING,
    "interval day to second": ColumnDataType.STRING,
    "xmltype": ColumnDataType.TEXT,
    "json": ColumnDataType.JSON,
    "rowid": ColumnDataType.STRING,
    "urowid": ColumnDataType.STRING,
}


def _map_oracle_type(native_type: str) -> ColumnDataType:
    base = native_type.lower().split("(")[0].strip()
    return _ORACLE_TYPE_MAP.get(base, ColumnDataType.STRING)


class OracleAdapter(AbstractBaseAdapter):
    """Adapter for Oracle Database 12c+."""

    db_type = "oracle"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        try:
            async with self.engine.connect() as conn:
                # Set session-level NLS parameters for consistent formatting
                await conn.execute(text("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD'"))
                await conn.execute(
                    text("ALTER SESSION SET NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS.FF6'")
                )
                await conn.execute(text("ALTER SESSION SET NLS_NUMERIC_CHARACTERS = '.,'"))
                await conn.commit()
            logger.info("Oracle session configured for '%s'.", self.config.host)
        except Exception as exc:
            raise ConnectionError(
                message=f"Oracle session setup failed: {exc}",
                details={"host": self.config.host},
            ) from exc

    async def disconnect(self) -> None:
        logger.debug("Oracle adapter disconnect (no-op).")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT banner FROM v$version WHERE ROWNUM = 1")
                )
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
            # v$version may require DBA privileges; fall back
            try:
                async with self.engine.connect() as conn:
                    result = await conn.execute(
                        text("SELECT SYS_CONTEXT('USERENV', 'DB_NAME') AS db FROM DUAL")
                    )
                    result.fetchone()
                latency = (time.perf_counter() - start) * 1000
                return ConnectionTestResult(
                    success=True,
                    message="Connection successful.",
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

    async def get_databases(self) -> List[str]:
        # Oracle uses a single database per instance; return the DB name
        rows = await self.execute_query(
            "SELECT SYS_CONTEXT('USERENV', 'DB_NAME') AS db_name FROM DUAL"
        )
        if rows:
            return [rows[0].get("db_name", "")]
        return []

    async def get_schemas(self, database: Optional[str] = None) -> List[str]:
        rows = await self.execute_query(
            """
            SELECT username
            FROM all_users
            ORDER BY username
            """
        )
        return [row["username"] for row in rows]

    # ------------------------------------------------------------------ #
    # Metadata — tables
    # ------------------------------------------------------------------ #

    async def get_tables(
        self,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        table_type: Optional[str] = None,
    ) -> List[str]:
        owner = (self._default_schema(schema) or self.config.username or "").upper()
        sql = """
            SELECT table_name
            FROM all_tables
            WHERE owner = :owner
        """
        params: Dict[str, Any] = {"owner": owner}
        sql += " ORDER BY table_name"
        rows = await self.execute_query(sql, params)
        tables = [row["table_name"] for row in rows]

        if not table_type or table_type == "VIEW":
            view_sql = "SELECT view_name AS table_name FROM all_views WHERE owner = :owner ORDER BY view_name"
            view_rows = await self.execute_query(view_sql, {"owner": owner})
            tables.extend(row["table_name"] for row in view_rows)

        return sorted(tables)

    async def get_table_schema(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> TableSchema:
        owner = (self._default_schema(schema) or self.config.username or "").upper()
        db = self._default_database(database)
        columns = await self.get_columns(table_name, schema=owner)
        indexes = await self.get_indexes(table_name, schema=owner)

        # Table-level metadata
        sql = """
            SELECT
                t.table_name,
                c.comments AS table_comment,
                t.num_rows,
                t.blocks,
                t.avg_row_len,
                t.last_analyzed
            FROM all_tables t
            LEFT JOIN all_tab_comments c
                ON t.owner = c.owner AND t.table_name = c.table_name
            WHERE t.owner = :owner AND t.table_name = :tbl
        """
        rows = await self.execute_query(
            sql, {"owner": owner, "tbl": table_name.upper()}
        )
        info = rows[0] if rows else {}

        # Estimate size: blocks * db_block_size (default 8192)
        blocks = info.get("blocks") or 0
        estimated_size = blocks * 8192 if blocks else None

        return TableSchema(
            connection_id=self.config.connection_id,
            database_name=db,
            schema_name=owner,
            table_name=table_name,
            table_type="TABLE",
            comment=info.get("table_comment"),
            columns=columns,
            indexes=indexes,
            row_count_estimate=info.get("num_rows"),
            size_bytes=estimated_size,
            updated_at=info.get("last_analyzed"),
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
        owner = (self._default_schema(schema) or self.config.username or "").upper()
        sql = """
            SELECT
                tc.column_name,
                tc.data_type,
                tc.data_length,
                tc.data_precision,
                tc.data_scale,
                tc.nullable,
                tc.data_default,
                tc.column_id,
                cc.comments AS column_comment,
                CASE WHEN pk.column_name IS NOT NULL THEN 'Y' ELSE 'N' END AS is_pk
            FROM all_tab_columns tc
            LEFT JOIN all_col_comments cc
                ON tc.owner = cc.owner AND tc.table_name = cc.table_name AND tc.column_name = cc.column_name
            LEFT JOIN (
                SELECT acc.owner, acc.table_name, acc.column_name
                FROM all_cons_columns acc
                JOIN all_constraints ac
                    ON acc.owner = ac.owner
                    AND acc.constraint_name = ac.constraint_name
                    AND acc.table_name = ac.table_name
                WHERE ac.constraint_type = 'P'
            ) pk ON pk.owner = tc.owner AND pk.table_name = tc.table_name AND pk.column_name = tc.column_name
            WHERE tc.owner = :owner AND tc.table_name = :tbl
            ORDER BY tc.column_id
        """
        rows = await self.execute_query(
            sql, {"owner": owner, "tbl": table_name.upper()}
        )
        columns: List[ColumnInfo] = []
        for row in rows:
            data_type = row["data_type"]
            # Build display type
            display_type = data_type
            if data_type in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW"):
                display_type = f"{data_type}({row.get('data_length')})"
            elif data_type == "NUMBER" and row.get("data_precision") is not None:
                display_type = f"NUMBER({row['data_precision']},{row.get('data_scale', 0)})"

            default_val = row.get("data_default")
            if default_val:
                default_val = str(default_val).strip()

            columns.append(
                ColumnInfo(
                    name=row["column_name"],
                    data_type=display_type,
                    logical_type=_map_oracle_type(data_type),
                    nullable=row["nullable"] == "Y",
                    is_primary_key=row["is_pk"] == "Y",
                    default_value=default_val or None,
                    comment=row.get("column_comment"),
                    ordinal_position=(row["column_id"] or 1) - 1,
                    character_max_length=row.get("data_length") if data_type in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR") else None,
                    numeric_precision=row.get("data_precision"),
                    numeric_scale=row.get("data_scale"),
                )
            )
        return columns

    async def get_indexes(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> List[IndexInfo]:
        owner = (self._default_schema(schema) or self.config.username or "").upper()
        sql = """
            SELECT
                i.index_name,
                i.uniqueness,
                i.index_type,
                LISTAGG(ic.column_name, ',') WITHIN GROUP (ORDER BY ic.column_position) AS columns,
                CASE WHEN c.constraint_type = 'P' THEN 'Y' ELSE 'N' END AS is_pk
            FROM all_indexes i
            JOIN all_ind_columns ic
                ON i.owner = ic.index_owner AND i.index_name = ic.index_name
            LEFT JOIN all_constraints c
                ON i.owner = c.owner AND i.index_name = c.constraint_name AND c.constraint_type = 'P'
            WHERE i.table_owner = :owner AND i.table_name = :tbl
            GROUP BY i.index_name, i.uniqueness, i.index_type, c.constraint_type
            ORDER BY i.index_name
        """
        rows = await self.execute_query(
            sql, {"owner": owner, "tbl": table_name.upper()}
        )
        indexes: List[IndexInfo] = []
        for row in rows:
            if row["is_pk"] == "Y":
                idx_type = IndexType.PRIMARY
            elif row["uniqueness"] == "UNIQUE":
                idx_type = IndexType.UNIQUE
            elif row.get("index_type") == "BITMAP":
                idx_type = IndexType.NORMAL
            else:
                idx_type = IndexType.NORMAL

            col_str = row.get("columns", "")
            cols = [c.strip() for c in col_str.split(",")] if col_str else []

            indexes.append(
                IndexInfo(
                    name=row["index_name"],
                    index_type=idx_type,
                    columns=cols,
                    is_unique=row["uniqueness"] == "UNIQUE" or row["is_pk"] == "Y",
                    comment=row.get("index_type"),
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
                message=f"Oracle query failed: {exc}",
                sql=sql,
            ) from exc

    async def execute_ddl(self, sql: str) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            raise QueryExecutionError(
                message=f"Oracle DDL execution failed: {exc}",
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
        """
        Oracle does not have a native SHOW CREATE TABLE.  We use
        DBMS_METADATA.GET_DDL when available, falling back to manual
        reconstruction.
        """
        owner = (self._default_schema(schema) or self.config.username or "").upper()

        # Try DBMS_METADATA first (requires the package to be installed)
        try:
            sql = """
                SELECT DBMS_METADATA.GET_DDL('TABLE', :tbl, :owner) AS ddl
                FROM DUAL
            """
            rows = await self.execute_query(
                sql, {"tbl": table_name.upper(), "owner": owner}
            )
            if rows and rows[0].get("ddl"):
                ddl_text = rows[0]["ddl"]
                # DBMS_METADATA returns a CLOB; convert to string
                if hasattr(ddl_text, "read"):
                    ddl_text = ddl_text.read()
                return str(ddl_text)
        except Exception:
            pass

        # Fallback: reconstruct from metadata
        columns = await self.get_columns(table_name, schema=owner)
        indexes = await self.get_indexes(table_name, schema=owner)

        lines: List[str] = []
        for col in columns:
            parts = [f'    "{col.name}" {col.data_type}']
            if not col.nullable:
                parts.append("NOT NULL")
            if col.default_value:
                parts.append(f"DEFAULT {col.default_value}")
            lines.append(" ".join(parts))

        pk_cols = [c.name for c in columns if c.is_primary_key]
        if pk_cols:
            pk_list = ", ".join(f'"{c}"' for c in pk_cols)
            lines.append(f'    CONSTRAINT "PK_{table_name}" PRIMARY KEY ({pk_list})')

        col_defs = ",\n".join(lines)
        ddl = f'CREATE TABLE "{owner}"."{table_name}" (\n{col_defs}\n);'

        for idx in indexes:
            if idx.index_type == IndexType.PRIMARY:
                continue
            unique = "UNIQUE " if idx.is_unique else ""
            idx_cols = ", ".join(f'"{c}"' for c in idx.columns)
            ddl += f'\nCREATE {unique}INDEX "{idx.name}" ON "{owner}"."{table_name}" ({idx_cols});'

        return ddl

    async def get_table_ddl(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> str:
        return await self.get_create_table_sql(table_name, database=database, schema=schema)

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #

    async def get_table_stats(
        self,
        table_name: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> TableStats:
        owner = (self._default_schema(schema) or self.config.username or "").upper()
        sql = """
            SELECT
                num_rows,
                blocks,
                avg_row_len,
                last_analyzed
            FROM all_tables
            WHERE owner = :owner AND table_name = :tbl
        """
        rows = await self.execute_query(
            sql, {"owner": owner, "tbl": table_name.upper()}
        )
        if not rows:
            return TableStats(table_name=table_name)
        info = rows[0]
        blocks = info.get("blocks") or 0
        size_bytes = blocks * 8192 if blocks else None
        avg_row = info.get("avg_row_len")
        return TableStats(
            table_name=table_name,
            row_count=info.get("num_rows", 0),
            size_bytes=size_bytes,
            avg_row_size_bytes=float(avg_row) if avg_row else None,
            last_analyzed=info.get("last_analyzed"),
        )

    async def get_server_version(self) -> str:
        try:
            rows = await self.execute_query(
                "SELECT banner FROM v$version WHERE ROWNUM = 1"
            )
            return rows[0].get("banner", "unknown") if rows else "unknown"
        except Exception:
            return "unknown"
