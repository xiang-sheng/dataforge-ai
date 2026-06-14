"""Alembic environment configuration for DataForge AI.

Reads the database URL from ``AppSettings`` (pydantic-settings) and
converts the async driver to a synchronous one so Alembic can manage
migrations without an async event loop.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# -- Import our ORM models so Alembic sees the metadata --------------- #
from src.core.models import Base

# -- Alembic config object ------------------------------------------- #
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# -- Target metadata for autogenerate -------------------------------- #
target_metadata = Base.metadata


# -- Helper: convert async URL → sync URL ---------------------------- #

_ASYNC_TO_SYNC_DRIVERS: dict[str, str] = {
    "postgresql+asyncpg": "postgresql+psycopg2",
    "mysql+aiomysql": "mysql+pymysql",
    "mssql+aioodbc": "mssql+pyodbc",
    "sqlite+aiosqlite": "sqlite",
    "clickhouse+asynch": "clickhouse+native",
    "hive+pyhive": "hive+pyhive",       # pyhive supports sync
    "oracle+oracledb": "oracle+oracledb",  # oracledb supports sync natively
}


def _to_sync_url(url: str) -> str:
    """Replace the async driver prefix with a synchronous equivalent."""
    for async_drv, sync_drv in _ASYNC_TO_SYNC_DRIVERS.items():
        if url.startswith(async_drv + "://"):
            return url.replace(async_drv + "://", sync_drv + "://", 1)
    return url


# -- Override sqlalchemy.url from AppSettings ------------------------- #

try:
    from src.config.settings import get_settings

    _settings = get_settings()
    _sync_url = _to_sync_url(_settings.database_url)
    config.set_main_option("sqlalchemy.url", _sync_url)
except Exception:
    # Fallback: use the placeholder URL from alembic.ini
    pass


# -- Migration runners ----------------------------------------------- #


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    Calls to ``context.execute()`` emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates a synchronous Engine and associates a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
