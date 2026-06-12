"""
DataForge AI - AI modeling API tests.

Tests for modelling suggestion, dimensional model design,
model review, and partitioning advice endpoints using mock AI responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient

BASE = "/api/v1/modeling"


# ---------------------------------------------------------------------------
# Modelling Suggestions
# ---------------------------------------------------------------------------


class TestModelingSuggest:
    """Tests for POST /modeling/suggest."""

    @pytest.mark.asyncio
    async def test_suggest_basic(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should return AI modelling suggestions from the mock provider."""
        payload = {
            **sample_table_schema,
            "modeling_type": "dimensional",
        }

        resp = await async_client.post(f"{BASE}/suggest", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["suggestions"], list)
        assert len(body["suggestions"]) > 0
        suggestion = body["suggestions"][0]
        assert "category" in suggestion
        assert "description" in suggestion
        assert 0 <= suggestion["confidence"] <= 1

    @pytest.mark.asyncio
    async def test_suggest_with_business_context(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should accept business context for richer suggestions."""
        payload = {
            **sample_table_schema,
            "business_context": "E-commerce platform with order tracking and customer analytics.",
            "modeling_type": "dimensional",
            "target_platform": "ClickHouse",
        }

        resp = await async_client.post(f"{BASE}/suggest", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("proposed_ddl"), list)
        assert "summary" in body

    @pytest.mark.asyncio
    async def test_suggest_with_data_vault_type(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should accept data_vault modelling type."""
        payload = {
            **sample_table_schema,
            "modeling_type": "data_vault",
        }

        resp = await async_client.post(f"{BASE}/suggest", json=payload)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_suggest_empty_tables_rejected(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 when no tables are provided."""
        payload = {
            "tables": [],
            "modeling_type": "dimensional",
        }

        resp = await async_client.post(f"{BASE}/suggest", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_suggest_invalid_modeling_type(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should return 422 for an unsupported modelling type."""
        payload = {
            **sample_table_schema,
            "modeling_type": "relational_v5",
        }

        resp = await async_client.post(f"{BASE}/suggest", json=payload)

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Dimensional Model Design
# ---------------------------------------------------------------------------


class TestDimensionalModel:
    """Tests for POST /modeling/dimensional."""

    @pytest.mark.asyncio
    async def test_design_star_schema(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should return a star-schema design with fact and dimension tables."""
        payload = {
            "tables": sample_table_schema["tables"],
            "business_process": "order fulfilment",
            "grain": "one row per order line",
            "preferred_schema": "star",
        }

        resp = await async_client.post(f"{BASE}/dimensional", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["fact_tables"], list)
        assert len(body["fact_tables"]) > 0
        assert isinstance(body["dimension_tables"], list)
        assert len(body["dimension_tables"]) > 0
        assert "rationale" in body

    @pytest.mark.asyncio
    async def test_design_snowflake_schema(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should accept snowflake schema preference."""
        payload = {
            "tables": sample_table_schema["tables"],
            "business_process": "customer analytics",
            "preferred_schema": "snowflake",
            "target_platform": "Snowflake",
        }

        resp = await async_client.post(f"{BASE}/dimensional", json=payload)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_design_fact_table_structure(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Fact tables in the response should have expected fields."""
        payload = {
            "tables": sample_table_schema["tables"],
            "business_process": "sales tracking",
            "grain": "one row per transaction",
            "preferred_schema": "star",
        }

        resp = await async_client.post(f"{BASE}/dimensional", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        fact = body["fact_tables"][0]
        assert "name" in fact
        assert "grain" in fact
        assert "measures" in fact
        assert "ddl" in fact

    @pytest.mark.asyncio
    async def test_design_dimension_table_structure(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Dimension tables in the response should have expected fields."""
        payload = {
            "tables": sample_table_schema["tables"],
            "business_process": "customer segmentation",
            "preferred_schema": "star",
        }

        resp = await async_client.post(f"{BASE}/dimensional", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        dim = body["dimension_tables"][0]
        assert "name" in dim
        assert "attributes" in dim
        assert "ddl" in dim

    @pytest.mark.asyncio
    async def test_design_missing_business_process(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should return 422 when business_process is missing."""
        payload = {
            "tables": sample_table_schema["tables"],
            "preferred_schema": "star",
        }

        resp = await async_client.post(f"{BASE}/dimensional", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_design_includes_warnings(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Response should include a warnings field."""
        payload = {
            "tables": sample_table_schema["tables"],
            "business_process": "inventory tracking",
            "preferred_schema": "star",
        }

        resp = await async_client.post(f"{BASE}/dimensional", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("warnings"), list)


# ---------------------------------------------------------------------------
# Model Review
# ---------------------------------------------------------------------------


class TestModelReview:
    """Tests for POST /modeling/review."""

    @pytest.mark.asyncio
    async def test_review_basic(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should return review findings with a quality score."""
        payload = {
            "tables": sample_table_schema["tables"],
        }

        resp = await async_client.post(f"{BASE}/review", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["findings"], list)
        assert len(body["findings"]) > 0
        finding = body["findings"][0]
        assert "severity" in finding
        assert "table" in finding
        assert "message" in finding
        assert 0 <= body["score"] <= 100

    @pytest.mark.asyncio
    async def test_review_with_warehouse_layer(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should accept warehouse layer context."""
        payload = {
            "tables": sample_table_schema["tables"],
            "warehouse_layer": "dwd",
        }

        resp = await async_client.post(f"{BASE}/review", json=payload)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_review_with_standards(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Should accept team-specific standards."""
        payload = {
            "tables": sample_table_schema["tables"],
            "standards": "All tables must have a 'created_at' and 'updated_at' column. No VARCHAR without length.",
        }

        resp = await async_client.post(f"{BASE}/review", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "summary" in body

    @pytest.mark.asyncio
    async def test_review_finding_severity_values(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """Finding severities should be one of the allowed values."""
        payload = {
            "tables": sample_table_schema["tables"],
        }

        resp = await async_client.post(f"{BASE}/review", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        valid_severities = {"info", "warning", "error", "critical"}
        for finding in body["findings"]:
            assert finding["severity"] in valid_severities

    @pytest.mark.asyncio
    async def test_review_empty_tables_rejected(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 when no tables are provided."""
        payload = {
            "tables": [],
        }

        resp = await async_client.post(f"{BASE}/review", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_review_score_is_numeric(
        self,
        async_client: AsyncClient,
        sample_table_schema: dict[str, Any],
    ) -> None:
        """The quality score should be a numeric value between 0 and 100."""
        payload = {
            "tables": sample_table_schema["tables"],
        }

        resp = await async_client.post(f"{BASE}/review", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["score"], (int, float))
        assert 0 <= body["score"] <= 100


# ---------------------------------------------------------------------------
# Partition Advice
# ---------------------------------------------------------------------------


class TestPartitionAdvice:
    """Tests for POST /modeling/partition."""

    @pytest.mark.asyncio
    async def test_partition_advice_basic(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return partition key recommendations."""
        payload = {
            "table_name": "events",
            "columns": [
                {"name": "event_id", "data_type": "BIGINT"},
                {"name": "user_id", "data_type": "BIGINT"},
                {"name": "event_date", "data_type": "DATE"},
                {"name": "event_type", "data_type": "VARCHAR(50)"},
            ],
            "row_count": 10_000_000,
            "query_patterns": [
                "WHERE event_date BETWEEN '2024-01-01' AND '2024-12-31'",
                "WHERE user_id = 12345",
            ],
        }

        resp = await async_client.post(f"{BASE}/partition", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["recommended_partition_keys"], list)
        assert len(body["recommended_partition_keys"]) > 0
        assert "event_date" in body["recommended_partition_keys"]
        assert isinstance(body["sort_keys"], list)
        assert "rationale" in body

    @pytest.mark.asyncio
    async def test_partition_advice_with_platform(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should accept target platform for platform-specific advice."""
        payload = {
            "table_name": "page_views",
            "columns": [
                {"name": "view_id", "data_type": "UUID"},
                {"name": "user_id", "data_type": "BIGINT"},
                {"name": "page_url", "data_type": "VARCHAR(2048)"},
                {"name": "viewed_at", "data_type": "TIMESTAMP"},
            ],
            "row_count": 50_000_000,
            "query_patterns": [
                "WHERE viewed_at >= now() - INTERVAL 7 DAY",
            ],
            "target_platform": "ClickHouse",
        }

        resp = await async_client.post(f"{BASE}/partition", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert "ddl_snippet" in body

    @pytest.mark.asyncio
    async def test_partition_advice_invalid_row_count(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 for non-positive row_count."""
        payload = {
            "table_name": "tiny_table",
            "columns": [{"name": "id", "data_type": "INT"}],
            "row_count": 0,
            "query_patterns": [],
        }

        resp = await async_client.post(f"{BASE}/partition", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_partition_advice_warnings(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Response should include a warnings field."""
        payload = {
            "table_name": "logs",
            "columns": [
                {"name": "log_id", "data_type": "BIGINT"},
                {"name": "log_date", "data_type": "DATE"},
                {"name": "level", "data_type": "VARCHAR(10)"},
            ],
            "row_count": 100_000_000,
            "query_patterns": ["WHERE log_date = today()"],
        }

        resp = await async_client.post(f"{BASE}/partition", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("warnings"), list)
