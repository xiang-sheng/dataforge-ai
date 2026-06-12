"""
DataForge AI - Data catalog service.

Provides a metadata catalog for registering, searching, and managing table
metadata across the data warehouse, with AI-powered auto-tagging and data
dictionary generation.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any

from src.ai.provider import BaseAIProvider, ChatMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TableCategory(StrEnum):
    """Business category classifications for tables."""

    FACT = "fact"
    DIMENSION = "dimension"
    AGGREGATE = "aggregate"
    STAGING = "staging"
    REFERENCE = "reference"
    REPORT = "report"
    OTHER = "other"


class DataSensitivity(StrEnum):
    """Data sensitivity / classification levels."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ColumnRole(StrEnum):
    """Semantic role of a column within a table."""

    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    SURROGATE_KEY = "surrogate_key"
    NATURAL_KEY = "natural_key"
    MEASURE = "measure"
    ATTRIBUTE = "attribute"
    PARTITION_KEY = "partition_key"
    METADATA = "metadata"
    DERIVED = "derived"


class CatalogEntryStatus(StrEnum):
    """Lifecycle status of a catalog entry."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    DRAFT = "draft"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ColumnMetadata:
    """Metadata for a single column in the catalog.

    Attributes:
        name: Column name.
        data_type: SQL data type.
        description: Business description of the column.
        role: Semantic role (PK, FK, measure, attribute, etc.).
        nullable: Whether NULLs are allowed.
        sample_values: Representative sample values.
        sensitivity: Data sensitivity classification.
        tags: Auto-generated or manually assigned tags.
        statistics: Column-level statistics (distinct count, null ratio, etc.).
    """

    name: str
    data_type: str
    description: str = ""
    role: ColumnRole = ColumnRole.ATTRIBUTE
    nullable: bool = True
    sample_values: list[str] = field(default_factory=list)
    sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    tags: list[str] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)


@dataclass
class TableMetadata:
    """Complete metadata for a table in the catalog.

    Attributes:
        table_id: Unique identifier for this catalog entry.
        connection_id: The database connection this table belongs to.
        schema_name: Database schema / namespace.
        table_name: Table name.
        description: Business description.
        category: Table category (fact, dimension, etc.).
        layer: Warehouse layer (ODS, DWD, DWS, ADS, DIM).
        columns: List of column metadata.
        primary_keys: Column names forming the primary key.
        foreign_keys: Foreign key references as (column, ref_table, ref_column).
        row_count: Approximate row count.
        size_bytes: Approximate size on disk.
        partition_columns: Columns used for partitioning.
        tags: Auto-generated or manually assigned tags.
        owner: Data owner or steward.
        sensitivity: Overall data sensitivity level.
        status: Lifecycle status.
        created_at: When the entry was first registered.
        updated_at: When the entry was last updated.
        last_profiled_at: When statistics were last refreshed.
        custom_properties: Arbitrary key-value metadata.
        upstream_tables: Tables this table derives from.
        downstream_tables: Tables derived from this table.
    """

    table_id: str = ""
    connection_id: str = ""
    schema_name: str = ""
    table_name: str = ""
    description: str = ""
    category: TableCategory = TableCategory.OTHER
    layer: str = ""
    columns: list[ColumnMetadata] = field(default_factory=list)
    primary_keys: list[str] = field(default_factory=list)
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)
    row_count: int = 0
    size_bytes: int = 0
    partition_columns: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    owner: str = ""
    sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    status: CatalogEntryStatus = CatalogEntryStatus.ACTIVE
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_profiled_at: datetime | None = None
    custom_properties: dict[str, str] = field(default_factory=dict)
    upstream_tables: list[str] = field(default_factory=list)
    downstream_tables: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.table_id:
            self.table_id = f"tbl_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.utcnow()
        if not self.updated_at:
            self.updated_at = self.created_at

    @property
    def full_name(self) -> str:
        """Return the fully qualified table name."""
        if self.schema_name:
            return f"{self.schema_name}.{self.table_name}"
        return self.table_name


@dataclass
class SearchResult:
    """A search result from the catalog.

    Attributes:
        entry: The matched catalog entry.
        score: Relevance score (0.0 to 1.0).
        matched_fields: Which fields matched the search query.
    """

    entry: TableMetadata
    score: float = 0.0
    matched_fields: list[str] = field(default_factory=list)


@dataclass
class DataDictionary:
    """A generated data dictionary document for a connection.

    Attributes:
        connection_id: The connection this dictionary covers.
        generated_at: When the dictionary was generated.
        tables: List of table entries included.
        summary: High-level summary statistics.
        content: The rendered document content (Markdown).
    """

    connection_id: str
    generated_at: datetime = field(default_factory=datetime.utcnow)
    tables: list[TableMetadata] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    content: str = ""


# ---------------------------------------------------------------------------
# DataCatalog
# ---------------------------------------------------------------------------

class DataCatalog:
    """Metadata catalog service for the data warehouse.

    Provides methods for registering, searching, updating, and enriching
    table metadata.  Supports AI-powered auto-tagging and data dictionary
    generation.

    The catalog uses an in-memory store by default.  For production use,
    replace the storage backend with a persistent store (database, file,
    or external catalog service like Apache Atlas or DataHub).

    Args:
        provider: An initialized ``BaseAIProvider`` for AI-powered features.

    Usage::

        catalog = DataCatalog(provider)

        # Register a table
        entry = await catalog.register_table("conn_1", TableMetadata(
            table_name="dwd_trade_order_di",
            description="Cleansed daily order detail",
            layer="DWD",
        ))

        # Search
        results = catalog.search_tables("order", filters={"layer": "DWD"})

        # Generate data dictionary
        dictionary = await catalog.generate_data_dictionary("conn_1")
    """

    def __init__(self, provider: BaseAIProvider) -> None:
        self._provider = provider
        self._entries: dict[str, TableMetadata] = {}  # table_id -> metadata
        self._index_by_name: dict[str, str] = {}  # full_name -> table_id
        self._index_by_connection: dict[str, list[str]] = defaultdict(list)

    # -- Registration -------------------------------------------------------

    async def register_table(
        self,
        connection_id: str,
        table_info: TableMetadata,
    ) -> TableMetadata:
        """Register a table in the catalog.

        If a table with the same full name already exists under the same
        connection, it will be updated instead of creating a duplicate.

        Args:
            connection_id: The database connection identifier.
            table_info: The table metadata to register.

        Returns:
            The registered (or updated) ``TableMetadata`` with assigned ID
            and timestamps.
        """
        table_info.connection_id = connection_id
        table_info.updated_at = datetime.utcnow()

        # Check for existing entry
        full_name = table_info.full_name
        existing_id = self._index_by_name.get(f"{connection_id}:{full_name}")

        if existing_id and existing_id in self._entries:
            # Update existing
            existing = self._entries[existing_id]
            existing.description = table_info.description or existing.description
            existing.columns = table_info.columns or existing.columns
            existing.primary_keys = table_info.primary_keys or existing.primary_keys
            existing.foreign_keys = table_info.foreign_keys or existing.foreign_keys
            existing.row_count = table_info.row_count or existing.row_count
            existing.size_bytes = table_info.size_bytes or existing.size_bytes
            existing.tags = table_info.tags or existing.tags
            existing.updated_at = datetime.utcnow()
            existing.layer = table_info.layer or existing.layer
            existing.category = table_info.category if table_info.category != TableCategory.OTHER else existing.category

            logger.info("Updated catalog entry for '%s'", full_name)
            return existing

        # New entry
        if not table_info.created_at:
            table_info.created_at = datetime.utcnow()

        self._entries[table_info.table_id] = table_info
        self._index_by_name[f"{connection_id}:{full_name}"] = table_info.table_id
        self._index_by_connection[connection_id].append(table_info.table_id)

        logger.info("Registered catalog entry '%s' (id=%s)", full_name, table_info.table_id)
        return table_info

    # -- Search -------------------------------------------------------------

    def search_tables(
        self,
        keyword: str,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Search the catalog for tables matching a keyword and optional filters.

        Searches across table name, description, column names, column
        descriptions, and tags.

        Args:
            keyword: The search keyword (case-insensitive substring match).
            filters: Optional dict of exact-match filters.  Supported keys:
                ``layer``, ``category``, ``connection_id``, ``status``,
                ``sensitivity``, ``owner``.
            limit: Maximum number of results to return.

        Returns:
            A list of ``SearchResult`` objects sorted by relevance score.
        """
        keyword_lower = keyword.lower()
        results: list[SearchResult] = []

        for entry in self._entries.values():
            matched_fields: list[str] = []
            score = 0.0

            # Name match
            if keyword_lower in entry.table_name.lower():
                matched_fields.append("table_name")
                score += 0.5
            if keyword_lower in entry.full_name.lower():
                matched_fields.append("full_name")
                score += 0.3

            # Description match
            if keyword_lower in entry.description.lower():
                matched_fields.append("description")
                score += 0.3

            # Column name match
            for col in entry.columns:
                if keyword_lower in col.name.lower():
                    matched_fields.append(f"column:{col.name}")
                    score += 0.2
                if keyword_lower in col.description.lower():
                    matched_fields.append(f"column_desc:{col.name}")
                    score += 0.1

            # Tag match
            for tag in entry.tags:
                if keyword_lower in tag.lower():
                    matched_fields.append(f"tag:{tag}")
                    score += 0.2

            if not matched_fields:
                continue

            # Apply filters
            if filters and not self._matches_filters(entry, filters):
                continue

            # Normalize score to [0, 1]
            score = min(score, 1.0)

            results.append(SearchResult(entry=entry, score=score, matched_fields=matched_fields))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    # -- Metadata retrieval -------------------------------------------------

    def get_table_metadata(self, table_id: str) -> TableMetadata | None:
        """Retrieve full metadata for a table by its catalog ID.

        Args:
            table_id: The unique catalog entry identifier.

        Returns:
            The ``TableMetadata`` if found, ``None`` otherwise.
        """
        return self._entries.get(table_id)

    def get_table_by_name(
        self,
        connection_id: str,
        schema_name: str,
        table_name: str,
    ) -> TableMetadata | None:
        """Retrieve metadata for a table by its fully qualified name.

        Args:
            connection_id: The connection identifier.
            schema_name: The database schema.
            table_name: The table name.

        Returns:
            The ``TableMetadata`` if found, ``None`` otherwise.
        """
        full_name = f"{schema_name}.{table_name}" if schema_name else table_name
        key = f"{connection_id}:{full_name}"
        entry_id = self._index_by_name.get(key)
        if entry_id:
            return self._entries.get(entry_id)
        return None

    # -- Metadata update ----------------------------------------------------

    def update_metadata(
        self,
        table_id: str,
        changes: dict[str, Any],
    ) -> TableMetadata | None:
        """Update specific fields of a catalog entry.

        Args:
            table_id: The catalog entry identifier.
            changes: A dict of field names to new values.

        Returns:
            The updated ``TableMetadata``, or ``None`` if not found.
        """
        entry = self._entries.get(table_id)
        if entry is None:
            return None

        for attr, value in changes.items():
            if hasattr(entry, attr):
                setattr(entry, attr, value)

        entry.updated_at = datetime.utcnow()
        logger.info("Updated catalog entry '%s' (fields: %s)", table_id, list(changes.keys()))
        return entry

    def delete_entry(self, table_id: str) -> bool:
        """Remove a catalog entry.

        Args:
            table_id: The catalog entry identifier.

        Returns:
            ``True`` if the entry was deleted, ``False`` if not found.
        """
        entry = self._entries.pop(table_id, None)
        if entry is None:
            return False

        full_name = entry.full_name
        key = f"{entry.connection_id}:{full_name}"
        self._index_by_name.pop(key, None)

        conn_list = self._index_by_connection.get(entry.connection_id, [])
        if table_id in conn_list:
            conn_list.remove(table_id)

        logger.info("Deleted catalog entry '%s' (id=%s)", full_name, table_id)
        return True

    # -- Data dictionary generation -------------------------------------------

    async def generate_data_dictionary(
        self,
        connection_id: str,
        include_statistics: bool = True,
        format: str = "markdown",
    ) -> DataDictionary:
        """Generate a data dictionary document for all tables under a connection.

        Args:
            connection_id: The connection to generate the dictionary for.
            include_statistics: Whether to include column statistics.
            format: Output format (``"markdown"`` or ``"html"``).

        Returns:
            A ``DataDictionary`` with the rendered content.
        """
        table_ids = self._index_by_connection.get(connection_id, [])
        tables = [
            self._entries[tid]
            for tid in table_ids
            if tid in self._entries
        ]

        # Sort by layer then name
        layer_order = {"ods": 0, "dwd": 1, "dim": 2, "dws": 3, "ads": 4}
        tables.sort(key=lambda t: (
            layer_order.get(t.layer.lower(), 99),
            t.table_name,
        ))

        # Build summary
        layer_counts: dict[str, int] = defaultdict(int)
        total_columns = 0
        for t in tables:
            layer_counts[t.layer or "unknown"] += 1
            total_columns += len(t.columns)

        summary = {
            "total_tables": len(tables),
            "total_columns": total_columns,
            "layer_distribution": dict(layer_counts),
            "connection_id": connection_id,
        }

        # Render content
        if format == "html":
            content = self._render_html_dictionary(tables, summary, connection_id)
        else:
            content = self._render_markdown_dictionary(tables, summary, connection_id)

        return DataDictionary(
            connection_id=connection_id,
            generated_at=datetime.utcnow(),
            tables=tables,
            summary=summary,
            content=content,
        )

    # -- AI-powered auto-tagging --------------------------------------------

    async def auto_tag_tables(
        self,
        table_info: TableMetadata,
    ) -> list[str]:
        """Use AI to suggest tags for a table based on its metadata.

        Analyzes the table name, description, column names, and column
        descriptions to suggest relevant business and technical tags.

        Args:
            table_info: The table metadata to analyze.

        Returns:
            A list of suggested tag strings.
        """
        # Build context for the AI
        col_descriptions = []
        for col in table_info.columns:
            col_descriptions.append(
                f"  - {col.name} ({col.data_type}): {col.description or 'No description'}"
            )
        cols_str = "\n".join(col_descriptions) if col_descriptions else "  (No columns defined)"

        prompt = (
            f"Based on the following table metadata, suggest 5-10 relevant tags "
            f"for cataloging and discoverability.\n\n"
            f"## Table\n"
            f"- Name: {table_info.full_name}\n"
            f"- Description: {table_info.description or 'No description'}\n"
            f"- Layer: {table_info.layer or 'Not specified'}\n"
            f"- Category: {table_info.category.value}\n\n"
            f"## Columns\n{cols_str}\n\n"
            f"## Instructions\n"
            f"Suggest tags in the following categories:\n"
            f"1. **Business domain** (e.g. ecommerce, finance, user, logistics)\n"
            f"2. **Data type** (e.g. transactional, dimensional, aggregated)\n"
            f"3. **Sensitivity** (e.g. pii, financial-data)\n"
            f"4. **Usage** (e.g. reporting, analytics, ml-feature)\n"
            f"5. **Quality** (e.g. cleansed, raw, enriched)\n\n"
            f"Return ONLY a comma-separated list of lowercase tags, no other text.\n"
        )

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a data governance expert.  Suggest concise, "
                    "standardized tags for data catalog entries."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages)

        # Parse the comma-separated tags
        tags = [
            tag.strip().lower().replace(" ", "-")
            for tag in response.content.split(",")
            if tag.strip()
        ]

        return tags[:15]  # Cap at 15 tags

    async def auto_tag_and_apply(
        self,
        table_id: str,
    ) -> list[str]:
        """Generate and apply AI tags to an existing catalog entry.

        Args:
            table_id: The catalog entry to tag.

        Returns:
            The list of newly applied tags.

        Raises:
            ValueError: If the table_id is not found.
        """
        entry = self._entries.get(table_id)
        if entry is None:
            raise ValueError(f"Catalog entry '{table_id}' not found.")

        suggested = await self.auto_tag_tables(entry)

        # Merge with existing tags (no duplicates)
        existing_set = set(entry.tags)
        new_tags = [t for t in suggested if t not in existing_set]
        entry.tags.extend(new_tags)
        entry.updated_at = datetime.utcnow()

        logger.info(
            "Applied %d new tags to '%s': %s",
            len(new_tags),
            entry.full_name,
            new_tags,
        )
        return new_tags

    # -- Catalog statistics -------------------------------------------------

    def get_catalog_statistics(self) -> dict[str, Any]:
        """Return overall catalog statistics.

        Returns:
            A dict with total tables, columns, layer distribution, etc.
        """
        total_columns = 0
        layer_counts: dict[str, int] = defaultdict(int)
        category_counts: dict[str, int] = defaultdict(int)
        connection_counts: dict[str, int] = defaultdict(int)
        tag_counts: dict[str, int] = defaultdict(int)

        for entry in self._entries.values():
            total_columns += len(entry.columns)
            layer_counts[entry.layer or "unknown"] += 1
            category_counts[entry.category.value] += 1
            connection_counts[entry.connection_id] += 1
            for tag in entry.tags:
                tag_counts[tag] += 1

        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        return {
            "total_tables": len(self._entries),
            "total_columns": total_columns,
            "total_connections": len(self._index_by_connection),
            "layer_distribution": dict(layer_counts),
            "category_distribution": dict(category_counts),
            "connection_distribution": dict(connection_counts),
            "top_tags": top_tags,
        }

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    def _matches_filters(
        entry: TableMetadata,
        filters: dict[str, Any],
    ) -> bool:
        """Check if an entry matches all specified filters."""
        for key, expected in filters.items():
            actual = getattr(entry, key, None)
            if actual is None:
                return False
            # Handle enum comparisons
            if isinstance(actual, Enum):
                if actual.value != expected and actual != expected:
                    return False
            elif str(actual) != str(expected):
                return False
        return True

    @staticmethod
    def _render_markdown_dictionary(
        tables: list[TableMetadata],
        summary: dict[str, Any],
        connection_id: str,
    ) -> str:
        """Render the data dictionary as a Markdown document."""
        lines: list[str] = [
            "# Data Dictionary",
            "",
            f"**Connection:** {connection_id}",
            f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Tables | {summary['total_tables']} |",
            f"| Total Columns | {summary['total_columns']} |",
            "",
            "### Layer Distribution",
            "",
            "| Layer | Tables |",
            "|-------|--------|",
        ]

        for layer, count in sorted(summary.get("layer_distribution", {}).items()):
            lines.append(f"| {layer.upper()} | {count} |")

        lines.append("")
        lines.append("---")
        lines.append("")

        # Group by layer
        by_layer: dict[str, list[TableMetadata]] = defaultdict(list)
        for t in tables:
            by_layer[t.layer or "other"].append(t)

        for layer in sorted(by_layer.keys()):
            lines.append(f"## {layer.upper()} Layer")
            lines.append("")

            for table in by_layer[layer]:
                lines.append(f"### {table.full_name}")
                lines.append("")
                if table.description:
                    lines.append(f"{table.description}")
                    lines.append("")

                lines.append(f"- **Category:** {table.category.value}")
                lines.append(f"- **Owner:** {table.owner or 'Not assigned'}")
                lines.append(f"- **Rows:** ~{table.row_count:,}")
                if table.tags:
                    lines.append(f"- **Tags:** {', '.join(table.tags)}")
                lines.append("")

                if table.columns:
                    lines.append("| Column | Type | Nullable | Description |")
                    lines.append("|--------|------|----------|-------------|")
                    for col in table.columns:
                        nullable = "Yes" if col.nullable else "No"
                        lines.append(
                            f"| {col.name} | {col.data_type} | {nullable} | "
                            f"{col.description} |"
                        )
                    lines.append("")

                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _render_html_dictionary(
        tables: list[TableMetadata],
        summary: dict[str, Any],
        connection_id: str,
    ) -> str:
        """Render the data dictionary as an HTML document."""
        rows_html: list[str] = []
        for table in tables:
            for col in table.columns:
                rows_html.append(
                    f"<tr>"
                    f"<td>{table.full_name}</td>"
                    f"<td>{col.name}</td>"
                    f"<td>{col.data_type}</td>"
                    f"<td>{'Yes' if col.nullable else 'No'}</td>"
                    f"<td>{col.description}</td>"
                    f"</tr>"
                )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Data Dictionary - {connection_id}</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; margin: 2em; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f5f5f5; font-weight: 600; }}
        tr:nth-child(even) {{ background: #fafafa; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f0f7ff; padding: 1em; border-radius: 4px; margin-bottom: 2em; }}
    </style>
</head>
<body>
    <h1>Data Dictionary</h1>
    <div class="summary">
        <p><strong>Connection:</strong> {connection_id}</p>
        <p><strong>Generated:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
        <p><strong>Tables:</strong> {summary['total_tables']} |
           <strong>Columns:</strong> {summary['total_columns']}</p>
    </div>
    <table>
        <thead>
            <tr>
                <th>Table</th>
                <th>Column</th>
                <th>Type</th>
                <th>Nullable</th>
                <th>Description</th>
            </tr>
        </thead>
        <tbody>
            {"".join(rows_html)}
        </tbody>
    </table>
</body>
</html>"""
        return html
