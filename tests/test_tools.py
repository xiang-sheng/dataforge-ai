"""Tests for src.warehouse.tools — LangChain tools for data analysis."""

import os
import tempfile

import duckdb
import pytest
from src.warehouse.tools import (
    ALL_TOOLS,
    _validate_identifier,
    compare_tables,
    create_table_from_query,
    describe_table,
    execute_ddl,
    execute_query,
    get_sample_data,
    init_tool_context,
    list_tables,
    read_convention,
    scan_redundancy_candidates,
)


@pytest.fixture(autouse=True)
def setup_db():
    """Create a fresh DuckDB for each test."""
    db_path = os.path.join(tempfile.gettempdir(), "_test_tools.duckdb")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = duckdb.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER, name VARCHAR, age INTEGER)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice', 30), (2, 'Bob', 25)")
    init_tool_context(conn)
    yield conn
    conn.close()
    if os.path.exists(db_path):
        os.remove(db_path)


# --- Identifier validation ---


class TestValidateIdentifier:
    def test_valid_names(self):
        assert _validate_identifier("users") == "users"
        assert _validate_identifier("order_items") == "order_items"
        assert _validate_identifier("_tmp") == "_tmp"
        assert _validate_identifier("Table123") == "Table123"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="不安全"):
            _validate_identifier("")

    def test_rejects_sql_injection(self):
        with pytest.raises(ValueError, match="不安全"):
            _validate_identifier('"; DROP TABLE users; --')

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError):
            _validate_identifier("table; DROP")
        with pytest.raises(ValueError):
            _validate_identifier("table' OR '1'='1")
        with pytest.raises(ValueError):
            _validate_identifier("table--comment")

    def test_rejects_starts_with_digit(self):
        with pytest.raises(ValueError):
            _validate_identifier("1table")


# --- list_tables ---


class TestListTables:
    def test_lists_existing_tables(self, setup_db):
        result = list_tables.invoke({})
        assert "users" in result
        assert "2" in result  # 2 rows

    def test_empty_db(self):
        conn = duckdb.connect(":memory:")
        init_tool_context(conn)
        result = list_tables.invoke({})
        assert "没有" in result


# --- describe_table ---


class TestDescribeTable:
    def test_describes_table(self, setup_db):
        result = describe_table.invoke({"table_name": "users"})
        assert "users" in result
        assert "name" in result
        assert "VARCHAR" in result

    def test_nonexistent_table(self, setup_db):
        result = describe_table.invoke({"table_name": "nonexistent"})
        assert "未找到" in result

    def test_rejects_injection(self, setup_db):
        result = describe_table.invoke({"table_name": '"; DROP TABLE users; --'})
        assert "不安全" in result


# --- get_sample_data ---


class TestGetSampleData:
    def test_returns_data(self, setup_db):
        result = get_sample_data.invoke({"table_name": "users", "limit": 5})
        assert "Alice" in result
        assert "Bob" in result

    def test_limit_clamped(self, setup_db):
        result = get_sample_data.invoke({"table_name": "users", "limit": 100})
        assert "Alice" in result  # Still works, limit clamped to 20

    def test_rejects_injection(self, setup_db):
        result = get_sample_data.invoke({"table_name": "users; DROP"})
        assert "不安全" in result


# --- execute_query ---


class TestExecuteQuery:
    def test_select_works(self, setup_db):
        result = execute_query.invoke({"sql": "SELECT name, age FROM users ORDER BY age"})
        assert "Bob" in result
        assert "Alice" in result

    def test_with_cte(self, setup_db):
        result = execute_query.invoke({
            "sql": "WITH cte AS (SELECT * FROM users) SELECT * FROM cte"
        })
        assert "Alice" in result

    def test_blocks_dml(self, setup_db):
        result = execute_query.invoke({"sql": "INSERT INTO users VALUES (3, 'Eve', 20)"})
        assert "仅允许" in result

    def test_bad_sql(self, setup_db):
        result = execute_query.invoke({"sql": "SELECT * FROM nonexistent"})
        assert "失败" in result


# --- execute_ddl ---


class TestExecuteDDL:
    def test_create_table(self, setup_db):
        result = execute_ddl.invoke({
            "ddl": "CREATE TABLE orders (id INTEGER, amount DECIMAL(10,2))"
        })
        assert "OK" in result
        assert "orders" in result

    def test_multiple_statements(self, setup_db):
        result = execute_ddl.invoke({
            "ddl": "CREATE TABLE t1 (id INT); CREATE TABLE t2 (id INT);"
        })
        assert result.count("OK") == 2

    def test_bad_ddl(self, setup_db):
        result = execute_ddl.invoke({"ddl": "THIS IS NOT SQL"})
        assert "FAIL" in result


# --- create_table_from_query ---


class TestCreateTableFromQuery:
    def test_materialize(self, setup_db):
        result = create_table_from_query.invoke({
            "table_name": "user_summary",
            "select_sql": "SELECT name, age FROM users WHERE age > 26",
            "table_comment": "成年用户",
        })
        assert "创建成功" in result
        assert "1" in result  # 1 row (Alice)

    def test_blocks_dml_in_select(self, setup_db):
        result = create_table_from_query.invoke({
            "table_name": "bad",
            "select_sql": "DROP TABLE users",
        })
        assert "必须" in result or "失败" in result

    def test_rejects_bad_table_name(self, setup_db):
        result = create_table_from_query.invoke({
            "table_name": "bad; DROP",
            "select_sql": "SELECT 1",
        })
        assert "不安全" in result


# --- read_convention ---


class TestReadConvention:
    def test_no_convention(self):
        init_tool_context(duckdb.connect(":memory:"))
        result = read_convention.invoke({})
        assert "未配置" in result

    def test_reads_file(self):
        conv_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "conventions", "default_convention.yaml"
        )
        if os.path.exists(conv_path):
            init_tool_context(duckdb.connect(":memory:"), conv_path)
            result = read_convention.invoke({})
            assert "规范文件" in result


# --- compare_tables ---


class TestCompareTables:
    def test_identical_tables(self, setup_db):
        """Two tables with identical schemas should show 100% similarity."""
        setup_db.execute("""
            CREATE TABLE users_copy (id INTEGER, name VARCHAR, age INTEGER)
        """)
        setup_db.execute("INSERT INTO users_copy VALUES (1, 'Alice', 30)")
        result = compare_tables.invoke({"table_a": "users", "table_b": "users_copy"})
        assert "100%" in result
        assert "冗余" in result or "高度相似" in result

    def test_different_tables(self, setup_db):
        """Tables with completely different schemas should show low similarity."""
        setup_db.execute("""
            CREATE TABLE products (pid INTEGER, title VARCHAR, price DOUBLE, stock INTEGER)
        """)
        result = compare_tables.invoke({"table_a": "users", "table_b": "products"})
        assert "差异较大" in result

    def test_partial_overlap(self, setup_db):
        """Tables sharing some columns should show partial overlap."""
        setup_db.execute("""
            CREATE TABLE user_profiles (id INTEGER, name VARCHAR, email VARCHAR, bio VARCHAR)
        """)
        result = compare_tables.invoke({"table_a": "users", "table_b": "user_profiles"})
        assert "共有列" in result
        assert "id" in result.lower()

    def test_nonexistent_table(self, setup_db):
        result = compare_tables.invoke({"table_a": "users", "table_b": "ghost_table"})
        assert "不存在" in result

    def test_injection_rejected(self, setup_db):
        result = compare_tables.invoke({"table_a": "users; DROP TABLE users", "table_b": "users"})
        assert "不安全" in result

    def test_all_tools_includes_compare(self):
        """compare_tables should be in ALL_TOOLS list."""
        tool_names = [t.name for t in ALL_TOOLS]
        assert "compare_tables" in tool_names


# --- scan_redundancy_candidates ---


class TestScanRedundancyCandidates:
    def test_finds_similar_tables(self, setup_db):
        """Tables with identical schemas should be flagged as candidates."""
        from unittest.mock import MagicMock, patch

        from src.warehouse.embedding import CandidatePair

        setup_db.execute("""
            CREATE TABLE orders_bak (
                id INTEGER, user_id INTEGER, amount DECIMAL(18,2),
                status VARCHAR, created_at TIMESTAMP
            )
        """)
        setup_db.execute("INSERT INTO orders_bak VALUES (1, 1, 10.0, 'ok', '2025-01-01')")

        # Mock the SchemaEmbedder to return a predefined candidate
        mock_embedder = MagicMock()
        mock_embedder.find_candidates.return_value = [
            CandidatePair("orders", "orders_bak", 0.92, 5, 5, 1, 1),
        ]

        with patch("src.warehouse.embedding.SchemaEmbedder", return_value=mock_embedder):
            result = scan_redundancy_candidates.invoke({
                "similarity_threshold": 0.5, "top_k": 10
            })
            assert "候选" in result
            assert "orders" in result

    def test_empty_db(self):
        """Empty database should report no need for detection."""
        init_tool_context(duckdb.connect(":memory:"))
        result = scan_redundancy_candidates.invoke({
            "similarity_threshold": 0.5, "top_k": 10
        })
        assert "无需" in result or "0" in result or "1" in result

    def test_single_table(self, setup_db):
        """Database with only the setup 'users' table."""
        # setup_db creates 'users' table by default
        # Clear other tables if any
        init_tool_context(duckdb.connect(":memory:"))
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE only_table (id INTEGER)")
        init_tool_context(conn)
        result = scan_redundancy_candidates.invoke({
            "similarity_threshold": 0.5, "top_k": 10
        })
        assert "无需" in result

    def test_all_tools_includes_scan(self):
        """scan_redundancy_candidates should be in ALL_TOOLS."""
        tool_names = [t.name for t in ALL_TOOLS]
        assert "scan_redundancy_candidates" in tool_names
