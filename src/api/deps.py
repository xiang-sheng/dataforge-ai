"""
DataForge AI - API dependency injection module.

Provides FastAPI dependency functions for application-wide services
including settings, connection management, AI providers, and database adapters.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status

if TYPE_CHECKING:
    from src.ai.provider import BaseAIProvider
    from src.config.settings import AppSettings
    from src.core.database import ConnectionManager
    from src.db.base import AbstractBaseAdapter


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@lru_cache
def get_settings() -> AppSettings:
    """Return the cached application settings singleton.

    The ``lru_cache`` decorator ensures the settings object is created only
    once and reused across all requests.

    Returns:
        AppSettings: The application configuration object.
    """
    from src.config.settings import get_settings as _get_settings

    return _get_settings()


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------

def get_connection_manager() -> ConnectionManager:
    """Return the application-wide ConnectionManager singleton.

    Delegates to ``src.main.get_connection_manager()`` which is initialised
    during the application lifespan with the correct ``AppSettings``.

    Returns:
        ConnectionManager: The global connection manager instance.

    Raises:
        RuntimeError: If called before the application lifespan has started.
    """
    from src.main import get_connection_manager as _get_cm

    return _get_cm()


# ---------------------------------------------------------------------------
# AI Provider
# ---------------------------------------------------------------------------

@lru_cache
def get_ai_provider() -> BaseAIProvider:
    """Return the cached AI-provider singleton.

    The AI provider wraps the underlying LLM backend and exposes a uniform
    interface for SQL generation, modeling suggestions, and code explanation.

    Returns:
        BaseAIProvider: The global AI provider instance.
    """
    settings = get_settings()

    return settings.get_ai_provider()


# ---------------------------------------------------------------------------
# Database Adapter (path-dependent)
# ---------------------------------------------------------------------------

async def get_db_adapter(
    connection_id: str,
    manager: ConnectionManager = Depends(get_connection_manager),
) -> AbstractBaseAdapter:
    """Resolve and return a live database adapter for the given *connection_id*.

    This dependency validates that the requested connection exists and that it
    can be reached before handing the adapter to the route handler.

    Args:
        connection_id: Unique identifier of the database connection.
        manager: Injected connection-manager instance.

    Raises:
        HTTPException(404): If the connection does not exist.
        HTTPException(502): If the connection cannot be established.

    Returns:
        AbstractBaseAdapter: A ready-to-use database adapter.
    """
    connection = await manager.get(connection_id)

    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    try:
        adapter = await manager.get_adapter(connection_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Failed to establish connection '{connection_id}': {exc}"
            ),
        ) from exc

    return adapter
