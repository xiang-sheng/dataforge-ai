# -*- coding: utf-8 -*-
"""
FastAPI application entry point for DataForge AI.

This module creates and configures the FastAPI application instance,
registers middleware, routers, and the lifespan handler that manages
startup / shutdown of shared resources (connection pool, Redis).

Run with::

    uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src import __version__
from src.config.settings import get_settings
from src.core.database import ConnectionManager
from src.core.exceptions import DataForgeError

# ------------------------------------------------------------------ #
# Logging
# ------------------------------------------------------------------ #

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("src.main")


# ------------------------------------------------------------------ #
# Shared resource holders (populated during lifespan)
# ------------------------------------------------------------------ #

_connection_manager: ConnectionManager | None = None
_redis_pool: aioredis.Redis | None = None


def get_connection_manager() -> ConnectionManager:
    """Return the application-wide ConnectionManager (raises if not ready)."""
    if _connection_manager is None:
        raise RuntimeError("ConnectionManager not initialised. Is the app running?")
    return _connection_manager


def get_redis() -> aioredis.Redis:
    """Return the application-wide Redis client (raises if not ready)."""
    if _redis_pool is None:
        raise RuntimeError("Redis client not initialised. Is the app running?")
    return _redis_pool


# ------------------------------------------------------------------ #
# Lifespan handler
# ------------------------------------------------------------------ #

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan handler — runs once on startup and once on shutdown.

    Startup:
        1. Initialise the internal ConnectionManager (metadata DB pool).
        2. Create the Redis connection pool.

    Shutdown:
        1. Dispose all cached database engines.
        2. Close the Redis connection pool.
    """
    global _connection_manager, _redis_pool

    # --- Startup ---
    logger.info("Starting DataForge AI v%s ...", __version__)

    # 1. Database connection manager
    _connection_manager = ConnectionManager(settings)
    await _connection_manager.initialise()
    logger.info("Connection manager ready.")

    # 2. Redis
    try:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        # Verify connectivity
        await _redis_pool.ping()
        logger.info("Redis connected (%s).", settings.redis_url.split("@")[-1])
    except Exception as exc:
        logger.warning("Redis connection failed (%s). Continuing without cache.", exc)
        _redis_pool = None

    logger.info("DataForge AI startup complete.")

    yield  # --- App is running ---

    # --- Shutdown ---
    logger.info("Shutting down DataForge AI ...")

    if _connection_manager:
        await _connection_manager.dispose_all()
        _connection_manager = None

    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None

    logger.info("Shutdown complete.")


# ------------------------------------------------------------------ #
# FastAPI application
# ------------------------------------------------------------------ #

app = FastAPI(
    title="DataForge AI",
    description=(
        "AI-powered data warehouse construction platform.  "
        "Provides APIs for multi-engine metadata discovery, AI-assisted SQL "
        "generation, data modelling, lineage analysis, and ETL pipeline "
        "orchestration."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ------------------------------------------------------------------ #
# Middleware
# ------------------------------------------------------------------ #

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
)


@app.middleware("http")
async def add_timing_header(request: Request, call_next) -> Response:
    """Inject response headers for request tracing and latency monitoring."""
    import uuid

    request_id = str(uuid.uuid4())
    start = time.perf_counter()

    response: Response = await call_next(request)

    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
    return response


# ------------------------------------------------------------------ #
# Exception handlers
# ------------------------------------------------------------------ #

@app.exception_handler(DataForgeError)
async def dataforge_error_handler(request: Request, exc: DataForgeError) -> JSONResponse:
    """Convert DataForgeError instances into structured JSON error responses."""
    return JSONResponse(
        status_code=exc.default_status_code,
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected errors (hides stack traces in production)."""
    logger.exception("Unhandled exception: %s", exc)
    if settings.debug:
        detail = {"message": str(exc), "type": type(exc).__name__}
    else:
        detail = {"message": "Internal server error."}
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", **detail}},
    )


# ------------------------------------------------------------------ #
# Health check
# ------------------------------------------------------------------ #

@app.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description="Returns the application health status and basic connectivity info.",
)
async def health_check() -> dict:
    """
    Lightweight health endpoint used by load balancers, Kubernetes probes,
    and monitoring systems.
    """
    health: dict = {
        "status": "healthy",
        "version": __version__,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": {},
    }

    # Internal database
    try:
        cm = get_connection_manager()
        async with cm.internal_engine.connect() as conn:
            from sqlalchemy import text as sa_text
            await conn.execute(sa_text("SELECT 1"))
        health["checks"]["database"] = {"status": "healthy"}
    except Exception as exc:
        health["checks"]["database"] = {"status": "unhealthy", "error": str(exc)}
        health["status"] = "degraded"

    # Redis
    try:
        r = get_redis()
        await r.ping()
        health["checks"]["redis"] = {"status": "healthy"}
    except Exception as exc:
        health["checks"]["redis"] = {"status": "unhealthy", "error": str(exc)}
        # Redis being down is not critical — mark as degraded, not unhealthy
        if health["status"] == "healthy":
            health["status"] = "degraded"

    # Active connections
    try:
        cm = get_connection_manager()
        connections = cm.list_connections()
        health["checks"]["active_connections"] = len(connections)
    except Exception:
        health["checks"]["active_connections"] = 0

    return health


# ------------------------------------------------------------------ #
# API routers placeholder
# ------------------------------------------------------------------ #
#
# In a full implementation you would import and include routers here:
#
# from src.api.connections import router as connections_router
# from src.api.tables import router as tables_router
# from src.api.sql_generation import router as sql_gen_router
# from src.api.modeling import router as modeling_router
# from src.api.lineage import router as lineage_router
# from src.api.etl import router as etl_router
#
# app.include_router(connections_router, prefix=settings.api_prefix, tags=["Connections"])
# app.include_router(tables_router,      prefix=settings.api_prefix, tags=["Tables"])
# app.include_router(sql_gen_router,     prefix=settings.api_prefix, tags=["SQL Generation"])
# app.include_router(modeling_router,    prefix=settings.api_prefix, tags=["Data Modeling"])
# app.include_router(lineage_router,     prefix=settings.api_prefix, tags=["Lineage"])
# app.include_router(etl_router,         prefix=settings.api_prefix, tags=["ETL"])
#
# ------------------------------------------------------------------ #


# ------------------------------------------------------------------ #
# Root endpoint
# ------------------------------------------------------------------ #

@app.get("/", tags=["System"], include_in_schema=False)
async def root() -> dict:
    return {
        "name": "DataForge AI",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }
