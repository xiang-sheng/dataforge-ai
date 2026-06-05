"""
DataForge AI - Shared pytest fixtures.

Provides reusable fixtures for the async HTTP client, mock services,
sample configurations, and schema data used across all test modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.router import api_router
from src.api.deps import get_settings, get_connection_manager, get_ai_provider, get_db_adapter

from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def _create_test_app() -> FastAPI:
    """Create a FastAPI app wired with the main API router."""
    app = FastAPI(title="DataForge AI Test", version="0.0.0-test")
    app.include_router(api_router)
    return app


# ---------------------------------------------------------------------------
# Mock Settings
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_settings() -> MagicMock:
    """Return a mock ``AppSettings`` object with sensible defaults."""
    settings = MagicMock()
    settings.app_name = "DataForge AI"
    settings.debug = True
    settings.database_url = "sqlite+aiosqlite:///./test.db"
    settings.ai_provider = "openai"
    settings.ai_api_key = "sk-test-key-000"
    settings.ai_model = "gpt-4"
    settings.ai_base_url = "https://api.openai.com/v1"
    settings.log_level = "DEBUG"
    settings.secret_key = "test-secret-key"
    settings.allowed_origins = ["*"]
    return settings


# ---------------------------------------------------------------------------
# Mock Connection Manager
# ---------------------------------------------------------------------------


def _make_sample_connection(
    conn_id: str = "conn-001",
    name: str = "Test PostgreSQL",
    db_type: str = "postgresql",
) -> dict[str, Any]:
    """Build a sample connection dict."""
    now = datetime.now(timezone.utc)
    return {
        "id": conn_id,
        "name": name,
        "db_type": db_type,
        "host": "localhost",
        "port": 5432,
        "username": "testuser",
        "password": "testpass",
        "default_database": "testdb",
        "status": "active",
        "extra_params": {},
        "tags": ["test"],
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture()
def mock_connection_manager() -> AsyncMock:
    """Return an ``AsyncMock`` that simulates the ``ConnectionManager``."""
    manager = AsyncMock()

    # Internal store
    _store: dict[str, dict[str, Any]] = {
        "conn-001": _make_sample_connection(),
    }
    _pipeline_store: dict[str, dict[str, Any]] = {}

    async def _get(conn_id: str) -> dict[str, Any] | None:
        return _store.get(conn_id)

    async def _save(conn_id: str, data: dict[str, Any]) -> None:
        _store[conn_id] = data

    async def _delete(conn_id: str) -> None:
        _store.pop(conn_id, None)

    async def _list_all(
        db_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        results = list(_store.values())
        if db_type:
            results = [c for c in results if c.get("db_type") == db_type]
        if tag:
            results = [c for c in results if tag in c.get("tags", [])]
        return results

    async def _get_adapter(connection_id: str) -> AsyncMock:
        adapter = AsyncMock()
        adapter.test = AsyncMock(return_value={
            "success": True,
            "latency_ms": 12.5,
            "server_version": "15.3",
            "message": "Connection successful.",
        })
        adapter.list_databases = AsyncMock(return_value=[
            {"name": "testdb", "size_mb": 256.0, "table_count": 15},
            {"name": "analytics", "size_mb": 1024.0, "table_count": 42},
        ])
        adapter.list_tables = AsyncMock(return_value=[
            {
                "schema_name": "public",
                "table_name": "users",
                "table_type": "BASE TABLE",
                "row_count": 10000,
                "size_mb": 12.5,
                "comment": "User accounts table",
            },
            {
                "schema_name": "public",
                "table_name": "orders",
                "table_type": "BASE TABLE",
                "row_count": 50000,
                "size_mb": 48.0,
                "comment": "Customer orders",
            },
        ])
        adapter.get_columns = AsyncMock(return_value=[
            {
                "name": "id",
                "data_type": "BIGINT",
                "nullable": False,
                "is_primary_key": True,
                "default_value": None,
                "comment": "Primary key",
                "ordinal_position": 1,
            },
            {
                "name": "name",
                "data_type": "VARCHAR(255)",
                "nullable": False,
                "is_primary_key": False,
                "default_value": None,
                "comment": "User full name",
                "ordinal_position": 2,
            },
            {
                "name": "email",
                "data_type": "VARCHAR(255)",
                "nullable": True,
                "is_primary_key": False,
                "default_value": None,
                "comment": "Email address",
                "ordinal_position": 3,
            },
            {
                "name": "created_at",
                "data_type": "TIMESTAMP",
                "nullable": False,
                "is_primary_key": False,
                "default_value": "CURRENT_TIMESTAMP",
                "comment": "Record creation time",
                "ordinal_position": 4,
            },
        ])
        adapter.execute = AsyncMock(return_value=None)
        adapter.execute_query = AsyncMock(return_value={
            "columns": ["id", "name", "email"],
            "rows": [[1, "Alice", "alice@example.com"], [2, "Bob", "bob@example.com"]],
            "row_count": 2,
            "execution_time_ms": 15.3,
            "truncated": False,
            "statement_type": "SELECT",
        })
        adapter.get_table_lineage = AsyncMock(return_value={
            "upstream": [
                {"source_table": "raw.events", "target_table": "dwd.user_activity", "transformation": "ETL"},
            ],
            "downstream": [
                {"source_table": "dwd.user_activity", "target_table": "dws.daily_active_users", "transformation": "Aggregation"},
            ],
        })
        adapter.get_lineage_graph = AsyncMock(return_value={
            "nodes": [
                {"id": "raw.events", "table_name": "raw.events", "node_type": "table", "layer": "ods"},
                {"id": "dwd.user_activity", "table_name": "dwd.user_activity", "node_type": "table", "layer": "dwd"},
            ],
            "edges": [
                {"source_id": "raw.events", "target_id": "dwd.user_activity", "edge_type": "data_flow"},
            ],
            "depth": 2,
        })
        adapter.get_column_lineage = AsyncMock(return_value={
            "upstream": [
                {
                    "source_table": "raw.events",
                    "source_column": "user_id",
                    "target_table": "dwd.user_activity",
                    "target_column": "user_id",
                    "transformation": None,
                },
            ],
            "downstream": [
                {
                    "source_table": "dwd.user_activity",
                    "source_column": "user_id",
                    "target_table": "dws.daily_active_users",
                    "target_column": "user_id",
                    "transformation": "COUNT(DISTINCT user_id)",
                },
            ],
        })
        adapter.get_impact_analysis = AsyncMock(return_value={
            "direct_downstream": ["dwd.user_activity"],
            "indirect_downstream": ["dws.daily_active_users", "ads.dashboard"],
            "total_affected_count": 3,
            "affected_pipelines": ["etl_events_to_dwd", "agg_daily_users"],
            "risk_level": "medium",
            "recommendations": [
                "Notify downstream pipeline owners before making changes.",
                "Run a dry-run migration first.",
            ],
        })
        return adapter

    async def _save_pipeline(pipeline_id: str, data: dict[str, Any]) -> None:
        _pipeline_store[pipeline_id] = data

    async def _get_pipeline(pipeline_id: str) -> dict[str, Any] | None:
        return _pipeline_store.get(pipeline_id)

    async def _list_pipelines(
        status: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        results = list(_pipeline_store.values())
        if status:
            results = [p for p in results if p.get("status") == status]
        if tag:
            results = [p for p in results if tag in p.get("tags", [])]
        return results

    manager.get = AsyncMock(side_effect=_get)
    manager.save = AsyncMock(side_effect=_save)
    manager.delete = AsyncMock(side_effect=_delete)
    manager.list_all = AsyncMock(side_effect=_list_all)
    manager.get_adapter = AsyncMock(side_effect=_get_adapter)
    manager.save_pipeline = AsyncMock(side_effect=_save_pipeline)
    manager.get_pipeline = AsyncMock(side_effect=_get_pipeline)
    manager.list_pipelines = AsyncMock(side_effect=_list_pipelines)

    return manager


# ---------------------------------------------------------------------------
# Mock AI Provider
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_ai_provider() -> AsyncMock:
    """Return an ``AsyncMock`` that simulates the ``AIProvider``."""
    provider = AsyncMock()

    provider.generate_sql = AsyncMock(return_value={
        "sql": "SELECT id, name, email FROM users WHERE created_at >= '2024-01-01' ORDER BY created_at DESC LIMIT 100;",
        "explanation": "This query selects the 100 most recently created users from the start of 2024.",
        "confidence": 0.92,
        "warnings": [],
        "alternative_queries": [
            "SELECT * FROM users WHERE created_at >= '2024-01-01' ORDER BY created_at DESC LIMIT 100;",
        ],
    })

    provider.explain_sql = AsyncMock(return_value={
        "summary": "Selects the 100 most recent users created in 2024.",
        "step_by_step": [
            "1. Scans the 'users' table.",
            "2. Filters rows where created_at >= '2024-01-01'.",
            "3. Sorts by created_at in descending order.",
            "4. Returns the first 100 rows.",
        ],
        "tables_used": ["users"],
        "estimated_complexity": "low",
        "performance_notes": ["Consider an index on (created_at DESC) for better performance."],
    })

    provider.optimize_sql = AsyncMock(return_value={
        "optimized_sql": "SELECT id, name, email FROM users WHERE created_at >= '2024-01-01' ORDER BY created_at DESC LIMIT 100;",
        "changes": [
            "Removed unnecessary JOIN with 'profiles' table.",
            "Added LIMIT clause to restrict result set.",
        ],
        "estimated_improvement": "~40% faster due to eliminated JOIN.",
        "warnings": ["Ensure 'profiles' data is not needed downstream."],
    })

    provider.translate_sql = AsyncMock(return_value={
        "translated_sql": "SELECT id, name, email FROM users WHERE created_at >= '2024-01-01' ORDER BY created_at DESC LIMIT 100;",
        "translation_notes": [
            "Replaced ClickHouse toDate() with PostgreSQL DATE casting.",
            "Changed LIMIT syntax.",
        ],
        "unsupported_features": [],
    })

    provider.design_warehouse = AsyncMock(return_value={
        "design_sql": [
            "CREATE TABLE dwd.dwd_user_activity (user_id BIGINT, event_type VARCHAR(50), event_time TIMESTAMP) ENGINE = MergeTree() PARTITION BY toYYYYMM(event_time);",
        ],
        "layer_rationale": "User activity events are stored in DWD as they represent cleaned, standardised fact data.",
        "naming_conventions": {"events": "dwd_user_activity"},
        "recommendations": ["Add a date partition for efficient time-range queries."],
    })

    provider.generate_migration = AsyncMock(return_value={
        "migration_sql": "ALTER TABLE users ADD COLUMN phone VARCHAR(20);",
        "rollback_sql": "ALTER TABLE users DROP COLUMN phone;",
        "warnings": ["Adding a nullable column is safe for most engines."],
        "estimated_impact": "Low - additive change, no data loss.",
    })

    provider.suggest_modeling = AsyncMock(return_value={
        "suggestions": [
            {
                "category": "normalization",
                "description": "Split the 'orders' table into 'orders' and 'order_items' for better normalisation.",
                "affected_tables": ["orders"],
                "confidence": 0.85,
            },
        ],
        "proposed_ddl": [
            "CREATE TABLE orders (id BIGINT PRIMARY KEY, customer_id BIGINT, total DECIMAL(12,2));",
            "CREATE TABLE order_items (id BIGINT PRIMARY KEY, order_id BIGINT, product_id BIGINT, qty INT, price DECIMAL(12,2));",
        ],
        "summary": "Recommend splitting orders for 3NF compliance.",
    })

    provider.design_dimensional_model = AsyncMock(return_value={
        "fact_tables": [
            {
                "name": "fact_orders",
                "grain": "one row per order line",
                "measures": [{"name": "quantity", "type": "SUM"}, {"name": "amount", "type": "SUM"}],
                "foreign_keys": [{"column": "dim_date_id", "ref_table": "dim_date"}],
                "ddl": "CREATE TABLE fact_orders (...);",
            },
        ],
        "dimension_tables": [
            {
                "name": "dim_date",
                "attributes": [{"name": "date_key", "type": "INT"}, {"name": "year", "type": "INT"}],
                "surrogate_key": "date_key",
                "ddl": "CREATE TABLE dim_date (...);",
            },
        ],
        "rationale": "Star schema chosen for simplicity and query performance.",
        "warnings": [],
    })

    provider.review_model = AsyncMock(return_value={
        "findings": [
            {
                "severity": "warning",
                "table": "users",
                "message": "Column 'email' lacks a unique constraint.",
                "recommendation": "Add a UNIQUE constraint on the email column.",
            },
        ],
        "score": 78.0,
        "summary": "Model is generally well-structured with minor improvements needed.",
    })

    provider.get_partition_advice = AsyncMock(return_value={
        "recommended_partition_keys": ["event_date"],
        "sort_keys": ["user_id", "event_date"],
        "rationale": "Partitioning by event_date aligns with the most common query filter pattern.",
        "estimated_partition_count": 365,
        "warnings": ["Avoid partitioning by high-cardinality columns."],
        "ddl_snippet": "PARTITION BY toYYYYMMDD(event_date)",
    })

    provider.analyze_lineage = AsyncMock(return_value={
        "source_tables": ["raw.events", "raw.users"],
        "target_tables": ["dwd.user_activity"],
        "edges": [
            {
                "source_id": "raw.events",
                "target_id": "dwd.user_activity",
                "edge_type": "data_flow",
            },
            {
                "source_id": "raw.users",
                "target_id": "dwd.user_activity",
                "edge_type": "data_flow",
            },
        ],
        "column_mappings": [
            {
                "source_table": "raw.events",
                "source_column": "user_id",
                "target_table": "dwd.user_activity",
                "target_column": "user_id",
                "transformation": None,
            },
        ],
    })

    provider.generate_airflow_dag = AsyncMock(return_value={
        "dag_code": "from airflow import DAG\n# generated DAG code",
        "dag_id": "dataforge_etl_pipeline_001",
        "instructions": [
            "Place the generated file in your Airflow dags/ directory.",
            "Ensure the required connections are configured in Airflow.",
        ],
        "warnings": [],
    })

    return provider


# ---------------------------------------------------------------------------
# Sample connection configs
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_postgres_config() -> dict[str, Any]:
    """Sample PostgreSQL connection creation payload."""
    return {
        "name": "Production PostgreSQL",
        "db_type": "postgresql",
        "host": "pg.example.com",
        "port": 5432,
        "username": "analytics_user",
        "password": "super-secret-pw",
        "default_database": "production",
        "extra_params": {"sslmode": "require"},
        "tags": ["production", "postgres"],
    }


@pytest.fixture()
def sample_clickhouse_config() -> dict[str, Any]:
    """Sample ClickHouse connection creation payload."""
    return {
        "name": "Analytics ClickHouse",
        "db_type": "clickhouse",
        "host": "ch.example.com",
        "port": 9000,
        "username": "writer",
        "password": "ch-secret-pw",
        "default_database": "analytics",
        "extra_params": {"secure": True},
        "tags": ["production", "clickhouse"],
    }


@pytest.fixture()
def sample_mysql_config() -> dict[str, Any]:
    """Sample MySQL connection creation payload."""
    return {
        "name": "Legacy MySQL",
        "db_type": "mysql",
        "host": "mysql.example.com",
        "port": 3306,
        "username": "reader",
        "password": "mysql-secret",
        "default_database": "legacy_app",
        "extra_params": {},
        "tags": ["legacy", "mysql"],
    }


@pytest.fixture()
def sample_snowflake_config() -> dict[str, Any]:
    """Sample Snowflake connection creation payload."""
    return {
        "name": "Data Warehouse Snowflake",
        "db_type": "snowflake",
        "host": "abc123.snowflakecomputing.com",
        "port": 443,
        "username": "etl_user",
        "password": "sf-secret",
        "default_database": "ANALYTICS_DB",
        "extra_params": {"warehouse": "COMPUTE_WH", "role": "ETL_ROLE"},
        "tags": ["warehouse", "snowflake"],
    }


# ---------------------------------------------------------------------------
# Sample table schema
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_table_schema() -> dict[str, Any]:
    """A representative table schema for modelling tests."""
    return {
        "tables": [
            {
                "table_name": "orders",
                "columns": [
                    {"name": "id", "data_type": "BIGINT", "nullable": False, "is_primary_key": True},
                    {"name": "customer_id", "data_type": "BIGINT", "nullable": False},
                    {"name": "product_id", "data_type": "BIGINT", "nullable": False},
                    {"name": "quantity", "data_type": "INT", "nullable": False},
                    {"name": "unit_price", "data_type": "DECIMAL(12,2)", "nullable": False},
                    {"name": "order_date", "data_type": "DATE", "nullable": False},
                    {"name": "status", "data_type": "VARCHAR(20)", "nullable": False},
                ],
                "primary_keys": ["id"],
                "foreign_keys": [
                    {"column": "customer_id", "ref_table": "customers", "ref_column": "id"},
                    {"column": "product_id", "ref_table": "products", "ref_column": "id"},
                ],
                "sample_row_count": 500000,
                "comment": "E-commerce order line items",
            },
            {
                "table_name": "customers",
                "columns": [
                    {"name": "id", "data_type": "BIGINT", "nullable": False, "is_primary_key": True},
                    {"name": "name", "data_type": "VARCHAR(255)", "nullable": False},
                    {"name": "email", "data_type": "VARCHAR(255)", "nullable": True},
                    {"name": "region", "data_type": "VARCHAR(50)", "nullable": True},
                    {"name": "created_at", "data_type": "TIMESTAMP", "nullable": False},
                ],
                "primary_keys": ["id"],
                "foreign_keys": [],
                "sample_row_count": 50000,
                "comment": "Customer master data",
            },
        ],
        "business_context": "E-commerce platform order processing and customer management.",
    }


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_client(
    mock_settings: MagicMock,
    mock_connection_manager: AsyncMock,
    mock_ai_provider: AsyncMock,
) -> AsyncGenerator[AsyncClient, None]:
    """Yield an ``httpx.AsyncClient`` wired to the test app with all
    dependencies overridden by mocks.
    """
    app = _create_test_app()

    # Override dependencies
    app.dependency_overrides[get_settings] = lambda: mock_settings
    app.dependency_overrides[get_connection_manager] = lambda: mock_connection_manager
    app.dependency_overrides[get_ai_provider] = lambda: mock_ai_provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
