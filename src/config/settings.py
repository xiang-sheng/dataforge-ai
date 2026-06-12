"""
Application settings for DataForge AI.

All configuration is loaded from environment variables (with .env file support)
via pydantic-settings. This ensures type safety, validation, and clear defaults
for every knob in the system.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from src.ai.provider import LangChainProvider, ProviderConfig


class AppSettings(BaseSettings):
    """
    Root configuration object for the DataForge AI platform.

    Values are resolved in order:
      1. Explicit keyword arguments (rarely used)
      2. Environment variables  (DATAFORGE__SECTION__KEY style)
      3. .env file values
      4. Hard-coded defaults below
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DATAFORGE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # General application settings
    # ------------------------------------------------------------------ #

    app_name: str = Field(
        default="DataForge AI",
        description="Human-readable application name shown in the OpenAPI docs and UI.",
    )

    app_version: str = Field(
        default="0.1.0",
        description="Semantic version string surfaced via /health and OpenAPI.",
    )

    debug: bool = Field(
        default=False,
        description="Enable debug mode (verbose logging, detailed error responses).",
    )

    log_level: str = Field(
        default="INFO",
        description=(
            "Python logging level name. "
            "Accepted values: DEBUG, INFO, WARNING, ERROR, CRITICAL."
        ),
    )

    api_prefix: str = Field(
        default="/api/v1",
        description="URL prefix applied to every versioned router.",
    )

    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        description=(
            "List of allowed CORS origins. "
            "Use ['*'] in development only; restrict in production."
        ),
    )

    cors_allow_credentials: bool = Field(
        default=True,
        description="Whether to allow credentials (cookies / auth headers) in CORS requests.",
    )

    # ------------------------------------------------------------------ #
    # Internal application database (metadata store)
    # ------------------------------------------------------------------ #

    database_url: str = Field(
        default="postgresql+asyncpg://dataforge:dataforge@localhost:5432/dataforge_meta",
        description=(
            "Async SQLAlchemy URL for the internal metadata / state database. "
            "Must use an async driver such as asyncpg or aiomysql."
        ),
    )

    database_pool_size: int = Field(
        default=20,
        ge=1,
        description="Minimum number of connections kept alive in the internal DB pool.",
    )

    database_max_overflow: int = Field(
        default=10,
        ge=0,
        description="Additional connections allowed beyond pool_size under load.",
    )

    database_pool_timeout: int = Field(
        default=30,
        ge=1,
        description="Seconds to wait for a connection from the pool before raising.",
    )

    database_pool_recycle: int = Field(
        default=1800,
        ge=0,
        description="Seconds after which a connection is recycled (prevents stale connections).",
    )

    database_echo: bool = Field(
        default=False,
        description="Echo all SQL statements to the log (SQLAlchemy echo flag).",
    )

    # ------------------------------------------------------------------ #
    # Redis / cache
    # ------------------------------------------------------------------ #

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used for caching and async task queues.",
    )

    redis_max_connections: int = Field(
        default=50,
        ge=1,
        description="Maximum number of simultaneous Redis connections in the pool.",
    )

    redis_key_prefix: str = Field(
        default="dataforge:",
        description="Key prefix applied to all Redis keys to avoid collisions.",
    )

    cache_ttl_seconds: int = Field(
        default=3600,
        ge=0,
        description="Default TTL in seconds for cached metadata entries (0 = no expiry).",
    )

    # ------------------------------------------------------------------ #
    # OpenAI / LLM settings
    # ------------------------------------------------------------------ #

    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key. Required for AI-assisted SQL generation and modelling.",
    )

    openai_api_base: str | None = Field(
        default=None,
        description=(
            "Override the OpenAI base URL to point at an Azure OpenAI endpoint, "
            "a local proxy, or an OpenAI-compatible service (e.g. vLLM, Ollama)."
        ),
    )

    openai_model: str = Field(
        default="gpt-4o",
        description="Default model identifier used for generation requests.",
    )

    openai_fallback_model: str = Field(
        default="gpt-4o-mini",
        description="Lighter model used as a cost-effective fallback for simple tasks.",
    )

    openai_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature for LLM calls. "
            "Low values (0.0-0.3) preferred for deterministic SQL generation."
        ),
    )

    openai_max_tokens: int = Field(
        default=4096,
        ge=1,
        description="Maximum tokens in a single LLM response.",
    )

    openai_request_timeout: int = Field(
        default=120,
        ge=1,
        description="HTTP timeout in seconds for each OpenAI API call.",
    )

    openai_max_retries: int = Field(
        default=3,
        ge=0,
        description="Number of automatic retries on transient OpenAI errors.",
    )

    # ------------------------------------------------------------------ #
    # LLM provider selection & Ollama / local model settings
    # ------------------------------------------------------------------ #

    llm_provider: str = Field(
        default="openai",
        description=(
            "Active LLM provider. Supported values: openai, azure_openai, "
            "ollama, tongyi (通义千问), deepseek, local."
        ),
    )

    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL. Default is the local Ollama instance.",
    )

    ollama_model: str = Field(
        default="qwen2.5:14b",
        description=(
            "Default Ollama model. Recommended choices for data engineering: "
            "qwen2.5:14b, deepseek-coder-v2:16b, llama3.1:8b, codellama:13b."
        ),
    )

    ollama_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for Ollama models.",
    )

    ollama_request_timeout: int = Field(
        default=300,
        ge=10,
        description=(
            "HTTP timeout in seconds for Ollama API calls. "
            "Local models may be slower than cloud APIs, so the default is higher."
        ),
    )

    # ------------------------------------------------------------------ #
    # DuckDB local sandbox settings
    # ------------------------------------------------------------------ #

    duckdb_enabled: bool = Field(
        default=True,
        description="Whether the DuckDB local verification sandbox is enabled.",
    )

    duckdb_database_path: str = Field(
        default=":memory:",
        description=(
            "DuckDB database path. ':memory:' for in-memory (default, no persistence), "
            "or a file path like './dataforge_sandbox.duckdb' for persistent storage."
        ),
    )

    duckdb_verify_sample_rows: int = Field(
        default=100,
        ge=10,
        le=10_000,
        description="Number of sample rows to generate for DuckDB sandbox verification.",
    )

    duckdb_memory_limit_mb: int = Field(
        default=512,
        ge=64,
        description="Memory limit for the DuckDB sandbox in megabytes.",
    )

    # ------------------------------------------------------------------ #
    # Convention file settings
    # ------------------------------------------------------------------ #

    convention_file_path: str | None = Field(
        default=None,
        description=(
            "Path to the default table creation convention file (YAML or Markdown). "
            "If set, DDL generation will follow these naming/type/partition rules."
        ),
    )

    # ------------------------------------------------------------------ #
    # Query execution limits
    # ------------------------------------------------------------------ #

    query_timeout_seconds: int = Field(
        default=300,
        ge=1,
        description="Default timeout for user-initiated queries against external databases.",
    )

    query_max_rows: int = Field(
        default=10_000,
        ge=1,
        description="Maximum number of rows returned by a single preview query.",
    )

    # ------------------------------------------------------------------ #
    # JWT / auth (future use — placeholder)
    # ------------------------------------------------------------------ #

    jwt_secret_key: str | None = Field(
        default=None,
        description="Secret key for HS256 JWT signing. Required when auth is enabled.",
    )

    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm.",
    )

    jwt_access_token_expire_minutes: int = Field(
        default=60 * 24,
        ge=1,
        description="Lifetime of an access token in minutes (default: 24 h).",
    )

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return upper

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        """Allow a comma-separated string or a list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ------------------------------------------------------------------ #
    # Bridge to AI provider layer
    # ------------------------------------------------------------------ #

    def get_provider_config(self) -> ProviderConfig:
        """Construct a :class:`~src.ai.provider.ProviderConfig` from the
        current application settings.

        Maps :attr:`llm_provider` to the correct :class:`ModelProvider` enum
        value and selects the appropriate settings fields for each backend.

        Returns:
            A fully populated ``ProviderConfig`` ready for
            :class:`LLMFactory` / :class:`LangChainProvider`.
        """
        from src.ai.provider import ModelProvider, ProviderConfig

        provider_map: dict[str, ModelProvider] = {
            "openai": ModelProvider.OPENAI,
            "azure_openai": ModelProvider.AZURE_OPENAI,
            "ollama": ModelProvider.OLLAMA,
            "local": ModelProvider.LOCAL,
            "tongyi": ModelProvider.TONGYI,
            "deepseek": ModelProvider.DEEPSEEK,
        }

        provider = provider_map.get(self.llm_provider.lower())
        if provider is None:
            raise ValueError(
                f"Unknown llm_provider '{self.llm_provider}'. "
                f"Supported values: {', '.join(provider_map)}"
            )

        # Ollama / local models use their own config fields
        if provider in (ModelProvider.OLLAMA, ModelProvider.LOCAL):
            return ProviderConfig(
                provider=provider,
                api_key=None,
                base_url=self.ollama_base_url,
                model=self.ollama_model,
                temperature=self.ollama_temperature,
                max_tokens=self.openai_max_tokens,
                timeout=float(self.ollama_request_timeout),
                max_retries=self.openai_max_retries,
            )

        # All OpenAI-compatible providers share the same construction
        base_url = self.openai_api_base
        if provider == ModelProvider.DEEPSEEK and not base_url:
            base_url = "https://api.deepseek.com"

        return ProviderConfig(
            provider=provider,
            api_key=self.openai_api_key,
            base_url=base_url,
            model=self.openai_model,
            temperature=self.openai_temperature,
            max_tokens=self.openai_max_tokens,
            timeout=float(self.openai_request_timeout),
            max_retries=self.openai_max_retries,
        )

    @classmethod
    def get_ai_provider(cls) -> LangChainProvider:
        """Convenience class method that creates a fully initialised
        :class:`~src.ai.provider.LangChainProvider` from the current
        application settings.

        Equivalent to::

            settings = get_settings()
            config   = settings.get_provider_config()
            provider = LangChainProvider(config)

        Returns:
            A ready-to-use ``LangChainProvider`` instance.
        """
        from src.ai.provider import LangChainProvider

        settings = get_settings()
        config = settings.get_provider_config()
        return LangChainProvider(config)


@lru_cache
def get_settings() -> AppSettings:
    """
    Return a cached AppSettings singleton.

    Using ``lru_cache`` ensures we only read and validate the settings
    once per process, which is the standard FastAPI pattern.
    """
    return AppSettings()
