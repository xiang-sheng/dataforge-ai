# -*- coding: utf-8 -*-
"""
DuckDB-based local sandbox for lightweight DDL and SQL verification.

This module provides an embedded DuckDB environment where users can:

1. Execute generated DDL to verify syntax correctness.
2. Insert sample/mock data to test computation SQL.
3. Run computation SQL and verify results.
4. Simulate data-warehouse layer transitions (ODS -> DWD -> DWS -> ADS).
5. Validate data types, constraints, and transformations locally before
   deploying to production.

DuckDB is an embedded OLAP database (think "SQLite for analytics").  The
sandbox creates an in-memory (or file-backed) DuckDB instance so that the
full verification cycle can run without any external database server.

Typical usage::

    with DuckDBSandbox() as sb:
        sb.verify_ddl("CREATE TABLE ods_orders (id INT, amount DECIMAL(10,2))")
        sb.insert_sample_data("ods_orders", num_rows=500)
        result = sb.execute_and_preview("SELECT * FROM ods_orders LIMIT 5")
        print(result.rows)
"""

from __future__ import annotations

import csv
import logging
import random
import re
import string
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ====================================================================== #
# Result data models (Pydantic)
# ====================================================================== #


class DDLVerifyResult(BaseModel):
    """Result of verifying a single DDL statement inside the sandbox."""

    success: bool
    ddl: str
    normalized_ddl: Optional[str] = Field(
        default=None,
        description="DDL as normalised / accepted by DuckDB.",
    )
    error: Optional[str] = None
    table_name: Optional[str] = None
    columns_created: List[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0


class SQLVerifyResult(BaseModel):
    """Result of verifying a computation SQL statement."""

    success: bool
    sql: str
    rows_affected: int = 0
    result_columns: List[str] = Field(default_factory=list)
    sample_rows: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    explain_plan: Optional[str] = None


class QueryResult(BaseModel):
    """Lightweight wrapper around a SELECT result set."""

    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    execution_time_ms: float = 0.0


class BatchDDLResult(BaseModel):
    """Aggregated result of executing a batch of DDL statements."""

    total: int
    succeeded: int
    failed: int
    results: List[DDLVerifyResult]


class PipelineStep(BaseModel):
    """
    A single step inside an ETL pipeline verification run.

    ``step_type`` must be one of:
      - ``"ddl"``: execute a DDL statement (``sql`` required).
      - ``"insert_sample"``: auto-generate rows into ``table_name``.
      - ``"computation_sql"``: execute a DML / computation statement.
      - ``"verify"``: assert row-count / column expectations on ``table_name``.
    """

    step_type: str
    sql: Optional[str] = None
    table_name: Optional[str] = None
    num_sample_rows: int = 100
    expected_row_count: Optional[int] = None
    expected_columns: Optional[List[str]] = None


class PipelineVerifyResult(BaseModel):
    """Aggregated result of an end-to-end pipeline verification."""

    success: bool
    steps_total: int
    steps_passed: int
    step_results: List[Dict[str, Any]]
    errors: List[str]
    total_execution_time_ms: float = 0.0


class TableInfo(BaseModel):
    """Introspection metadata for a single table inside the sandbox."""

    table_name: str
    columns: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Each dict contains: name, type, nullable, default.",
    )
    row_count: int = 0
    estimated_size_bytes: int = 0


# ====================================================================== #
# Internal helper: SQL dialect translator
# ====================================================================== #


class _DialectTranslator:
    """
    Translate common DDL fragments from ClickHouse / Hive / MySQL syntax
    into DuckDB-compatible syntax.

    This is a *best-effort* translator — it handles the most common patterns
    encountered in data-warehouse DDL generation.  It is intentionally
    conservative: when it cannot confidently translate a clause it leaves
    the original text intact and lets DuckDB report the error.
    """

    # ------------------------------------------------------------------ #
    # Type mapping tables
    # ------------------------------------------------------------------ #

    _CLICKHOUSE_TYPE_MAP: Dict[str, str] = {
        "uint8": "SMALLINT",
        "uint16": "INTEGER",
        "uint32": "INTEGER",
        "uint64": "BIGINT",
        "uint128": "HUGEINT",
        "uint256": "HUGEINT",
        "int8": "TINYINT",
        "int16": "SMALLINT",
        "int32": "INTEGER",
        "int64": "BIGINT",
        "int128": "HUGEINT",
        "int256": "HUGEINT",
        "float32": "REAL",
        "float64": "DOUBLE",
        "fixedstring": "VARCHAR",
        "lowcardinality": "VARCHAR",
        "enum8": "VARCHAR",
        "enum16": "VARCHAR",
        "datetime64": "TIMESTAMP",
        "datetime": "TIMESTAMP",
        "date32": "DATE",
        "uuid": "VARCHAR",
        "nothing": "VARCHAR",
    }

    _HIVE_TYPE_MAP: Dict[str, str] = {
        "string": "VARCHAR",
        "tinyint": "TINYINT",
        "smallint": "SMALLINT",
        "int": "INTEGER",
        "bigint": "BIGINT",
        "binary": "BLOB",
        "timestamp": "TIMESTAMP",
    }

    _MYSQL_TYPE_MAP: Dict[str, str] = {
        "datetime": "TIMESTAMP",
        "mediumtext": "VARCHAR",
        "longtext": "VARCHAR",
        "tinytext": "VARCHAR",
        "mediumblob": "BLOB",
        "longblob": "BLOB",
        "tinyblob": "BLOB",
        "tinyint": "TINYINT",
        "mediumint": "INTEGER",
        "int": "INTEGER",
        "double precision": "DOUBLE",
        "enum": "VARCHAR",
        "set": "VARCHAR",
        "json": "JSON",
    }

    # Regex that strips engine-specific trailing clauses.
    _STRIP_PATTERNS: List[re.Pattern[str]] = [
        # ClickHouse
        re.compile(
            r"\bENGINE\s*=\s*\w+(\([^)]*\))?"
            r"(\s+ORDER\s+BY\s+(?:\([^)]*\)|\w+))?"
            r"(\s+PARTITION\s+BY\s+(?:\([^)]*\)|\w+))?"
            r"(\s+PRIMARY\s+KEY\s+(?:\([^)]*\)|\w+))?"
            r"(\s+SAMPLE\s+BY\s+(?:\([^)]*\)|\w+))?"
            r"(\s+SETTINGS\s+[^;\n]*)?",
            re.IGNORECASE,
        ),
        # Hive
        re.compile(
            r"\bSTORED\s+AS\s+\w+",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bROW\s+FORMAT\s+DELIMITED[^;\n]*",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bLOCATION\s+'[^']*'",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bTBLPROPERTIES\s*\([^)]*\)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bPARTITIONED\s+BY\s*\([^)]*\)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bCLUSTERED\s+BY\s*\([^)]*\)(\s+INTO\s+\d+\s+BUCKETS)?",
            re.IGNORECASE,
        ),
        # MySQL
        re.compile(
            r"\bENGINE\s*=\s*\w+",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bDEFAULT\s+CHARSET\s*=\s*\w+",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bCOLLATE\s*=\s*\w+",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bAUTO_INCREMENT\s*=\s*\d+",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bCOMMENT\s*=\s*'[^']*'",
            re.IGNORECASE,
        ),
        # Generic
        re.compile(
            r"\bDISTRIBUTED\s+BY\s+\([^)]*\)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bWITH\s*\([^)]*\)\s*;?\s*$",
            re.IGNORECASE,
        ),
    ]

    # Patterns for wrapping Nullable(...) / LowCardinality(...)
    _NULLABLE_RE = re.compile(r"Nullable\(([^)]+)\)", re.IGNORECASE)
    _LOW_CARD_RE = re.compile(r"LowCardinality\(([^)]+)\)", re.IGNORECASE)

    @classmethod
    def translate(cls, ddl: str) -> str:
        """
        Return a DuckDB-compatible version of *ddl*.

        The input may be ClickHouse, Hive, or MySQL flavoured DDL.  The
        translator applies all known transformations; unknown constructs
        are passed through unchanged.
        """
        result = ddl.strip()

        # Unwrap ClickHouse wrappers first.
        result = cls._unwrap_clickhouse_wrappers(result)

        # Replace engine-specific type tokens.
        result = cls._replace_types(result)

        # Strip engine-specific clauses.
        result = cls._strip_engine_clauses(result)

        # Normalise semicolons (keep at most one trailing).
        result = result.rstrip().rstrip(";").rstrip() + ";"

        return result

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def _unwrap_clickhouse_wrappers(cls, ddl: str) -> str:
        """Remove ``Nullable(...)`` and ``LowCardinality(...)`` wrappers."""
        # LowCardinality -> inner type (we don't have LC in DuckDB)
        ddl = cls._LOW_CARD_RE.sub(r"\1", ddl)
        # Nullable -> keep inner type (DuckDB columns are nullable by default)
        ddl = cls._NULLABLE_RE.sub(r"\1", ddl)
        return ddl

    @classmethod
    def _replace_types(cls, ddl: str) -> str:
        """Replace known engine-specific type tokens with DuckDB equivalents."""
        # Build a combined map (case-insensitive matching via regex).
        combined: Dict[str, str] = {}
        combined.update(cls._CLICKHOUSE_TYPE_MAP)
        combined.update(cls._HIVE_TYPE_MAP)
        combined.update(cls._MYSQL_TYPE_MAP)

        for src_type, dst_type in combined.items():
            # Word-boundary replacement (case-insensitive).
            pattern = re.compile(rf"\b{re.escape(src_type)}\b", re.IGNORECASE)
            ddl = pattern.sub(dst_type, ddl)

        return ddl

    @classmethod
    def _strip_engine_clauses(cls, ddl: str) -> str:
        """Remove engine/storage-specific clauses from CREATE TABLE statements."""
        for pat in cls._STRIP_PATTERNS:
            ddl = pat.sub("", ddl)
        return ddl


# ====================================================================== #
# Internal helper: sample data generator
# ====================================================================== #


# A small word list for generating readable VARCHAR values.
_WORD_POOL: List[str] = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
    "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
    "oscar", "papa", "quebec", "romeo", "sierra", "tango", "ultra",
    "victor", "whiskey", "xray", "yankee", "zulu", "data", "lake",
    "warehouse", "pipeline", "metric", "dimension", "fact", "table",
    "order", "customer", "product", "region", "channel", "amount",
    "price", "quantity", "status", "active", "pending", "closed",
]

_FIRST_NAMES: List[str] = [
    "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Hank",
    "Ivy", "Jack", "Karen", "Leo", "Mona", "Nick", "Olivia", "Paul",
]

_LAST_NAMES: List[str] = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Chen", "Wang", "Li", "Zhang",
]


class _SampleDataGenerator:
    """
    Generate realistic sample data for a DuckDB table based on its schema.

    The generator inspects each column's declared type and produces
    appropriate random values.  A ``seed`` parameter ensures
    reproducibility across runs.

    For efficiency the generator builds a single ``INSERT INTO ... VALUES``
    statement with all rows rather than issuing one INSERT per row.
    """

    # Date range defaults for temporal columns.
    _DEFAULT_DATE_START = date(2020, 1, 1)
    _DEFAULT_DATE_END = date(2025, 12, 31)

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        table_name: str,
        num_rows: int = 100,
        seed: int = 42,
    ) -> None:
        self._conn = conn
        self._table_name = table_name
        self._num_rows = max(1, num_rows)
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def generate_and_insert(self) -> int:
        """
        Generate sample rows and insert them into the target table.

        Returns the number of rows actually inserted.
        """
        columns = self._introspect_columns()
        if not columns:
            logger.warning(
                "Table '%s' has no columns — skipping sample generation.",
                self._table_name,
            )
            return 0

        rows = self._generate_rows(columns)
        self._bulk_insert(columns, rows)
        return len(rows)

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def _introspect_columns(self) -> List[Dict[str, str]]:
        """Return ``[{name, type}, ...]`` for the target table."""
        try:
            result = self._conn.execute(
                f"DESCRIBE \"{self._table_name}\""
            ).fetchall()
        except Exception as exc:
            logger.error(
                "Failed to describe table '%s': %s", self._table_name, exc
            )
            return []

        columns: List[Dict[str, str]] = []
        for row in result:
            columns.append({"name": row[0], "type": row[1].upper()})
        return columns

    # ------------------------------------------------------------------ #
    # Row generation
    # ------------------------------------------------------------------ #

    def _generate_rows(
        self, columns: List[Dict[str, str]]
    ) -> List[Tuple[Any, ...]]:
        rows: List[Tuple[Any, ...]] = []
        for i in range(self._num_rows):
            row_values: List[Any] = []
            for col in columns:
                row_values.append(
                    self._value_for_type(col["type"], col["name"], i)
                )
            rows.append(tuple(row_values))
        return rows

    def _value_for_type(self, col_type: str, col_name: str, row_index: int) -> Any:
        """Produce a single random value appropriate for *col_type*."""
        upper = col_type.upper().strip()

        # ---- integer family ----
        if any(tok in upper for tok in ("INT", "SERIAL")):
            if "BIG" in upper or "HUGE" in upper:
                return self._rng.randint(1, 10_000_000_000)
            if "SMALL" in upper:
                return self._rng.randint(0, 32_000)
            if "TINY" in upper:
                return self._rng.randint(0, 127)
            # Heuristic: if column name ends with _id or is "id", generate
            # sequential-ish values; otherwise random.
            if col_name.lower() in ("id",) or col_name.lower().endswith("_id"):
                return row_index + 1
            return self._rng.randint(1, 1_000_000)

        # ---- boolean ----
        if upper == "BOOLEAN" or upper == "BOOL":
            return self._rng.choice([True, False])

        # ---- floating point ----
        if upper in ("REAL", "FLOAT"):
            return round(self._rng.uniform(0.0, 10_000.0), 4)
        if upper == "DOUBLE":
            return round(self._rng.uniform(0.0, 1_000_000.0), 6)

        # ---- decimal ----
        if upper.startswith("DECIMAL") or upper.startswith("NUMERIC"):
            precision, scale = self._parse_decimal_params(upper)
            max_int_part = 10 ** (precision - scale) - 1
            value = round(self._rng.uniform(0, max_int_part), scale)
            return value

        # ---- date ----
        if upper == "DATE":
            delta = (self._DEFAULT_DATE_END - self._DEFAULT_DATE_START).days
            random_day = self._rng.randint(0, max(delta, 1))
            return self._DEFAULT_DATE_START + timedelta(days=random_day)

        # ---- timestamp ----
        if "TIMESTAMP" in upper or "DATETIME" in upper:
            start_ts = datetime(2020, 1, 1)
            end_ts = datetime(2025, 12, 31, 23, 59, 59)
            delta_seconds = int((end_ts - start_ts).total_seconds())
            random_seconds = self._rng.randint(0, max(delta_seconds, 1))
            return start_ts + timedelta(seconds=random_seconds)

        # ---- time ----
        if upper == "TIME":
            total_seconds = self._rng.randint(0, 86_399)
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        # ---- blob / binary ----
        if upper in ("BLOB", "BYTEA", "BINARY", "VARBINARY"):
            length = self._rng.randint(4, 32)
            return self._rng.randbytes(length)

        # ---- JSON ----
        if upper == "JSON":
            return (
                f'{{"key": "{self._rng.choice(_WORD_POOL)}", '
                f'"value": {self._rng.randint(1, 1000)}}}'
            )

        # ---- VARCHAR / TEXT / everything else ----
        return self._generate_string(col_name)

    def _generate_string(self, col_name: str) -> str:
        """Generate a context-aware string value."""
        lower_name = col_name.lower()

        # Name-like columns
        if lower_name in ("first_name", "fname", "given_name"):
            return self._rng.choice(_FIRST_NAMES)
        if lower_name in ("last_name", "lname", "surname", "family_name"):
            return self._rng.choice(_LAST_NAMES)
        if lower_name in ("name", "full_name", "customer_name", "user_name"):
            return f"{self._rng.choice(_FIRST_NAMES)} {self._rng.choice(_LAST_NAMES)}"

        # Email-like columns
        if "email" in lower_name:
            first = self._rng.choice(_FIRST_NAMES).lower()
            last = self._rng.choice(_LAST_NAMES).lower()
            domain = self._rng.choice(["example.com", "test.org", "mail.dev"])
            return f"{first}.{last}@{domain}"

        # Status columns
        if "status" in lower_name:
            return self._rng.choice(["active", "pending", "closed", "cancelled"])

        # Code / type columns
        if "code" in lower_name or "type" in lower_name:
            return "".join(self._rng.choices(string.ascii_uppercase, k=3))

        # URL columns
        if "url" in lower_name or "link" in lower_name:
            slug = self._rng.choice(_WORD_POOL)
            return f"https://example.com/{slug}"

        # Generic: 1-3 random words
        num_words = self._rng.randint(1, 3)
        return " ".join(self._rng.choice(_WORD_POOL) for _ in range(num_words))

    # ------------------------------------------------------------------ #
    # Bulk insert
    # ------------------------------------------------------------------ #

    def _bulk_insert(
        self,
        columns: List[Dict[str, str]],
        rows: List[Tuple[Any, ...]],
    ) -> None:
        """Insert rows using DuckDB's ``executemany`` for efficiency."""
        col_names = ", ".join(f'"{c["name"]}"' for c in columns)
        placeholders = ", ".join(["?"] * len(columns))
        sql = (
            f'INSERT INTO "{self._table_name}" ({col_names}) '
            f"VALUES ({placeholders})"
        )
        try:
            self._conn.executemany(sql, rows)
            logger.debug(
                "Inserted %d sample rows into '%s'.",
                len(rows),
                self._table_name,
            )
        except Exception:
            # Fallback: insert row-by-row so that a single bad value
            # doesn't lose the entire batch.
            logger.warning(
                "Bulk insert into '%s' failed; falling back to row-by-row.",
                self._table_name,
            )
            inserted = 0
            for row in rows:
                try:
                    self._conn.execute(sql, row)
                    inserted += 1
                except Exception as row_exc:
                    logger.debug("Skipping row due to error: %s", row_exc)
            logger.info(
                "Row-by-row fallback inserted %d / %d rows into '%s'.",
                inserted,
                len(rows),
                self._table_name,
            )

    # ------------------------------------------------------------------ #
    # Utility
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_decimal_params(col_type: str) -> Tuple[int, int]:
        """Extract ``(precision, scale)`` from a ``DECIMAL(p,s)`` string."""
        match = re.search(r"\((\d+)\s*,\s*(\d+)\)", col_type)
        if match:
            return int(match.group(1)), int(match.group(2))
        # Default DECIMAL without parameters
        return (18, 2)


# ====================================================================== #
# Main class: DuckDBSandbox
# ====================================================================== #


class DuckDBSandbox:
    """
    Lightweight local verification sandbox using DuckDB.

    DuckDB is an embedded OLAP database (like SQLite for analytics).
    This sandbox creates an in-memory DuckDB instance to validate
    generated DDL and computation SQL without needing any external
    database server.

    Usage::

        sandbox = DuckDBSandbox()
        sandbox.open()
        try:
            sandbox.verify_ddl("CREATE TABLE t (id INT, name VARCHAR)")
            result = sandbox.execute_and_preview("SELECT * FROM t")
        finally:
            sandbox.close()

    Or as a context manager::

        with DuckDBSandbox() as sb:
            sb.verify_ddl("CREATE TABLE t (id INT)")
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        """
        Create a sandbox.

        Parameters
        ----------
        db_path:
            ``":memory:"`` (default) for a purely in-memory database, or
            a file path for a persistent sandbox that survives across
            sessions.
        """
        self._db_path = db_path
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._translator = _DialectTranslator()
        logger.debug("DuckDBSandbox initialised (path=%s).", db_path)

    def __repr__(self) -> str:
        state = "open" if self._conn is not None else "closed"
        return f"<DuckDBSandbox path={self._db_path!r} state={state}>"

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def open(self) -> None:
        """Open the DuckDB connection.  Idempotent — calling twice is safe."""
        if self._conn is not None:
            logger.debug("Sandbox already open; skipping.")
            return

        # Ensure parent directory exists for file-backed databases.
        if self._db_path != ":memory:":
            parent = Path(self._db_path).parent
            parent.mkdir(parents=True, exist_ok=True)

        self._conn = duckdb.connect(self._db_path)
        logger.info("DuckDB sandbox opened (path=%s).", self._db_path)

    def close(self) -> None:
        """Close the connection and release resources."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:
                logger.warning("Error closing DuckDB connection: %s", exc)
            finally:
                self._conn = None
            logger.info("DuckDB sandbox closed.")

    def reset(self) -> None:
        """
        Drop all user-created tables and reset the sandbox to a clean state.

        Views and temporary tables are also removed.
        """
        self._ensure_open()
        assert self._conn is not None

        tables = self.list_tables()
        for tbl in tables:
            try:
                self._conn.execute(f'DROP TABLE IF EXISTS "{tbl}" CASCADE')
            except Exception as exc:
                logger.warning("Failed to drop table '%s': %s", tbl, exc)

        # Also drop any views.
        try:
            views = self._conn.execute(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema = 'main'"
            ).fetchall()
            for (view_name,) in views:
                self._conn.execute(f'DROP VIEW IF EXISTS "{view_name}" CASCADE')
        except Exception:
            pass  # Views may not exist

        logger.info("DuckDB sandbox reset — all tables and views dropped.")

    # Context manager support ----------------------------------------- #

    def __enter__(self) -> "DuckDBSandbox":
        self.open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # DDL verification
    # ------------------------------------------------------------------ #

    def verify_ddl(self, ddl: str) -> DDLVerifyResult:
        """
        Execute a DDL statement and verify that it succeeds.

        The statement is first passed through the :class:`_DialectTranslator`
        so that ClickHouse / Hive / MySQL flavoured DDL is automatically
        adapted to DuckDB syntax.

        Returns a :class:`DDLVerifyResult` indicating success or failure,
        the normalised DDL, and any columns that were created.
        """
        self._ensure_open()
        assert self._conn is not None

        translated = self._translator.translate(ddl)
        start = time.perf_counter()

        try:
            self._conn.execute(translated)
            elapsed = (time.perf_counter() - start) * 1000

            # Try to extract table name and columns for CREATE TABLE.
            table_name = self._extract_table_name(translated)
            columns_created: List[str] = []
            if table_name and translated.upper().strip().startswith("CREATE"):
                try:
                    columns_created = [
                        c["name"]
                        for c in self._describe_raw(table_name)
                    ]
                except Exception:
                    pass

            logger.debug(
                "DDL verified OK (%.1f ms): %s",
                elapsed,
                ddl[:120],
            )
            return DDLVerifyResult(
                success=True,
                ddl=ddl,
                normalized_ddl=translated,
                table_name=table_name,
                columns_created=columns_created,
                execution_time_ms=round(elapsed, 2),
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            error_msg = str(exc)
            logger.debug(
                "DDL verification failed (%.1f ms): %s — %s",
                elapsed,
                ddl[:120],
                error_msg,
            )
            return DDLVerifyResult(
                success=False,
                ddl=ddl,
                normalized_ddl=translated,
                error=error_msg,
                table_name=self._extract_table_name(translated),
                execution_time_ms=round(elapsed, 2),
            )

    def verify_multiple_ddl(self, ddls: List[str]) -> List[DDLVerifyResult]:
        """
        Verify a list of DDL statements in order, returning one result per
        statement.

        Execution stops at the first failure so that dependent statements
        are not run against a broken schema.
        """
        results: List[DDLVerifyResult] = []
        for ddl in ddls:
            result = self.verify_ddl(ddl)
            results.append(result)
            if not result.success:
                logger.warning(
                    "Stopping DDL batch at statement %d due to error: %s",
                    len(results),
                    result.error,
                )
                break
        return results

    def batch_execute_ddl(self, ddls: List[str]) -> BatchDDLResult:
        """
        Execute a batch of DDL statements (e.g. a full warehouse-layer setup).

        Unlike :meth:`verify_multiple_ddl`, this method continues past
        failures so that the caller gets a complete picture of which
        statements succeeded and which did not.
        """
        results: List[DDLVerifyResult] = []
        for ddl in ddls:
            results.append(self.verify_ddl(ddl))

        succeeded = sum(1 for r in results if r.success)
        return BatchDDLResult(
            total=len(ddls),
            succeeded=succeeded,
            failed=len(ddls) - succeeded,
            results=results,
        )

    # ------------------------------------------------------------------ #
    # Computation SQL verification
    # ------------------------------------------------------------------ #

    def verify_computation_sql(
        self,
        sql: str,
        expected_columns: Optional[List[str]] = None,
    ) -> SQLVerifyResult:
        """
        Execute a computation SQL (e.g. ``INSERT INTO ... SELECT ...``)
        and verify:

        * SQL syntax is correct (no runtime error).
        * Result columns match *expected_columns* (when provided).
        * Rows affected count is reported.

        An ``EXPLAIN`` plan is captured automatically for diagnostic
        purposes.
        """
        self._ensure_open()
        assert self._conn is not None

        start = time.perf_counter()

        # Capture EXPLAIN plan (best-effort, SELECT only).
        explain_plan: Optional[str] = None
        upper_sql = sql.strip().upper()
        if upper_sql.startswith("SELECT") or upper_sql.startswith("WITH"):
            try:
                explain_rows = self._conn.execute(
                    f"EXPLAIN {sql}"
                ).fetchall()
                explain_plan = "\n".join(
                    f"{row[0]}: {row[1]}" if len(row) >= 2 else str(row)
                    for row in explain_rows
                )
            except Exception:
                pass  # EXPLAIN may fail for non-SELECT

        # Execute the statement.
        try:
            result = self._conn.execute(sql)
            elapsed = (time.perf_counter() - start) * 1000

            # Determine rows affected / result columns.
            result_columns: List[str] = []
            sample_rows: List[Dict[str, Any]] = []
            rows_affected = 0

            if result.description:
                result_columns = [desc[0] for desc in result.description]

                # Fetch a few sample rows for SELECT-like results.
                if upper_sql.startswith("SELECT") or upper_sql.startswith("WITH"):
                    fetched = result.fetchmany(10)
                    sample_rows = [
                        dict(zip(result_columns, row)) for row in fetched
                    ]
                    rows_affected = len(sample_rows)
                else:
                    rows_affected = result.fetchall().__len__() if hasattr(result, "fetchall") else 0
            else:
                rows_affected = 0

            # Column validation.
            error: Optional[str] = None
            if expected_columns is not None:
                expected_lower = {c.lower() for c in expected_columns}
                actual_lower = {c.lower() for c in result_columns}
                missing = expected_lower - actual_lower
                if missing:
                    error = (
                        f"Missing expected columns: {sorted(missing)}. "
                        f"Got: {sorted(actual_lower)}"
                    )

            success = error is None
            logger.debug(
                "Computation SQL %s (%.1f ms): %s",
                "OK" if success else "MISMATCH",
                elapsed,
                sql[:120],
            )
            return SQLVerifyResult(
                success=success,
                sql=sql,
                rows_affected=rows_affected,
                result_columns=result_columns,
                sample_rows=sample_rows,
                error=error,
                execution_time_ms=round(elapsed, 2),
                explain_plan=explain_plan,
            )

        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            error_msg = str(exc)
            logger.debug(
                "Computation SQL failed (%.1f ms): %s — %s",
                elapsed,
                sql[:120],
                error_msg,
            )
            return SQLVerifyResult(
                success=False,
                sql=sql,
                error=error_msg,
                execution_time_ms=round(elapsed, 2),
                explain_plan=explain_plan,
            )

    def execute_and_preview(
        self, sql: str, max_rows: int = 100
    ) -> QueryResult:
        """
        Execute a SELECT query and return the result set as a list of
        dictionaries.

        Parameters
        ----------
        sql:
            A ``SELECT`` (or ``WITH ... SELECT``) statement.
        max_rows:
            Maximum number of rows to return (default 100).
        """
        self._ensure_open()
        assert self._conn is not None

        start = time.perf_counter()
        try:
            result = self._conn.execute(sql)
            elapsed = (time.perf_counter() - start) * 1000

            columns: List[str] = []
            if result.description:
                columns = [desc[0] for desc in result.description]

            fetched = result.fetchmany(max_rows)
            rows = [dict(zip(columns, row)) for row in fetched]

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=round(elapsed, 2),
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error(
                "execute_and_preview failed (%.1f ms): %s", elapsed, exc
            )
            raise

    # ------------------------------------------------------------------ #
    # Sample data generation
    # ------------------------------------------------------------------ #

    def insert_sample_data(
        self,
        table_name: str,
        num_rows: int = 100,
        seed: int = 42,
    ) -> int:
        """
        Auto-generate and insert sample data into *table_name* based on
        its schema.

        Uses the :class:`_SampleDataGenerator` which introspects column
        types and produces realistic random values.

        Parameters
        ----------
        table_name:
            Target table (must already exist).
        num_rows:
            Number of rows to generate (default 100).
        seed:
            RNG seed for reproducibility (default 42).

        Returns
        -------
        int
            Number of rows actually inserted.
        """
        self._ensure_open()
        assert self._conn is not None

        generator = _SampleDataGenerator(
            conn=self._conn,
            table_name=table_name,
            num_rows=num_rows,
            seed=seed,
        )
        count = generator.generate_and_insert()
        logger.info(
            "Generated %d sample rows for table '%s' (seed=%d).",
            count,
            table_name,
            seed,
        )
        return count

    def insert_from_csv(self, table_name: str, csv_path: str) -> int:
        """
        Load data from a CSV file into *table_name*.

        The table must already exist.  The CSV header row is used to
        map columns by name.

        Returns the number of rows loaded.
        """
        self._ensure_open()
        assert self._conn is not None

        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        # Use DuckDB's native CSV reader for efficiency.
        try:
            self._conn.execute(
                f"COPY \"{table_name}\" FROM '{path.as_posix()}' "
                f"(HEADER TRUE, AUTO_DETECT FALSE)"
            )
            count = self.get_row_count(table_name)
            logger.info(
                "Loaded %d rows from CSV '%s' into '%s'.",
                count,
                csv_path,
                table_name,
            )
            return count
        except Exception as exc:
            logger.warning(
                "DuckDB COPY failed (%s); falling back to Python CSV reader.",
                exc,
            )

        # Fallback: read with Python csv and insert row-by-row.
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        return self.insert_from_dicts(table_name, rows)

    def insert_from_dicts(
        self, table_name: str, rows: List[Dict[str, Any]]
    ) -> int:
        """
        Insert data from a list of dictionaries.

        Each dict represents one row; keys are column names.

        Returns the number of rows inserted.
        """
        self._ensure_open()
        assert self._conn is not None

        if not rows:
            return 0

        columns = list(rows[0].keys())
        col_names = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join(["?"] * len(columns))
        sql = (
            f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
        )

        values = [tuple(row.get(c) for c in columns) for row in rows]
        self._conn.executemany(sql, values)
        logger.info(
            "Inserted %d dict-rows into '%s'.", len(values), table_name
        )
        return len(values)

    # ------------------------------------------------------------------ #
    # Full pipeline verification
    # ------------------------------------------------------------------ #

    def verify_pipeline(
        self, steps: List[PipelineStep]
    ) -> PipelineVerifyResult:
        """
        Verify a complete ETL pipeline end-to-end.

        Steps are executed sequentially.  Each step may be:

        ``"ddl"``
            Execute a DDL statement (``sql`` field required).
        ``"insert_sample"``
            Auto-generate sample data into ``table_name``.
        ``"computation_sql"``
            Execute a DML or computation statement.
        ``"verify"``
            Assert that ``table_name`` has the expected row count and/or
            columns.

        Returns a :class:`PipelineVerifyResult` summarising all step
        outcomes.
        """
        start_all = time.perf_counter()
        step_results: List[Dict[str, Any]] = []
        errors: List[str] = []
        passed = 0

        for idx, step in enumerate(steps):
            step_label = f"Step {idx + 1}/{len(steps)} [{step.step_type}]"
            logger.info("Pipeline %s starting.", step_label)

            try:
                result = self._execute_pipeline_step(step)
                step_results.append(
                    {
                        "step_index": idx,
                        "step_type": step.step_type,
                        "success": result.get("success", False),
                        "detail": result,
                    }
                )
                if result.get("success", False):
                    passed += 1
                else:
                    err = result.get("error", "Unknown failure")
                    errors.append(f"{step_label}: {err}")
            except Exception as exc:
                errors.append(f"{step_label}: {exc}")
                step_results.append(
                    {
                        "step_index": idx,
                        "step_type": step.step_type,
                        "success": False,
                        "detail": {"error": str(exc)},
                    }
                )

        total_elapsed = (time.perf_counter() - start_all) * 1000
        success = len(errors) == 0
        logger.info(
            "Pipeline verification %s — %d/%d steps passed in %.1f ms.",
            "PASSED" if success else "FAILED",
            passed,
            len(steps),
            total_elapsed,
        )
        return PipelineVerifyResult(
            success=success,
            steps_total=len(steps),
            steps_passed=passed,
            step_results=step_results,
            errors=errors,
            total_execution_time_ms=round(total_elapsed, 2),
        )

    def _execute_pipeline_step(
        self, step: PipelineStep
    ) -> Dict[str, Any]:
        """Dispatch a single pipeline step to the appropriate handler."""
        kind = step.step_type.lower()

        if kind == "ddl":
            if not step.sql:
                return {"success": False, "error": "DDL step requires 'sql'."}
            ddl_result = self.verify_ddl(step.sql)
            return ddl_result.model_dump()

        if kind == "insert_sample":
            if not step.table_name:
                return {
                    "success": False,
                    "error": "insert_sample step requires 'table_name'.",
                }
            count = self.insert_sample_data(
                step.table_name, num_rows=step.num_sample_rows
            )
            return {"success": True, "rows_inserted": count}

        if kind == "computation_sql":
            if not step.sql:
                return {
                    "success": False,
                    "error": "computation_sql step requires 'sql'.",
                }
            sql_result = self.verify_computation_sql(
                step.sql, expected_columns=step.expected_columns
            )
            return sql_result.model_dump()

        if kind == "verify":
            if not step.table_name:
                return {
                    "success": False,
                    "error": "verify step requires 'table_name'.",
                }
            return self._verify_table_expectations(
                table_name=step.table_name,
                expected_row_count=step.expected_row_count,
                expected_columns=step.expected_columns,
            )

        return {
            "success": False,
            "error": f"Unknown step_type: {step.step_type!r}",
        }

    def _verify_table_expectations(
        self,
        table_name: str,
        expected_row_count: Optional[int] = None,
        expected_columns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Check that a table meets expected row-count and column criteria."""
        errors: List[str] = []

        # Column check
        if expected_columns is not None:
            info = self.describe_table(table_name)
            actual_cols = {c["name"] for c in info.columns}
            expected_set = {c for c in expected_columns}
            missing = expected_set - actual_cols
            if missing:
                errors.append(
                    f"Table '{table_name}' missing columns: {sorted(missing)}"
                )

        # Row count check
        if expected_row_count is not None:
            actual_count = self.get_row_count(table_name)
            if actual_count != expected_row_count:
                errors.append(
                    f"Table '{table_name}' row count: "
                    f"expected {expected_row_count}, got {actual_count}"
                )

        success = len(errors) == 0
        return {
            "success": success,
            "table_name": table_name,
            "errors": errors,
        }

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def list_tables(self) -> List[str]:
        """Return the names of all user-created tables in the sandbox."""
        self._ensure_open()
        assert self._conn is not None

        try:
            rows = self._conn.execute(
                "SELECT table_name "
                "FROM information_schema.tables "
                "WHERE table_schema = 'main' "
                "  AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ).fetchall()
            return [row[0] for row in rows]
        except Exception:
            # Fallback for older DuckDB versions
            try:
                rows = self._conn.execute("SHOW TABLES").fetchall()
                return [row[0] for row in rows]
            except Exception:
                return []

    def describe_table(self, table_name: str) -> TableInfo:
        """
        Return column metadata and row count for *table_name*.

        Raises if the table does not exist.
        """
        self._ensure_open()
        assert self._conn is not None

        columns = self._describe_raw(table_name)
        row_count = self.get_row_count(table_name)
        size_bytes = self._estimate_table_size(table_name)

        return TableInfo(
            table_name=table_name,
            columns=columns,
            row_count=row_count,
            estimated_size_bytes=size_bytes,
        )

    def get_row_count(self, table_name: str) -> int:
        """Return the number of rows in *table_name*."""
        self._ensure_open()
        assert self._conn is not None

        try:
            result = self._conn.execute(
                f'SELECT COUNT(*) AS cnt FROM "{table_name}"'
            ).fetchone()
            return result[0] if result else 0
        except Exception as exc:
            logger.error("get_row_count failed for '%s': %s", table_name, exc)
            raise

    def get_table_ddl(self, table_name: str) -> str:
        """
        Return the ``CREATE TABLE`` statement for *table_name* as DuckDB
        would generate it.
        """
        self._ensure_open()
        assert self._conn is not None

        try:
            # DuckDB provides a SQL column via sqlite_master-compatible view.
            result = self._conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                [table_name],
            ).fetchone()
            if result and result[0]:
                return result[0]

            # Fallback: use DESCRIBE to reconstruct a minimal DDL.
            columns = self._describe_raw(table_name)
            col_defs = []
            for col in columns:
                nullable = "" if col.get("nullable", True) else " NOT NULL"
                default = (
                    f" DEFAULT {col['default']}" if col.get("default") else ""
                )
                col_defs.append(f'    "{col["name"]}" {col["type"]}{nullable}{default}')

            cols_sql = ",\n".join(col_defs)
            return f'CREATE TABLE "{table_name}" (\n{cols_sql}\n);'
        except Exception as exc:
            logger.error(
                "get_table_ddl failed for '%s': %s", table_name, exc
            )
            raise

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #

    def export_to_parquet(self, table_name: str, path: str) -> str:
        """
        Export *table_name* to a Parquet file at *path*.

        Returns the absolute path of the written file.
        """
        self._ensure_open()
        assert self._conn is not None

        abs_path = str(Path(path).resolve())
        # Ensure parent directory exists.
        Path(abs_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn.execute(
            f"COPY \"{table_name}\" TO '{abs_path}' (FORMAT PARQUET)"
        )
        logger.info(
            "Exported table '%s' to Parquet: %s", table_name, abs_path
        )
        return abs_path

    def export_to_csv(self, table_name: str, path: str) -> str:
        """
        Export *table_name* to a CSV file at *path*.

        Returns the absolute path of the written file.
        """
        self._ensure_open()
        assert self._conn is not None

        abs_path = str(Path(path).resolve())
        Path(abs_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn.execute(
            f"COPY \"{table_name}\" TO '{abs_path}' "
            f"(FORMAT CSV, HEADER TRUE)"
        )
        logger.info("Exported table '%s' to CSV: %s", table_name, abs_path)
        return abs_path

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _ensure_open(self) -> None:
        """Raise if the sandbox has not been opened yet."""
        if self._conn is None:
            raise RuntimeError(
                "DuckDBSandbox is not open. Call .open() or use as a "
                "context manager before executing queries."
            )

    def _describe_raw(self, table_name: str) -> List[Dict[str, Any]]:
        """
        Run ``DESCRIBE`` on a table and return raw column dicts.

        Each dict has keys: ``name``, ``type``, ``nullable``, ``default``.
        """
        assert self._conn is not None
        rows = self._conn.execute(
            f'DESCRIBE "{table_name}"'
        ).fetchall()

        columns: List[Dict[str, Any]] = []
        for row in rows:
            # DESCRIBE returns: (name, type, nullable_str, default, key, extra)
            columns.append(
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2].upper() == "YES" if len(row) > 2 else True,
                    "default": row[3] if len(row) > 3 and row[3] else None,
                }
            )
        return columns

    def _estimate_table_size(self, table_name: str) -> int:
        """
        Estimate the in-memory size of a table in bytes.

        Uses DuckDB's ``pragma storage_info`` when available, falling back
        to a rough heuristic.
        """
        assert self._conn is not None

        try:
            result = self._conn.execute(
                "SELECT SUM(block_size * number_of_blocks) AS total_bytes "
                "FROM pragma_storage_info("
                f"'{table_name}'"
                ")"
            ).fetchone()
            if result and result[0] is not None:
                return int(result[0])
        except Exception:
            pass

        # Heuristic: row_count * 64 bytes (rough average per row).
        try:
            count = self.get_row_count(table_name)
            return count * 64
        except Exception:
            return 0

    @staticmethod
    def _extract_table_name(ddl: str) -> Optional[str]:
        """
        Extract the table name from a DDL statement using regex.

        Handles patterns like:
          - ``CREATE TABLE foo (...)``
          - ``CREATE TABLE IF NOT EXISTS foo (...)``
          - ``CREATE TABLE "schema"."foo" (...)``
          - ``ALTER TABLE foo ...``
          - ``DROP TABLE foo``
        """
        # Match CREATE TABLE [IF NOT EXISTS] [schema.]table
        patterns = [
            re.compile(
                r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+"
                r"(?:IF\s+NOT\s+EXISTS\s+)?"
                r'(?:["\w]+\.)?"?(\w+)"?',
                re.IGNORECASE,
            ),
            re.compile(
                r"ALTER\s+TABLE\s+"
                r'(?:IF\s+EXISTS\s+)?(?:["\w]+\.)?"?(\w+)"?',
                re.IGNORECASE,
            ),
            re.compile(
                r"DROP\s+TABLE\s+"
                r'(?:IF\s+EXISTS\s+)?(?:["\w]+\.)?"?(\w+)"?',
                re.IGNORECASE,
            ),
            re.compile(
                r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\s+)?(?:TEMPORARY\s+)?"
                r"VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                r'(?:["\w]+\.)?"?(\w+)"?',
                re.IGNORECASE,
            ),
        ]
        for pat in patterns:
            match = pat.search(ddl)
            if match:
                return match.group(1)
        return None
