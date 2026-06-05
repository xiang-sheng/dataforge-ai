# -*- coding: utf-8 -*-
"""
Adapter factory for DataForge AI.

Provides :class:`AdapterFactory`, the central registry that maps a
:class:`DatabaseType` to its concrete :class:`AbstractBaseAdapter`
implementation.  New adapters can be registered at runtime or discovered
automatically from the ``src.db`` package.

Usage::

    from src.db.factory import AdapterFactory
    from src.core.schemas import ConnectionConfig

    config = ConnectionConfig(db_type="mysql", host="...", ...)
    adapter = await AdapterFactory.create_adapter(config, engine)
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Dict, Optional, Set, Type

from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.exceptions import ConnectionError
from src.core.schemas import ConnectionConfig, DatabaseType
from src.db.base import AbstractBaseAdapter

logger = logging.getLogger(__name__)


class AdapterFactory:
    """
    Registry-based factory that instantiates the correct database adapter
    for a given :class:`ConnectionConfig`.

    Class-level state (shared across all instances):
        - ``_registry``: maps ``db_type`` string -> adapter class
        - ``_loaded_modules``: tracks which modules have been scanned
    """

    _registry: Dict[str, Type[AbstractBaseAdapter]] = {}
    _loaded_modules: Set[str] = set()

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    @classmethod
    def register(cls, db_type: str, adapter_class: Type[AbstractBaseAdapter]) -> None:
        """
        Register an adapter class for the given ``db_type`` key.

        Parameters
        ----------
        db_type:
            Lower-case engine identifier (must match ``DatabaseType`` values).
        adapter_class:
            A concrete subclass of :class:`AbstractBaseAdapter`.

        Raises
        ------
        TypeError
            If ``adapter_class`` is not a subclass of ``AbstractBaseAdapter``.
        """
        if not (inspect.isclass(adapter_class) and issubclass(adapter_class, AbstractBaseAdapter)):
            raise TypeError(
                f"adapter_class must be a subclass of AbstractBaseAdapter, "
                f"got {adapter_class!r}"
            )
        cls._registry[db_type.lower()] = adapter_class
        logger.debug("Registered adapter '%s' -> %s", db_type, adapter_class.__name__)

    @classmethod
    def unregister(cls, db_type: str) -> None:
        """Remove the adapter registration for ``db_type``."""
        cls._registry.pop(db_type.lower(), None)

    # ------------------------------------------------------------------ #
    # Auto-discovery
    # ------------------------------------------------------------------ #

    @classmethod
    def discover_adapters(cls, package_path: Optional[str] = None) -> None:
        """
        Scan the ``src.db`` package (or a custom path) for modules whose
        names end with ``_adapter`` and register any
        :class:`AbstractBaseAdapter` subclasses they contain.

        This makes it trivial to add a new engine: drop a
        ``newengine_adapter.py`` file into ``src/db/`` and it will be
        picked up automatically.

        Parameters
        ----------
        package_path:
            Filesystem path to the package directory.  Defaults to the
            directory containing this module.
        """
        import src.db as db_package

        if package_path is None:
            package_path = db_package.__path__[0]  # type: ignore[attr-defined]

        if package_path in cls._loaded_modules:
            return  # Already scanned

        logger.info("Auto-discovering adapters in '%s' ...", package_path)

        for module_info in pkgutil.iter_modules([package_path]):
            name = module_info.name
            if not name.endswith("_adapter"):
                continue

            full_module_name = f"src.db.{name}"
            try:
                module = importlib.import_module(full_module_name)
            except ImportError as exc:
                logger.warning(
                    "Could not import adapter module '%s': %s", full_module_name, exc
                )
                continue

            # Walk all classes in the module and register concrete adapters
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    inspect.isclass(attr)
                    and issubclass(attr, AbstractBaseAdapter)
                    and attr is not AbstractBaseAdapter
                    and not inspect.isabstract(attr)
                ):
                    db_type = getattr(attr, "db_type", "")
                    if db_type:
                        cls.register(db_type, attr)
                        logger.info(
                            "Discovered adapter: %s (db_type='%s')",
                            attr.__name__,
                            db_type,
                        )

        cls._loaded_modules.add(package_path)

    # ------------------------------------------------------------------ #
    # Adapter creation
    # ------------------------------------------------------------------ #

    @classmethod
    async def create_adapter(
        cls,
        config: ConnectionConfig,
        engine: AsyncEngine,
    ) -> AbstractBaseAdapter:
        """
        Instantiate and return the appropriate adapter for ``config``.

        If the adapter registry has not been populated yet, auto-discovery
        is triggered lazily on the first call.

        Parameters
        ----------
        config:
            Connection configuration (must include a valid ``db_type``).
        engine:
            Pre-created SQLAlchemy async engine from the ConnectionManager.

        Returns
        -------
        AbstractBaseAdapter
            A fully initialised adapter instance.  ``connect()`` has
            **not** been called — the caller decides whether to invoke it.

        Raises
        ------
        ConnectionError
            If no adapter is registered for the requested ``db_type``.
        """
        # Ensure registry is populated
        if not cls._registry:
            cls.discover_adapters()

        db_type = config.db_type if isinstance(config.db_type, str) else config.db_type.value
        db_type_lower = db_type.lower()

        adapter_class = cls._registry.get(db_type_lower)
        if adapter_class is None:
            available = ", ".join(sorted(cls._registry.keys()))
            raise ConnectionError(
                message=f"No adapter registered for database type '{db_type}'.",
                code="UNSUPPORTED_DB_TYPE",
                details={
                    "requested_type": db_type,
                    "available_types": list(cls._registry.keys()),
                },
            )

        logger.info(
            "Creating adapter '%s' for %s:%s (%s)",
            adapter_class.__name__,
            config.host,
            config.port,
            db_type,
        )

        try:
            adapter = adapter_class(config=config, engine=engine)
        except Exception as exc:
            raise ConnectionError(
                message=f"Failed to instantiate adapter for '{db_type}': {exc}",
                details={"db_type": db_type, "host": config.host, "port": config.port},
            ) from exc

        return adapter

    # ------------------------------------------------------------------ #
    # Introspection helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def list_available_adapters(cls) -> Dict[str, str]:
        """
        Return a mapping of ``db_type`` -> adapter class name for all
        registered adapters.  Triggers discovery if the registry is empty.
        """
        if not cls._registry:
            cls.discover_adapters()
        return {
            db_type: adapter_cls.__name__
            for db_type, adapter_cls in sorted(cls._registry.items())
        }

    @classmethod
    def is_supported(cls, db_type: str) -> bool:
        """Check whether an adapter exists for the given ``db_type``."""
        if not cls._registry:
            cls.discover_adapters()
        return db_type.lower() in cls._registry

    @classmethod
    def get_adapter_class(cls, db_type: str) -> Optional[Type[AbstractBaseAdapter]]:
        """Return the adapter class for ``db_type``, or ``None``."""
        if not cls._registry:
            cls.discover_adapters()
        return cls._registry.get(db_type.lower())


# ------------------------------------------------------------------ #
# Convenience: pre-register all built-in adapters so that users who
# import AdapterFactory directly (without triggering discovery) still
# get a working registry.
# ------------------------------------------------------------------ #

def _register_builtins() -> None:
    """Import and register every built-in adapter eagerly."""
    from src.db.clickhouse_adapter import ClickHouseAdapter
    from src.db.doris_adapter import DorisAdapter
    from src.db.hive_adapter import HiveAdapter
    from src.db.mysql_adapter import MySQLAdapter
    from src.db.oracle_adapter import OracleAdapter
    from src.db.postgres_adapter import PostgreSQLAdapter
    from src.db.sqlserver_adapter import SQLServerAdapter

    AdapterFactory.register("mysql", MySQLAdapter)
    AdapterFactory.register("postgresql", PostgreSQLAdapter)
    AdapterFactory.register("clickhouse", ClickHouseAdapter)
    AdapterFactory.register("doris", DorisAdapter)
    AdapterFactory.register("hive", HiveAdapter)
    AdapterFactory.register("sqlserver", SQLServerAdapter)
    AdapterFactory.register("oracle", OracleAdapter)


_register_builtins()
