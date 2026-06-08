"""
DataForge AI - Main API router.

Aggregates all sub-routers (connections, warehouse, DDL builder, modeling,
SQL, lineage, ETL) under a single ``APIRouter`` that the application mounts
at ``/api/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.routes import agent, connection, ddl_builder, etl, lineage, modeling, sql, warehouse

api_router = APIRouter(
    prefix="/api/v1",
    tags=["DataForge AI"],
)

# -- Sub-routers --------------------------------------------------------------

api_router.include_router(
    connection.router,
    prefix="/connections",
    tags=["Connections"],
)

api_router.include_router(
    warehouse.router,
    prefix="/warehouse",
    tags=["Warehouse"],
)

api_router.include_router(
    ddl_builder.router,
    prefix="/ddl",
    tags=["DDL Builder"],
)

api_router.include_router(
    modeling.router,
    prefix="/modeling",
    tags=["Modeling"],
)

api_router.include_router(
    sql.router,
    prefix="/sql",
    tags=["SQL"],
)

api_router.include_router(
    lineage.router,
    prefix="/lineage",
    tags=["Lineage"],
)

api_router.include_router(
    etl.router,
    prefix="/etl",
    tags=["ETL"],
)

api_router.include_router(
    agent.router,
    prefix="/agent",
    tags=["AI Agent"],
)
