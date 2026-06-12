"""
Database connection management for DataForge AI.

Provides :class:`ConnectionManager`, the central service for creating,
caching, health-checking, and tearing down async database engines that
the adapter layer uses to talk to external data sources.

The manager also owns the *internal* SQLAlchemy async engine used by the
platform itself to persist metadata, pipeline state, and lineage graphs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.exceptions import (
    ConnectionError,
    ConnectionTimeoutError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.config.settings import AppSettings
    from src.core.schemas import ConnectionConfig

logger = logging.getLogger(__name__)


class _EngineEntry:
    """Internal bookkeeping for a cached engine."""

    __slots__ = ("config", "created_at", "engine", "last_health_check", "session_factory")

    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        config: ConnectionConfig,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.config = config
        self.created_at = time.time()
        self.last_health_check: float | None = None


class ConnectionManager:
    """
    Manages multiple async SQLAlchemy engines keyed by connection ID.

    Lifecycle:
        1. Call :meth:`initialise` once at application startup to create
           the internal metadata engine.
        2. Use :meth:`get_engine` / :meth:`get_session` to obtain engines
           or sessions for user-defined connections — they are created
           lazily and cached.
        3. Call :meth:`dispose_all` during shutdown to release all pools.

    Thread / task safety:
        All public methods are safe to call concurrently from different
        ``asyncio`` tasks.  Engine creation is guarded by an
        :class:`asyncio.Lock` so that two coroutines requesting the same
        connection ID will not create duplicate engines.
    """

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._engines: dict[str, _EngineEntry] = {}
        self._lock = asyncio.Lock()

        # Internal metadata engine (created during initialise())
        self._internal_engine: AsyncEngine | None = None
        self._internal_session_factory: async_sessionmaker[AsyncSession] | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def initialise(self) -> None:
        """
        Create the internal metadata engine and verify connectivity.

        Called once during application lifespan startup.
        """
        logger.info(
            "Initialising internal metadata engine (%s)",
            self._settings.database_url.split("@")[-1],  # hide credentials
        )
        self._internal_engine = create_async_engine(
            self._settings.database_url,
            pool_size=self._settings.database_pool_size,
            max_overflow=self._settings.database_max_overflow,
            pool_timeout=self._settings.database_pool_timeout,
            pool_recycle=self._settings.database_pool_recycle,
            echo=self._settings.database_echo,
        )
        self._internal_session_factory = async_sessionmaker(
            bind=self._internal_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Verify connectivity
        try:
            async with self._internal_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Internal metadata engine connected successfully.")
        except Exception as exc:
            logger.error("Failed to connect to internal metadata database: %s", exc)
            raise ConnectionError(
                message=f"Cannot connect to internal metadata database: {exc}",
                details={"url": self._settings.database_url.split("@")[-1]},
            ) from exc

    async def dispose_all(self) -> None:
        """
        Dispose every cached engine and the internal engine.

        Called during application lifespan shutdown.
        """
        logger.info("Disposing %d cached engine(s) ...", len(self._engines))
        for cid, entry in list(self._engines.items()):
            try:
                await entry.engine.dispose()
                logger.debug("Disposed engine for connection '%s'.", cid)
            except Exception as exc:
                logger.warning("Error disposing engine '%s': %s", cid, exc)
        self._engines.clear()

        if self._internal_engine:
            await self._internal_engine.dispose()
            self._internal_engine = None
            self._internal_session_factory = None
            logger.info("Internal metadata engine disposed.")

    # ------------------------------------------------------------------ #
    # Internal engine access
    # ------------------------------------------------------------------ #

    @property
    def internal_engine(self) -> AsyncEngine:
        """Return the internal metadata engine (raises if not initialised)."""
        if self._internal_engine is None:
            raise RuntimeError("ConnectionManager has not been initialised. Call initialise() first.")
        return self._internal_engine

    @property
    def internal_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the internal session factory (raises if not initialised)."""
        if self._internal_session_factory is None:
            raise RuntimeError("ConnectionManager has not been initialised. Call initialise() first.")
        return self._internal_session_factory

    @asynccontextmanager
    async def internal_session(self) -> AsyncIterator[AsyncSession]:
        """Yield an internal AsyncSession with auto-commit / rollback."""
        factory = self.internal_session_factory
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ------------------------------------------------------------------ #
    # External connection management
    # ------------------------------------------------------------------ #

    def _build_url(self, config: ConnectionConfig) -> str:
        """
        Construct an async SQLAlchemy URL from a ConnectionConfig.

        Maps the ``db_type`` to the appropriate async driver:
          - mysql     -> mysql+aiomysql
          - postgresql -> postgresql+asyncpg
          - clickhouse -> clickhouse+asynch  (or native protocol)
          - doris     -> mysql+aiomysql  (Doris speaks MySQL protocol)
          - hive      -> hive+pyhive  (thrift)
          - sqlserver -> mssql+aioodbc
          - oracle    -> oracle+oracledb
        """
        driver_map = {
            "mysql": "mysql+aiomysql",
            "postgresql": "postgresql+asyncpg",
            "clickhouse": "clickhouse+asynch",
            "doris": "mysql+aiomysql",
            "hive": "hive+pyhive",
            "sqlserver": "mssql+aioodbc",
            "oracle": "oracle+oracledb",
        }
        driver = driver_map.get(config.db_type, config.db_type)

        # Build the URL; password may be empty for some auth modes
        url = f"{driver}://{config.username}"
        if config.password:
            url += f":{config.password}"
        url += f"@{config.host}:{config.port}"
        if config.database:
            url += f"/{config.database}"

        # Append extra query params
        if config.extra_params:
            params_str = "&".join(f"{k}={v}" for k, v in config.extra_params.items())
            url += f"?{params_str}"

        return url

    async def get_engine(self, config: ConnectionConfig) -> AsyncEngine:
        """
        Return a (cached) async engine for the given connection config.

        If no engine exists for ``config.connection_id``, one is created,
        tested, and cached.  Concurrent callers for the same ID will share
        the same engine thanks to the internal lock.
        """
        cid = config.connection_id or config.name
        entry = self._engines.get(cid)
        if entry is not None:
            return entry.engine

        async with self._lock:
            # Double-check after acquiring lock
            entry = self._engines.get(cid)
            if entry is not None:
                return entry.engine

            url = self._build_url(config)
            logger.info("Creating engine for connection '%s' (%s)", cid, config.db_type)

            try:
                engine = create_async_engine(
                    url,
                    pool_size=5,
                    max_overflow=5,
                    pool_timeout=config.connection_timeout,
                    pool_recycle=1800,
                    pool_pre_ping=True,
                    echo=self._settings.database_echo,
                )
            except Exception as exc:
                raise ConnectionError(
                    message=f"Failed to create engine for '{cid}': {exc}",
                    details={"connection_id": cid, "db_type": config.db_type},
                ) from exc

            # Quick connectivity test
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            except Exception as exc:
                await engine.dispose()
                raise ConnectionError(
                    message=f"Connection test failed for '{cid}': {exc}",
                    details={"connection_id": cid, "host": config.host, "port": config.port},
                ) from exc

            session_factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            self._engines[cid] = _EngineEntry(engine, session_factory, config)
            logger.info("Engine for '%s' created and verified.", cid)
            return engine

    async def get_session(self, config: ConnectionConfig) -> async_sessionmaker[AsyncSession]:
        """Return the session factory for a given connection config."""
        cid = config.connection_id or config.name
        entry = self._engines.get(cid)
        if entry is None:
            # Force engine creation
            await self.get_engine(config)
            entry = self._engines[cid]
        return entry.session_factory

    @asynccontextmanager
    async def session(self, config: ConnectionConfig) -> AsyncIterator[AsyncSession]:
        """Yield an external AsyncSession with auto-commit / rollback."""
        factory = await self.get_session(config)
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ------------------------------------------------------------------ #
    # Health checks
    # ------------------------------------------------------------------ #

    async def health_check(self, config: ConnectionConfig) -> dict[str, Any]:
        """
        Run a lightweight health probe against an external connection.

        Returns a dict with ``status``, ``latency_ms``, and optional
        ``server_version``.
        """
        cid = config.connection_id or config.name
        start = time.perf_counter()
        try:
            engine = await self.get_engine(config)
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                _ = result.scalar()
            latency = (time.perf_counter() - start) * 1000

            # Update bookkeeping
            entry = self._engines.get(cid)
            if entry:
                entry.last_health_check = time.time()

            return {
                "connection_id": cid,
                "status": "healthy",
                "latency_ms": round(latency, 2),
            }
        except TimeoutError:
            raise ConnectionTimeoutError(
                message=f"Health check timed out for '{cid}'.",
                details={"connection_id": cid, "timeout_s": config.connection_timeout},
            ) from None
        except Exception as exc:
            return {
                "connection_id": cid,
                "status": "unhealthy",
                "error": str(exc),
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            }

    async def check_all(self) -> dict[str, dict[str, Any]]:
        """Health-check every cached connection and return a summary."""
        results: dict[str, dict[str, Any]] = {}
        for cid, entry in self._engines.items():
            results[cid] = await self.health_check(entry.config)
        return results

    # ------------------------------------------------------------------ #
    # Connection removal
    # ------------------------------------------------------------------ #

    async def remove_engine(self, connection_id: str) -> None:
        """Dispose and remove the engine for a specific connection."""
        async with self._lock:
            entry = self._engines.pop(connection_id, None)
            if entry:
                await entry.engine.dispose()
                logger.info("Removed and disposed engine for '%s'.", connection_id)

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #

    def list_connections(self) -> dict[str, dict[str, Any]]:
        """Return a summary of all cached connections (no secrets)."""
        summary: dict[str, dict[str, Any]] = {}
        for cid, entry in self._engines.items():
            summary[cid] = {
                "db_type": entry.config.db_type,
                "host": entry.config.host,
                "port": entry.config.port,
                "database": entry.config.database,
                "created_at": entry.created_at,
                "last_health_check": entry.last_health_check,
                "pool": {
                    "size": entry.engine.pool.size(),
                    "checked_in": entry.engine.pool.checkedin(),
                    "checked_out": entry.engine.pool.checkedout(),
                    "overflow": entry.engine.pool.overflow(),
                },
            }
        return summary
