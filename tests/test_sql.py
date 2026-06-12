"""
DataForge AI - SQL generation API tests.

Tests for SQL generation, explanation, optimization, translation,
and execution endpoints using mock AI responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient

BASE = "/api/v1/sql"


# ---------------------------------------------------------------------------
# SQL Generation
# ---------------------------------------------------------------------------


class TestSQLGeneration:
    """Tests for POST /sql/generate."""

    @pytest.mark.asyncio
    async def test_generate_sql_basic(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return generated SQL with explanation from the mock AI."""
        payload = {
            "prompt": "Get the 100 most recent users created in 2024",
            "dialect": "clickhouse",
        }

        resp = await async_client.post(f"{BASE}/generate", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "sql" in body
        assert len(body["sql"]) > 0
        assert "explanation" in body
        assert body["confidence"] > 0

    @pytest.mark.asyncio
    async def test_generate_sql_with_schema_context(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should accept inline schema context."""
        payload = {
            "prompt": "Count orders per customer",
            "dialect": "postgresql",
            "schema_context": (
                "CREATE TABLE orders (id BIGINT, customer_id BIGINT, amount DECIMAL);"
                "CREATE TABLE customers (id BIGINT, name VARCHAR(100));"
            ),
        }

        resp = await async_client.post(f"{BASE}/generate", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "sql" in body

    @pytest.mark.asyncio
    async def test_generate_sql_with_connection_context(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should introspect the connection schema when connection_id is provided."""
        payload = {
            "prompt": "Show all users with their emails",
            "dialect": "postgresql",
            "connection_id": "conn-001",
            "database": "testdb",
        }

        resp = await async_client.post(f"{BASE}/generate", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "sql" in body

    @pytest.mark.asyncio
    async def test_generate_sql_empty_prompt(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 when the prompt is empty."""
        payload = {
            "prompt": "",
            "dialect": "clickhouse",
        }

        resp = await async_client.post(f"{BASE}/generate", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_generate_sql_with_additional_instructions(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should pass additional instructions to the AI provider."""
        payload = {
            "prompt": "Get user counts per region",
            "dialect": "clickhouse",
            "additional_instructions": "Use CTEs instead of subqueries",
            "max_results": 50,
        }

        resp = await async_client.post(f"{BASE}/generate", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "sql" in body
        assert isinstance(body.get("alternative_queries"), list)


# ---------------------------------------------------------------------------
# SQL Explanation
# ---------------------------------------------------------------------------


class TestSQLExplain:
    """Tests for POST /sql/explain."""

    @pytest.mark.asyncio
    async def test_explain_sql(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return a structured explanation of the SQL."""
        payload = {
            "sql": "SELECT id, name, email FROM users WHERE created_at >= '2024-01-01' ORDER BY created_at DESC LIMIT 100;",
            "dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/explain", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "summary" in body
        assert len(body["summary"]) > 0
        assert isinstance(body["step_by_step"], list)
        assert len(body["step_by_step"]) > 0
        assert isinstance(body["tables_used"], list)
        assert "users" in body["tables_used"]

    @pytest.mark.asyncio
    async def test_explain_sql_detailed_level(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should accept the 'detailed' detail level."""
        payload = {
            "sql": "SELECT COUNT(*) FROM orders GROUP BY customer_id;",
            "dialect": "clickhouse",
            "detail_level": "detailed",
        }

        resp = await async_client.post(f"{BASE}/explain", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "estimated_complexity" in body

    @pytest.mark.asyncio
    async def test_explain_empty_sql(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 when SQL is empty."""
        payload = {
            "sql": "",
            "dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/explain", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_explain_includes_performance_notes(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Mock should include performance notes in the response."""
        payload = {
            "sql": "SELECT * FROM users;",
            "dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/explain", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["performance_notes"], list)


# ---------------------------------------------------------------------------
# SQL Optimization
# ---------------------------------------------------------------------------


class TestSQLOptimize:
    """Tests for POST /sql/optimize."""

    @pytest.mark.asyncio
    async def test_optimize_sql(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return optimised SQL with a change log."""
        payload = {
            "sql": "SELECT u.id, u.name, p.bio FROM users u JOIN profiles p ON u.id = p.user_id WHERE u.created_at >= '2024-01-01' ORDER BY u.created_at DESC;",
            "dialect": "postgresql",
            "optimization_goals": ["performance"],
        }

        resp = await async_client.post(f"{BASE}/optimize", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["original_sql"] == payload["sql"]
        assert "optimized_sql" in body
        assert len(body["optimized_sql"]) > 0
        assert isinstance(body["changes"], list)
        assert len(body["changes"]) > 0

    @pytest.mark.asyncio
    async def test_optimize_with_schema_context(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should accept schema context for better optimization."""
        payload = {
            "sql": "SELECT * FROM orders WHERE status = 'completed';",
            "dialect": "clickhouse",
            "schema_context": "CREATE TABLE orders (id BIGINT, status VARCHAR(20)) ENGINE = MergeTree();",
            "optimization_goals": ["performance", "readability"],
        }

        resp = await async_client.post(f"{BASE}/optimize", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "optimized_sql" in body

    @pytest.mark.asyncio
    async def test_optimize_empty_sql(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 for empty SQL."""
        payload = {
            "sql": "",
            "dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/optimize", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_optimize_returns_warnings(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Mock response should include warnings."""
        payload = {
            "sql": "SELECT * FROM users;",
            "dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/optimize", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("warnings"), list)


# ---------------------------------------------------------------------------
# SQL Translation
# ---------------------------------------------------------------------------


class TestSQLTranslate:
    """Tests for POST /sql/translate."""

    @pytest.mark.asyncio
    async def test_translate_clickhouse_to_postgres(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should translate SQL from ClickHouse to PostgreSQL."""
        payload = {
            "sql": "SELECT toDate(created_at) AS dt, count() FROM events GROUP BY dt;",
            "source_dialect": "clickhouse",
            "target_dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/translate", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["source_dialect"] == "clickhouse"
        assert body["target_dialect"] == "postgresql"
        assert "translated_sql" in body
        assert isinstance(body["translation_notes"], list)

    @pytest.mark.asyncio
    async def test_translate_same_dialect_rejected(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 400 when source and target dialects are the same."""
        payload = {
            "sql": "SELECT 1;",
            "source_dialect": "postgresql",
            "target_dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/translate", json=payload)

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_translate_empty_sql(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 for empty SQL."""
        payload = {
            "sql": "",
            "source_dialect": "mysql",
            "target_dialect": "postgresql",
        }

        resp = await async_client.post(f"{BASE}/translate", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_translate_unsupported_features_list(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Response should include unsupported_features list."""
        payload = {
            "sql": "SELECT arrayJoin([1,2,3]);",
            "source_dialect": "clickhouse",
            "target_dialect": "mysql",
        }

        resp = await async_client.post(f"{BASE}/translate", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("unsupported_features"), list)

    @pytest.mark.asyncio
    async def test_translate_preserve_comments_option(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should accept the preserve_comments option."""
        payload = {
            "sql": "-- Get active users\nSELECT * FROM users WHERE active = 1;",
            "source_dialect": "mysql",
            "target_dialect": "postgresql",
            "preserve_comments": True,
        }

        resp = await async_client.post(f"{BASE}/translate", json=payload)

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# SQL Execution
# ---------------------------------------------------------------------------


class TestSQLExecute:
    """Tests for POST /sql/execute."""

    @pytest.mark.asyncio
    async def test_execute_select(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should execute SQL and return results from the mock adapter."""
        payload = {
            "sql": "SELECT id, name, email FROM users LIMIT 10;",
            "connection_id": "conn-001",
            "database": "testdb",
        }

        resp = await async_client.post(f"{BASE}/execute", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["row_count"] > 0
        assert isinstance(body["columns"], list)
        assert isinstance(body["rows"], list)
        assert body["execution_time_ms"] > 0

    @pytest.mark.asyncio
    async def test_execute_nonexistent_connection(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 502 when the connection does not exist."""
        payload = {
            "sql": "SELECT 1;",
            "connection_id": "ghost-conn",
        }

        resp = await async_client.post(f"{BASE}/execute", json=payload)

        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_execute_empty_sql(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 for empty SQL."""
        payload = {
            "sql": "",
            "connection_id": "conn-001",
        }

        resp = await async_client.post(f"{BASE}/execute", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_with_parameters(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should accept parameterised query values."""
        payload = {
            "sql": "SELECT * FROM users WHERE id = :user_id;",
            "connection_id": "conn-001",
            "parameters": {"user_id": 42},
            "max_rows": 10,
            "timeout_seconds": 60,
        }

        resp = await async_client.post(f"{BASE}/execute", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "columns" in body
        assert "rows" in body
