"""
DataForge AI - Connection API tests.

Tests for all connection management endpoints: CRUD operations,
connection testing, database/table/column introspection, and error handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient

BASE = "/api/v1/connections"


# ---------------------------------------------------------------------------
# Create connection
# ---------------------------------------------------------------------------


class TestCreateConnection:
    """Tests for POST /connections."""

    @pytest.mark.asyncio
    async def test_create_postgres_connection(
        self,
        async_client: AsyncClient,
        sample_postgres_config: dict[str, Any],
    ) -> None:
        """Should create a PostgreSQL connection and return 201."""
        resp = await async_client.post(BASE, json=sample_postgres_config)

        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Production PostgreSQL"
        assert body["db_type"] == "postgresql"
        assert body["host"] == "pg.example.com"
        assert body["port"] == 5432
        assert "id" in body
        assert "password" not in body  # passwords must be redacted
        assert body["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_create_clickhouse_connection(
        self,
        async_client: AsyncClient,
        sample_clickhouse_config: dict[str, Any],
    ) -> None:
        """Should create a ClickHouse connection."""
        resp = await async_client.post(BASE, json=sample_clickhouse_config)

        assert resp.status_code == 201
        body = resp.json()
        assert body["db_type"] == "clickhouse"
        assert body["port"] == 9000

    @pytest.mark.asyncio
    async def test_create_connection_missing_required_fields(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 when required fields are missing."""
        resp = await async_client.post(BASE, json={"name": "Incomplete"})

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_connection_invalid_port(
        self,
        async_client: AsyncClient,
        sample_postgres_config: dict[str, Any],
    ) -> None:
        """Should return 422 for an out-of-range port."""
        sample_postgres_config["port"] = 99999
        resp = await async_client.post(BASE, json=sample_postgres_config)

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List connections
# ---------------------------------------------------------------------------


class TestListConnections:
    """Tests for GET /connections."""

    @pytest.mark.asyncio
    async def test_list_connections(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return the seeded connection."""
        resp = await async_client.get(BASE)

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        # Passwords must not leak
        for conn in body:
            assert "password" not in conn

    @pytest.mark.asyncio
    async def test_list_connections_filter_by_tag(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should filter connections by tag query parameter."""
        resp = await async_client.get(BASE, params={"tag": "test"})

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)


# ---------------------------------------------------------------------------
# Get connection
# ---------------------------------------------------------------------------


class TestGetConnection:
    """Tests for GET /connections/{id}."""

    @pytest.mark.asyncio
    async def test_get_existing_connection(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return the connection with matching ID."""
        resp = await async_client.get(f"{BASE}/conn-001")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "conn-001"
        assert body["name"] == "Test PostgreSQL"

    @pytest.mark.asyncio
    async def test_get_nonexistent_connection(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 404 for a missing connection."""
        resp = await async_client.get(f"{BASE}/does-not-exist")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update connection
# ---------------------------------------------------------------------------


class TestUpdateConnection:
    """Tests for PUT /connections/{id}."""

    @pytest.mark.asyncio
    async def test_update_connection_name(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should update the connection name."""
        resp = await async_client.put(
            f"{BASE}/conn-001",
            json={"name": "Renamed Connection"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Renamed Connection"

    @pytest.mark.asyncio
    async def test_update_nonexistent_connection(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 404 when updating a missing connection."""
        resp = await async_client.put(
            f"{BASE}/missing",
            json={"name": "Nope"},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete connection
# ---------------------------------------------------------------------------


class TestDeleteConnection:
    """Tests for DELETE /connections/{id}."""

    @pytest.mark.asyncio
    async def test_delete_connection(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should delete an existing connection and return a confirmation."""
        resp = await async_client.delete(f"{BASE}/conn-001")

        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_connection(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 404 when deleting a missing connection."""
        resp = await async_client.delete(f"{BASE}/no-such-id")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test connection endpoint
# ---------------------------------------------------------------------------


class TestTestConnection:
    """Tests for POST /connections/{id}/test."""

    @pytest.mark.asyncio
    async def test_connection_success(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return a successful test result with latency."""
        resp = await async_client.post(f"{BASE}/conn-001/test")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["latency_ms"] is not None
        assert body["server_version"] is not None

    @pytest.mark.asyncio
    async def test_connection_not_found(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 404 for a non-existent connection test."""
        resp = await async_client.post(f"{BASE}/missing/test")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Introspection endpoints
# ---------------------------------------------------------------------------


class TestIntrospection:
    """Tests for database/table/column introspection endpoints."""

    @pytest.mark.asyncio
    async def test_list_databases(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return databases from the mock adapter."""
        resp = await async_client.get(f"{BASE}/conn-001/databases")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert body[0]["name"] == "testdb"

    @pytest.mark.asyncio
    async def test_list_tables(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return tables from the mock adapter."""
        resp = await async_client.get(
            f"{BASE}/conn-001/tables",
            params={"database": "testdb"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        table_names = [t["table_name"] for t in body]
        assert "users" in table_names

    @pytest.mark.asyncio
    async def test_get_columns(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return column metadata from the mock adapter."""
        resp = await async_client.get(
            f"{BASE}/conn-001/tables/users/columns",
            params={"database": "testdb"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        col_names = [c["name"] for c in body]
        assert "id" in col_names
        assert "email" in col_names

    @pytest.mark.asyncio
    async def test_list_tables_missing_database_param(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 422 when required 'database' query param is missing."""
        resp = await async_client.get(f"{BASE}/conn-001/tables")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestConnectionErrorHandling:
    """Tests for error scenarios in connection endpoints."""

    @pytest.mark.asyncio
    async def test_create_connection_empty_name(
        self,
        async_client: AsyncClient,
        sample_postgres_config: dict[str, Any],
    ) -> None:
        """Should reject a connection with an empty name."""
        sample_postgres_config["name"] = ""
        resp = await async_client.post(BASE, json=sample_postgres_config)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_connection_invalid_db_type(
        self,
        async_client: AsyncClient,
        sample_postgres_config: dict[str, Any],
    ) -> None:
        """Should reject an unsupported database type."""
        sample_postgres_config["db_type"] = "oracle_legacy_v2"
        resp = await async_client.post(BASE, json=sample_postgres_config)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_introspection_on_nonexistent_connection(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 404/502 when introspecting a connection that doesn't exist."""
        resp = await async_client.get(
            f"{BASE}/ghost-conn/databases",
        )

        # The get_db_adapter dependency raises 404 for missing connections
        assert resp.status_code in (404, 502)
