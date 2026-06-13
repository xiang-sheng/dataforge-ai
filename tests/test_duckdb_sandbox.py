"""Tests for src.db.duckdb_sandbox -- DuckDB-based local sandbox."""

from __future__ import annotations

import duckdb
import pytest
from src.db.duckdb_sandbox import (
    BatchDDLResult,
    DDLVerifyResult,
    DuckDBSandbox,
    QueryResult,
    SQLVerifyResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandbox():
    """Create an in-memory DuckDBSandbox, open it, and close after the test."""
    sb = DuckDBSandbox(db_path=":memory:")
    sb.open()
    yield sb
    sb.close()


@pytest.fixture()
def sandbox_with_table(sandbox):
    """Sandbox with a pre-created 'orders' table."""
    sandbox.verify_ddl(
        "CREATE TABLE orders ("
        "  id INTEGER,"
        "  customer_name VARCHAR,"
        "  amount DECIMAL(10,2),"
        "  status VARCHAR,"
        "  created_at TIMESTAMP"
        ")"
    )
    return sandbox


# ======================================================================== #
# Sandbox lifecycle
# ======================================================================== #


class TestSandboxLifecycle:
    """Sandbox creation, open/close, and context-manager usage."""

    def test_open_and_close(self):
        sb = DuckDBSandbox()
        sb.open()
        assert "open" in repr(sb)
        sb.close()
        assert "closed" in repr(sb)

    def test_context_manager(self):
        with DuckDBSandbox() as sb:
            assert "open" in repr(sb)
        assert "closed" in repr(sb)

    def test_double_open_is_safe(self):
        sb = DuckDBSandbox()
        sb.open()
        sb.open()  # should not raise
        sb.close()

    def test_operations_on_closed_sandbox_raise(self):
        sb = DuckDBSandbox()
        with pytest.raises(RuntimeError, match="not open"):
            sb.verify_ddl("CREATE TABLE t (id INT)")

    def test_reset_drops_all_tables(self, sandbox_with_table):
        tables_before = sandbox_with_table.list_tables()
        assert "orders" in tables_before

        sandbox_with_table.reset()
        tables_after = sandbox_with_table.list_tables()
        assert "orders" not in tables_after


# ======================================================================== #
# DDL verification
# ======================================================================== #


class TestVerifyDDL:
    """DDL execution and table validation."""

    def test_create_table_succeeds(self, sandbox):
        result = sandbox.verify_ddl(
            "CREATE TABLE users (id INTEGER, name VARCHAR)"
        )
        assert isinstance(result, DDLVerifyResult)
        assert result.success is True
        assert result.table_name == "users"
        assert "id" in result.columns_created
        assert "name" in result.columns_created
        assert result.error is None

    def test_create_table_if_not_exists(self, sandbox):
        result = sandbox.verify_ddl(
            "CREATE TABLE IF NOT EXISTS events ("
            "  event_id BIGINT,"
            "  event_type VARCHAR"
            ")"
        )
        assert result.success is True
        assert result.table_name == "events"

    def test_invalid_ddl_fails(self, sandbox):
        result = sandbox.verify_ddl("THIS IS NOT VALID SQL")
        assert result.success is False
        assert result.error is not None

    def test_table_exists_after_ddl(self, sandbox):
        sandbox.verify_ddl("CREATE TABLE products (pid INTEGER, title VARCHAR)")
        tables = sandbox.list_tables()
        assert "products" in tables

    def test_verify_multiple_ddl_stops_on_failure(self, sandbox):
        ddls = [
            "CREATE TABLE t1 (id INT)",
            "INVALID SQL HERE",
            "CREATE TABLE t3 (id INT)",  # should NOT execute
        ]
        results = sandbox.verify_multiple_ddl(ddls)
        assert len(results) == 2  # stops at second
        assert results[0].success is True
        assert results[1].success is False

    def test_batch_execute_ddl_continues_past_failure(self, sandbox):
        ddls = [
            "CREATE TABLE ok1 (id INT)",
            "NOT VALID SQL",
            "CREATE TABLE ok2 (id INT)",
        ]
        result = sandbox.batch_execute_ddl(ddls)
        assert isinstance(result, BatchDDLResult)
        assert result.total == 3
        assert result.succeeded == 2
        assert result.failed == 1


# ======================================================================== #
# _estimate_table_size -- SQL injection rejection
# ======================================================================== #


class TestEstimateTableSize:
    """Regex validation in _estimate_table_size rejects malicious names."""

    def test_valid_identifier(self, sandbox_with_table):
        size = sandbox_with_table._estimate_table_size("orders")
        # Should return a non-negative integer (may be 0 for empty table)
        assert isinstance(size, int)
        assert size >= 0

    def test_rejects_quoted_name(self, sandbox_with_table):
        result = sandbox_with_table._estimate_table_size("orders'; DROP TABLE orders; --")
        assert result == 0

    def test_rejects_semicolon(self, sandbox_with_table):
        result = sandbox_with_table._estimate_table_size("orders; DROP TABLE orders")
        assert result == 0

    def test_rejects_single_quote(self, sandbox_with_table):
        result = sandbox_with_table._estimate_table_size("orders' OR '1'='1")
        assert result == 0

    def test_rejects_double_quote(self, sandbox_with_table):
        result = sandbox_with_table._estimate_table_size('orders" OR "1"="1')
        assert result == 0

    def test_rejects_parenthesis(self, sandbox_with_table):
        result = sandbox_with_table._estimate_table_size("orders()")
        assert result == 0

    def test_rejects_starts_with_digit(self, sandbox_with_table):
        result = sandbox_with_table._estimate_table_size("1orders")
        assert result == 0

    def test_accepts_underscore_prefix(self, sandbox_with_table):
        # _tmp is a valid SQL identifier
        sandbox_with_table.verify_ddl("CREATE TABLE _tmp (id INT)")
        size = sandbox_with_table._estimate_table_size("_tmp")
        assert isinstance(size, int)
        assert size >= 0


# ======================================================================== #
# _extract_table_name -- various DDL formats
# ======================================================================== #


class TestExtractTableName:
    """Static extraction of table names from DDL strings."""

    def test_create_table(self):
        name = DuckDBSandbox._extract_table_name("CREATE TABLE foo (id INT)")
        assert name == "foo"

    def test_create_table_if_not_exists(self):
        name = DuckDBSandbox._extract_table_name(
            "CREATE TABLE IF NOT EXISTS bar (id INT)"
        )
        assert name == "bar"

    def test_create_or_replace_table(self):
        name = DuckDBSandbox._extract_table_name(
            "CREATE OR REPLACE TABLE baz (id INT)"
        )
        assert name == "baz"

    def test_quoted_table_name(self):
        name = DuckDBSandbox._extract_table_name(
            'CREATE TABLE "my_table" (id INT)'
        )
        assert name == "my_table"

    def test_schema_qualified(self):
        name = DuckDBSandbox._extract_table_name(
            'CREATE TABLE "main"."orders" (id INT)'
        )
        assert name == "orders"

    def test_alter_table(self):
        name = DuckDBSandbox._extract_table_name(
            "ALTER TABLE users ADD COLUMN age INT"
        )
        assert name == "users"

    def test_drop_table(self):
        name = DuckDBSandbox._extract_table_name("DROP TABLE old_data")
        assert name == "old_data"

    def test_drop_table_if_exists(self):
        name = DuckDBSandbox._extract_table_name(
            "DROP TABLE IF EXISTS old_data"
        )
        assert name == "old_data"

    def test_create_view(self):
        name = DuckDBSandbox._extract_table_name(
            "CREATE VIEW v_active AS SELECT * FROM users WHERE active = 1"
        )
        assert name == "v_active"

    def test_no_match_returns_none(self):
        name = DuckDBSandbox._extract_table_name("SELECT 1")
        assert name is None


# ======================================================================== #
# Schema extraction (describe_table / list_tables)
# ======================================================================== #


class TestSchemaExtraction:
    """Introspecting tables that exist in the sandbox."""

    def test_list_tables_empty(self, sandbox):
        assert sandbox.list_tables() == []

    def test_list_tables_after_create(self, sandbox):
        sandbox.verify_ddl("CREATE TABLE alpha (id INT)")
        sandbox.verify_ddl("CREATE TABLE beta (id INT)")
        tables = sandbox.list_tables()
        assert "alpha" in tables
        assert "beta" in tables

    def test_describe_table_columns(self, sandbox_with_table):
        info = sandbox_with_table.describe_table("orders")
        col_names = [c["name"] for c in info.columns]
        assert "id" in col_names
        assert "customer_name" in col_names
        assert "amount" in col_names
        assert info.table_name == "orders"

    def test_describe_table_row_count(self, sandbox_with_table):
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (1, 'Alice', 10.5, 'active', '2025-01-01')"
        )
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (2, 'Bob', 20.0, 'pending', '2025-01-02')"
        )
        info = sandbox_with_table.describe_table("orders")
        assert info.row_count == 2

    def test_get_row_count(self, sandbox_with_table):
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (1, 'Alice', 10.5, 'active', '2025-01-01')"
        )
        assert sandbox_with_table.get_row_count("orders") == 1

    def test_get_table_ddl(self, sandbox_with_table):
        ddl = sandbox_with_table.get_table_ddl("orders")
        assert "orders" in ddl.lower() or "CREATE" in ddl.upper()


# ======================================================================== #
# SQL verification (execute_and_preview / verify_computation_sql)
# ======================================================================== #


class TestSQLVerification:
    """Executing SELECT queries and computation SQL."""

    def test_execute_and_preview_returns_results(self, sandbox_with_table):
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (1, 'Alice', 10.5, 'active', '2025-01-01')"
        )
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (2, 'Bob', 25.0, 'pending', '2025-01-02')"
        )

        result = sandbox_with_table.execute_and_preview(
            "SELECT id, customer_name, amount FROM orders ORDER BY id"
        )
        assert isinstance(result, QueryResult)
        assert result.row_count == 2
        assert result.columns == ["id", "customer_name", "amount"]
        assert result.rows[0]["customer_name"] == "Alice"
        assert result.rows[1]["customer_name"] == "Bob"

    def test_execute_and_preview_respects_max_rows(self, sandbox_with_table):
        for i in range(10):
            sandbox_with_table._conn.execute(
                f"INSERT INTO orders VALUES ({i}, 'User{i}', {i}.0, 'ok', '2025-01-01')"
            )
        result = sandbox_with_table.execute_and_preview(
            "SELECT * FROM orders", max_rows=3
        )
        assert result.row_count == 3

    def test_execute_and_preview_bad_sql_raises(self, sandbox_with_table):
        with pytest.raises(duckdb.Error):
            sandbox_with_table.execute_and_preview("SELECT * FROM nonexistent")

    def test_verify_computation_sql_select(self, sandbox_with_table):
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (1, 'Alice', 10.5, 'active', '2025-01-01')"
        )
        result = sandbox_with_table.verify_computation_sql(
            "SELECT id, customer_name FROM orders"
        )
        assert isinstance(result, SQLVerifyResult)
        assert result.success is True
        assert "id" in result.result_columns
        assert "customer_name" in result.result_columns
        assert result.rows_affected == 1

    def test_verify_computation_sql_expected_columns_mismatch(
        self, sandbox_with_table
    ):
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (1, 'Alice', 10.5, 'active', '2025-01-01')"
        )
        result = sandbox_with_table.verify_computation_sql(
            "SELECT id FROM orders",
            expected_columns=["id", "nonexistent_col"],
        )
        assert result.success is False
        assert "Missing expected columns" in result.error

    def test_verify_computation_sql_with_cte(self, sandbox_with_table):
        sandbox_with_table._conn.execute(
            "INSERT INTO orders VALUES (1, 'Alice', 10.5, 'active', '2025-01-01')"
        )
        result = sandbox_with_table.verify_computation_sql(
            "WITH cte AS (SELECT * FROM orders) SELECT id FROM cte"
        )
        assert result.success is True

    def test_verify_computation_sql_invalid(self, sandbox_with_table):
        result = sandbox_with_table.verify_computation_sql(
            "SELECT * FROM table_does_not_exist"
        )
        assert result.success is False
        assert result.error is not None


# ======================================================================== #
# Sample data generation
# ======================================================================== #


class TestSampleDataGeneration:
    """Auto-generating and inserting sample data."""

    def test_insert_sample_data(self, sandbox_with_table):
        count = sandbox_with_table.insert_sample_data("orders", num_rows=10)
        assert count == 10
        row_count = sandbox_with_table.get_row_count("orders")
        assert row_count == 10

    def test_insert_from_dicts(self, sandbox_with_table):
        rows = [
            {"id": 1, "customer_name": "Alice", "amount": 10.5, "status": "active", "created_at": "2025-01-01"},
            {"id": 2, "customer_name": "Bob", "amount": 20.0, "status": "pending", "created_at": "2025-01-02"},
        ]
        count = sandbox_with_table.insert_from_dicts("orders", rows)
        assert count == 2
        assert sandbox_with_table.get_row_count("orders") == 2

    def test_insert_from_dicts_empty(self, sandbox_with_table):
        count = sandbox_with_table.insert_from_dicts("orders", [])
        assert count == 0
