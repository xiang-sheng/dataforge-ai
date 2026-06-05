# -*- coding: utf-8 -*-
"""
Custom exception hierarchy for DataForge AI.

Every exception inherits from :class:`DataForgeError` so that callers can
catch the base class when a broad handler is appropriate, or catch a
specific subclass for fine-grained control.

All exceptions carry a machine-readable ``code`` string that is surfaced
in API error responses so that front-end clients can branch on it without
parsing human-readable messages.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class DataForgeError(Exception):
    """
    Base exception for every error raised inside the DataForge AI platform.

    Attributes
    ----------
    message:
        Human-readable description of what went wrong.
    code:
        Short, UPPER_SNAKE_CASE identifier suitable for programmatic handling
        (e.g. ``CONNECTION_FAILED``, ``QUERY_TIMEOUT``).
    details:
        Arbitrary extra context that may help with debugging (query text,
        connection id, stack snippets, etc.).
    """

    default_code: str = "DATAFORGE_ERROR"
    default_status_code: int = 500

    def __init__(
        self,
        message: str = "An unexpected error occurred.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.message = message
        self.code = code or self.default_code
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the exception into a JSON-friendly dict for API responses."""
        payload: Dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(code={self.code!r}, message={self.message!r})"
        )


# ------------------------------------------------------------------ #
# Connection errors
# ------------------------------------------------------------------ #


class ConnectionError(DataForgeError):
    """
    Raised when a database connection cannot be established or is lost.

    Typical causes:
    - Wrong host / port / credentials
    - Network unreachable or firewall blocking
    - Database engine is down
    """

    default_code = "CONNECTION_FAILED"
    default_status_code = 502

    def __init__(
        self,
        message: str = "Failed to connect to the target database.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message=message, code=code, details=details)


class ConnectionTimeoutError(ConnectionError):
    """Raised when a connection attempt exceeds the configured timeout."""

    default_code = "CONNECTION_TIMEOUT"

    def __init__(
        self,
        message: str = "Connection attempt timed out.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message=message, code=code, details=details)


class ConnectionPoolExhaustedError(ConnectionError):
    """Raised when the connection pool has no available connections."""

    default_code = "CONNECTION_POOL_EXHAUSTED"

    def __init__(
        self,
        message: str = "All connections in the pool are in use.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message=message, code=code, details=details)


# ------------------------------------------------------------------ #
# Query execution errors
# ------------------------------------------------------------------ #


class QueryExecutionError(DataForgeError):
    """
    Raised when a SQL query fails during execution against a target database.

    Carries the original SQL text (truncated) and the database engine's
    native error code when available.
    """

    default_code = "QUERY_EXECUTION_FAILED"
    default_status_code = 422

    def __init__(
        self,
        message: str = "Query execution failed.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        sql: Optional[str] = None,
        native_error_code: Optional[int] = None,
    ) -> None:
        _details = details or {}
        if sql:
            # Truncate long SQL to keep error payloads manageable
            _details["sql"] = sql[:2000] + ("..." if len(sql) > 2000 else "")
        if native_error_code is not None:
            _details["native_error_code"] = native_error_code
        super().__init__(message=message, code=code, details=_details)


class QueryTimeoutError(QueryExecutionError):
    """Raised when a query exceeds the configured execution timeout."""

    default_code = "QUERY_TIMEOUT"

    def __init__(
        self,
        message: str = "Query execution timed out.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message=message, code=code, details=details)


# ------------------------------------------------------------------ #
# AI / LLM generation errors
# ------------------------------------------------------------------ #


class AIGenerationError(DataForgeError):
    """
    Raised when an LLM call fails or returns an unusable response.

    Typical causes:
    - API key missing or invalid
    - Rate limit exceeded
    - Model returned malformed output that could not be parsed
    """

    default_code = "AI_GENERATION_FAILED"
    default_status_code = 502

    def __init__(
        self,
        message: str = "AI generation request failed.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message=message, code=code, details=details)


class AIParsingError(AIGenerationError):
    """Raised when the LLM response cannot be parsed into the expected schema."""

    default_code = "AI_PARSE_FAILED"

    def __init__(
        self,
        message: str = "Failed to parse AI model response.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message=message, code=code, details=details)


# ------------------------------------------------------------------ #
# Schema validation errors
# ------------------------------------------------------------------ #


class SchemaValidationError(DataForgeError):
    """
    Raised when a user-supplied schema definition is invalid.

    Examples:
    - Unsupported column type
    - Duplicate column names
    - Missing required fields
    """

    default_code = "SCHEMA_VALIDATION_FAILED"
    default_status_code = 422

    def __init__(
        self,
        message: str = "Schema validation failed.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        field_errors: Optional[Dict[str, str]] = None,
    ) -> None:
        _details = details or {}
        if field_errors:
            _details["field_errors"] = field_errors
        super().__init__(message=message, code=code, details=_details)


# ------------------------------------------------------------------ #
# Warehouse layer errors
# ------------------------------------------------------------------ #


class WarehouseLayerError(DataForgeError):
    """
    Raised when a data-warehouse layer constraint is violated.

    Examples:
    - Attempting to create a DWD table without an ODS source
    - Cross-layer dependency that violates the layering rules
    - Invalid layer transition in an ETL pipeline definition
    """

    default_code = "WAREHOUSE_LAYER_ERROR"
    default_status_code = 422

    def __init__(
        self,
        message: str = "Data warehouse layer constraint violated.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        source_layer: Optional[str] = None,
        target_layer: Optional[str] = None,
    ) -> None:
        _details = details or {}
        if source_layer:
            _details["source_layer"] = source_layer
        if target_layer:
            _details["target_layer"] = target_layer
        super().__init__(message=message, code=code, details=_details)


# ------------------------------------------------------------------ #
# ETL / pipeline errors
# ------------------------------------------------------------------ #


class ETLPipelineError(DataForgeError):
    """Raised when an ETL pipeline definition is invalid or fails at runtime."""

    default_code = "ETL_PIPELINE_ERROR"
    default_status_code = 500

    def __init__(
        self,
        message: str = "ETL pipeline execution failed.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> None:
        _details = details or {}
        if task_id:
            _details["task_id"] = task_id
        super().__init__(message=message, code=code, details=_details)


# ------------------------------------------------------------------ #
# Resource not found
# ------------------------------------------------------------------ #


class ResourceNotFoundError(DataForgeError):
    """
    Raised when a requested resource (connection, table, model) does not exist.

    Maps to HTTP 404 in the API layer.
    """

    default_code = "RESOURCE_NOT_FOUND"
    default_status_code = 404

    def __init__(
        self,
        message: str = "Requested resource not found.",
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> None:
        _details = details or {}
        if resource_type:
            _details["resource_type"] = resource_type
        if resource_id:
            _details["resource_id"] = resource_id
        super().__init__(message=message, code=code, details=_details)
