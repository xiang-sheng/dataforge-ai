"""
DataForge AI - Convention/spec file loader for data warehouse table creation standards.

Reads convention files (YAML or Markdown) that define how tables should be built
in a data warehouse.  Conventions cover naming rules, data type standards,
partitioning strategies, comment requirements, quality constraints, and storage
format preferences.

Typical usage::

    loader = ConventionLoader()
    convention = loader.load_auto("conventions/standard.yaml")

    validator = ConventionValidator()
    result = validator.validate_table(table_schema, convention)
    if not result.is_valid:
        for v in result.violations:
            print(f"[{v.severity}] {v.location}: {v.message}")
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ConventionError(Exception):
    """Base exception for convention loading and parsing errors."""

    def __init__(
        self,
        message: str = "Convention loading failed.",
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.path = path
        self.details = details or {}
        super().__init__(self.message)


class ConventionParseError(ConventionError):
    """Raised when a convention file cannot be parsed (malformed YAML / Markdown)."""

    def __init__(
        self,
        message: str = "Failed to parse convention file.",
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message=message, path=path, details=details)


class ConventionValidationError(ConventionError):
    """Raised when a loaded convention fails internal consistency checks."""

    def __init__(
        self,
        message: str = "Convention validation failed.",
        warnings: list[str] | None = None,
    ) -> None:
        self.warnings = warnings or []
        super().__init__(message=message, details={"warnings": self.warnings})


# ---------------------------------------------------------------------------
# Pydantic data models - convention building blocks
# ---------------------------------------------------------------------------

class NamingConvention(BaseModel):
    """Table and column naming rules.

    Encodes the patterns, case styles, prefixes, suffixes, and reserved-word
    lists that govern how tables and columns must be named within the
    warehouse.

    Attributes:
        table_pattern: Template for constructing table names,
            e.g. ``"{layer}_{domain}_{description}"``.
        column_patterns: Per-category column naming templates,
            e.g. ``{"surrogate_key": "{table}_sk", "business_key": "{table}_bk"}``.
        case_style: Required case convention.
            One of ``"snake_case"``, ``"camelCase"``, ``"UPPER_SNAKE"``.
        prefix_rules: Mapping of layer name to required table-name prefix,
            e.g. ``{"ODS": "ods_", "DWD": "dwd_"}``.
        suffix_rules: Mapping of table category to required suffix,
            e.g. ``{"dimension": "_dim", "fact": "_fact"}``.
        reserved_words: Words that must never appear as table or column names.
    """

    table_pattern: str = Field(
        default="{layer}_{domain}_{description}",
        description="Template pattern for table names.",
    )
    column_patterns: dict[str, str] = Field(
        default_factory=dict,
        description="Per-category column naming templates.",
    )
    case_style: str = Field(
        default="snake_case",
        description="Required identifier case style.",
    )
    prefix_rules: dict[str, str] = Field(
        default_factory=lambda: {
            "ODS": "ods_",
            "DWD": "dwd_",
            "DWS": "dws_",
            "ADS": "ads_",
            "DIM": "dim_",
            "TMP": "tmp_",
        },
        description="Layer name -> required table name prefix.",
    )
    suffix_rules: dict[str, str] = Field(
        default_factory=lambda: {
            "dimension": "_dim",
            "fact": "_fact",
            "aggregate": "_agg",
        },
        description="Table category -> required suffix.",
    )
    reserved_words: list[str] = Field(
        default_factory=lambda: [
            "select", "from", "where", "insert", "update", "delete",
            "drop", "create", "alter", "table", "index", "order",
            "group", "by", "having", "join", "union", "all", "and",
            "or", "not", "null", "true", "false", "set", "values",
            "into", "as", "on", "in", "is", "like", "between", "exists",
            "case", "when", "then", "else", "end", "limit", "offset",
        ],
        description="Words that cannot be used as table or column names.",
    )


class DataTypeStandard(BaseModel):
    """Standard data type mappings per target engine.

    Attributes:
        logical_to_physical: Maps a logical type name to a dict of
            ``{engine: physical_type}``, e.g.
            ``{"STRING": {"clickhouse": "String", "hive": "STRING", "duckdb": "VARCHAR"}}``.
        preferred_types: Mapping of column-role hints to preferred SQL types,
            e.g. ``{"id_column": "BIGINT", "amount": "DECIMAL(18,2)"}``.
        forbidden_types: Types that must never be used (e.g. ``["TEXT", "BLOB"]``).
    """

    logical_to_physical: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {
            "STRING": {"clickhouse": "String", "hive": "STRING", "duckdb": "VARCHAR", "mysql": "VARCHAR(255)", "postgresql": "VARCHAR(255)"},
            "INTEGER": {"clickhouse": "Int32", "hive": "INT", "duckdb": "INTEGER", "mysql": "INT", "postgresql": "INTEGER"},
            "BIGINT": {"clickhouse": "Int64", "hive": "BIGINT", "duckdb": "BIGINT", "mysql": "BIGINT", "postgresql": "BIGINT"},
            "FLOAT": {"clickhouse": "Float32", "hive": "FLOAT", "duckdb": "REAL", "mysql": "FLOAT", "postgresql": "REAL"},
            "DOUBLE": {"clickhouse": "Float64", "hive": "DOUBLE", "duckdb": "DOUBLE", "mysql": "DOUBLE", "postgresql": "DOUBLE PRECISION"},
            "DECIMAL": {"clickhouse": "Decimal(18,2)", "hive": "DECIMAL(18,2)", "duckdb": "DECIMAL(18,2)", "mysql": "DECIMAL(18,2)", "postgresql": "NUMERIC(18,2)"},
            "BOOLEAN": {"clickhouse": "UInt8", "hive": "BOOLEAN", "duckdb": "BOOLEAN", "mysql": "TINYINT(1)", "postgresql": "BOOLEAN"},
            "DATE": {"clickhouse": "Date", "hive": "DATE", "duckdb": "DATE", "mysql": "DATE", "postgresql": "DATE"},
            "TIMESTAMP": {"clickhouse": "DateTime", "hive": "TIMESTAMP", "duckdb": "TIMESTAMP", "mysql": "DATETIME", "postgresql": "TIMESTAMP"},
            "JSON": {"clickhouse": "String", "hive": "STRING", "duckdb": "JSON", "mysql": "JSON", "postgresql": "JSONB"},
        },
        description="Logical type -> {engine: physical type} mapping.",
    )
    preferred_types: dict[str, str] = Field(
        default_factory=lambda: {
            "id_column": "BIGINT",
            "amount": "DECIMAL(18,2)",
            "quantity": "INTEGER",
            "percentage": "DECIMAL(5,4)",
            "flag": "BOOLEAN",
            "description": "STRING",
            "timestamp": "TIMESTAMP",
            "date": "DATE",
        },
        description="Column-role hints to preferred SQL types.",
    )
    forbidden_types: list[str] = Field(
        default_factory=lambda: ["TEXT", "BLOB", "MEDIUMTEXT", "LONGTEXT", "MEDIUMBLOB", "LONGBLOB"],
        description="Data types that must not be used.",
    )


class PartitionConvention(BaseModel):
    """Partitioning strategy rules.

    Attributes:
        default_partition_column: Default column name used for partitioning.
        partition_by_layer: Per-layer partition column override.
        retention_days_by_layer: Data retention period (days) per layer.
        granularity: Partition granularity: ``"daily"``, ``"hourly"``, ``"monthly"``.
    """

    default_partition_column: str = Field(
        default="dt",
        description="Default partition column name.",
    )
    partition_by_layer: dict[str, str] = Field(
        default_factory=lambda: {
            "ODS": "dt",
            "DWD": "dt",
            "DWS": "stat_date",
            "ADS": "dt",
        },
        description="Layer name -> partition column override.",
    )
    retention_days_by_layer: dict[str, int] = Field(
        default_factory=lambda: {
            "ODS": 90,
            "DWD": 365,
            "DWS": 730,
            "ADS": 0,
            "DIM": 0,
            "TMP": 7,
        },
        description="Data retention (days) per layer.  0 = permanent.",
    )
    granularity: str = Field(
        default="daily",
        description="Partition granularity: daily, hourly, or monthly.",
    )


class CommentConvention(BaseModel):
    """Comment / description requirements.

    Attributes:
        table_comment_required: Whether every table must have a comment.
        column_comment_required: Whether every column must have a comment.
        table_comment_pattern: Optional regex or template that table comments
            must match, e.g. ``"[{layer}] {description} - {owner}"``.
        column_comment_min_length: Minimum character length for column comments.
    """

    table_comment_required: bool = Field(default=True)
    column_comment_required: bool = Field(default=True)
    table_comment_pattern: str | None = Field(default=None)
    column_comment_min_length: int = Field(default=5)


class QualityRule(BaseModel):
    """Data quality constraints.

    Attributes:
        primary_key_required: Whether every table must declare a primary key.
        not_null_columns: Glob patterns for column names that must be NOT NULL,
            e.g. ``["*_id", "*_key"]``.
        unique_constraints: Glob patterns for columns that must be UNIQUE.
        check_constraints: A list of dicts with ``column`` and ``rule`` keys,
            e.g. ``[{"column": "amount", "rule": ">= 0"}]``.
    """

    primary_key_required: bool = Field(default=True)
    not_null_columns: list[str] = Field(
        default_factory=lambda: ["*_id", "*_key", "*_sk", "*_bk"],
        description="Glob patterns for columns that must be NOT NULL.",
    )
    unique_constraints: list[str] = Field(
        default_factory=lambda: ["*_sk", "*_bk"],
        description="Glob patterns for columns that must be UNIQUE.",
    )
    check_constraints: list[dict[str, str]] = Field(
        default_factory=list,
        description="Column-level CHECK constraints.",
    )


class StorageConvention(BaseModel):
    """Storage format and compression rules.

    Attributes:
        default_format_by_engine: Preferred storage format per engine.
        compression_by_engine: Preferred compression codec per engine.
        index_strategy: Human-readable index strategy hints.
    """

    default_format_by_engine: dict[str, str] = Field(
        default_factory=lambda: {
            "hive": "ORC",
            "clickhouse": "MergeTree",
            "duckdb": "Parquet",
            "mysql": "InnoDB",
            "postgresql": "heap",
            "doris": "OLAP",
            "spark": "Parquet",
        },
        description="Engine name -> preferred storage format.",
    )
    compression_by_engine: dict[str, str] = Field(
        default_factory=lambda: {
            "hive": "SNAPPY",
            "clickhouse": "LZ4",
            "duckdb": "ZSTD",
            "spark": "SNAPPY",
        },
        description="Engine name -> preferred compression codec.",
    )
    index_strategy: dict[str, str] = Field(
        default_factory=lambda: {
            "bitmap": "For low-cardinality columns (< 1000 distinct values)",
            "btree": "For high-cardinality columns used in equality/range filters",
            "minmax": "For monotonically increasing columns (timestamps, sequence IDs)",
        },
        description="Index strategy hints keyed by index type.",
    )


class TableConvention(BaseModel):
    """Complete convention set for table creation.

    This is the top-level model that aggregates every sub-convention into a
    single object that can be loaded, validated, cached, and merged.

    Attributes:
        version: Semantic version string for this convention set.
        description: Human-readable summary of the convention's purpose.
        naming: Naming rules.
        data_types: Data type standards.
        partition: Partitioning strategy.
        comments: Comment requirements.
        quality: Quality constraints.
        storage: Storage format and compression rules.
        custom_rules: Arbitrary user-defined extra rules.
    """

    version: str = Field(default="1.0.0", description="Convention version.")
    description: str = Field(
        default="Default data warehouse table creation conventions.",
        description="Summary of the convention set.",
    )
    naming: NamingConvention = Field(default_factory=NamingConvention)
    data_types: DataTypeStandard = Field(default_factory=DataTypeStandard)
    partition: PartitionConvention = Field(default_factory=PartitionConvention)
    comments: CommentConvention = Field(default_factory=CommentConvention)
    quality: QualityRule = Field(default_factory=QualityRule)
    storage: StorageConvention = Field(default_factory=StorageConvention)
    custom_rules: dict[str, Any] = Field(
        default_factory=dict,
        description="User-defined extra rules.",
    )


# ---------------------------------------------------------------------------
# Validation result models
# ---------------------------------------------------------------------------

class Violation(BaseModel):
    """A single convention violation found during validation.

    Attributes:
        severity: ``"error"``, ``"warning"``, or ``"info"``.
        rule: Short identifier for the rule that was violated.
        message: Human-readable description.
        location: The table name or column name where the violation was found.
        suggestion: Optional recommendation for how to fix the violation.
    """

    severity: str = Field(..., description="error | warning | info")
    rule: str = Field(..., description="Rule identifier.")
    message: str = Field(..., description="Description of the violation.")
    location: str = Field(default="", description="Table or column name.")
    suggestion: str | None = Field(default=None, description="How to fix it.")


class ValidationResult(BaseModel):
    """Aggregated outcome of validating a table schema against conventions.

    Attributes:
        is_valid: ``True`` when there are zero ``error``-severity violations.
        violations: Every violation found (across all severities).
        warnings: Plain-text advisory messages (non-rule-based).
        score: Compliance score from 0 (terrible) to 100 (perfect).
    """

    is_valid: bool = Field(default=True)
    violations: list[Violation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score: int = Field(default=100, ge=0, le=100)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    - Dict values are merged recursively.
    - List values in *override* replace those in *base*.
    - Scalar values in *override* replace those in *base*.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _normalise_case(name: str, case_style: str) -> str:
    """Return *name* converted to the requested *case_style*.

    Supports ``snake_case``, ``UPPER_SNAKE``, and ``camelCase``.
    """
    if case_style == "snake_case":
        # Insert underscore before uppercase runs, then lower
        s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
        return re.sub(r"[-\s]+", "_", s2).lower()
    if case_style == "UPPER_SNAKE":
        s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
        return re.sub(r"[-\s]+", "_", s2).upper()
    if case_style == "camelCase":
        parts = re.split(r"[_\-\s]+", name)
        return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
    return name


def _matches_any_pattern(name: str, patterns: list[str]) -> bool:
    """Return ``True`` if *name* matches at least one glob in *patterns*."""
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


def _is_valid_case(name: str, case_style: str) -> bool:
    """Check whether *name* conforms to the requested case style."""
    if case_style == "snake_case":
        return bool(re.match(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", name))
    if case_style == "UPPER_SNAKE":
        return bool(re.match(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$", name))
    if case_style == "camelCase":
        return bool(re.match(r"^[a-z][a-zA-Z0-9]*$", name))
    # Unknown style -- treat as valid
    return True


def _file_hash(path: str) -> str:
    """Return the MD5 hex digest of a file (used for cache invalidation)."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Markdown parser helpers
# ---------------------------------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
_MD_CODE_BLOCK_RE = re.compile(r"```(?:yaml|json)?\s*\n(.*?)```", re.DOTALL)
_MD_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_MD_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
_MD_KV_RE = re.compile(r"^[-*]\s+\*\*(.+?)\*\*\s*[:=]\s*(.+)$")


def _parse_md_yaml_block(text: str) -> dict[str, Any] | None:
    """Extract and parse the first YAML code block from Markdown *text*."""
    try:
        import yaml
    except ImportError:
        return None

    match = _MD_CODE_BLOCK_RE.search(text)
    if match:
        try:
            data = yaml.safe_load(match.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _parse_md_table(text: str) -> list[dict[str, str]]:
    """Parse the first Markdown table found in *text* into a list of row dicts."""
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    headers: list[str] = []
    in_table = False
    header_parsed = False

    for line in lines:
        stripped = line.strip()
        if _MD_TABLE_ROW_RE.match(stripped):
            if not in_table:
                in_table = True
                # First row = headers
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                headers = cells
                header_parsed = False
                continue
            if not header_parsed and _MD_TABLE_SEP_RE.match(stripped):
                # Second row = separator
                header_parsed = True
                continue
            if header_parsed:
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                row = {}
                for i, header in enumerate(headers):
                    row[header] = cells[i] if i < len(cells) else ""
                rows.append(row)
        elif in_table:
            # End of table
            break

    return rows


def _parse_md_key_value(text: str) -> dict[str, str]:
    """Parse Markdown bullet-list key-value pairs.

    Expects lines like ``- **key**: value`` or ``- **key** = value``.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        m = _MD_KV_RE.match(line.strip())
        if m:
            result[m.group(1).strip()] = m.group(2).strip()
    return result


def _section_map(md_text: str) -> dict[str, str]:
    """Split a Markdown document into sections keyed by heading text.

    Only the body *below* each heading (up to the next heading of the same
    or higher level) is stored.
    """
    sections: dict[str, str] = {}
    headings = list(_MD_HEADING_RE.finditer(md_text))

    for idx, match in enumerate(headings):
        heading_text = match.group(2).strip().lower()
        start = match.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(md_text)
        sections[heading_text] = md_text[start:end]

    return sections


# ---------------------------------------------------------------------------
# ConventionLoader
# ---------------------------------------------------------------------------

class ConventionLoader:
    """Loads and validates table creation conventions from YAML or Markdown files.

    Loaded conventions are cached by file path (with content-hash based
    invalidation) so that repeated calls for the same file are fast.

    Usage::

        loader = ConventionLoader()
        convention = loader.load_auto("path/to/conventions.yaml")
    """

    def __init__(self) -> None:
        self._cache: dict[str, TableConvention] = {}
        self._cache_hashes: dict[str, str] = {}

    # -- Public loading API ------------------------------------------------

    def load_from_yaml(self, path: str) -> TableConvention:
        """Load conventions from a YAML file.

        Args:
            path: Filesystem path to the YAML convention file.

        Returns:
            A fully populated ``TableConvention`` instance.

        Raises:
            ConventionParseError: If the file is missing, unreadable, or
                contains invalid YAML.
        """
        try:
            import yaml
        except ImportError as exc:
            raise ConventionParseError(
                "PyYAML is required to load YAML convention files. "
                "Install it with: pip install pyyaml",
                path=path,
            ) from exc

        resolved = self._resolve_path(path)
        raw_text = self._read_file(resolved)

        try:
            data = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise ConventionParseError(
                f"Invalid YAML in '{resolved}': {exc}",
                path=resolved,
            ) from exc

        if not isinstance(data, dict):
            raise ConventionParseError(
                f"Expected a YAML mapping at the top level of '{resolved}', "
                f"got {type(data).__name__}.",
                path=resolved,
            )

        convention = self.load_from_dict(data)
        self._store_cache(resolved, convention)
        logger.info("Loaded YAML convention from '%s' (version %s)", resolved, convention.version)
        return convention

    def load_from_markdown(self, path: str) -> TableConvention:
        """Parse conventions from a Markdown specification document.

        The Markdown file is expected to contain headings such as
        ``## Naming``, ``## Data Types``, ``## Partition``, etc., each
        followed by either a YAML code block, a Markdown table, or
        bullet-list key-value pairs.

        Args:
            path: Filesystem path to the Markdown convention file.

        Returns:
            A ``TableConvention`` instance.

        Raises:
            ConventionParseError: If the file is missing or cannot be
                parsed at all.
        """
        resolved = self._resolve_path(path)
        raw_text = self._read_file(resolved)

        data = self._markdown_to_dict(raw_text)
        convention = self.load_from_dict(data)
        self._store_cache(resolved, convention)
        logger.info("Loaded Markdown convention from '%s' (version %s)", resolved, convention.version)
        return convention

    def load_from_dict(self, data: dict) -> TableConvention:
        """Load conventions from a Python dictionary.

        Missing keys fall back to the defaults defined on each Pydantic model,
        so callers only need to supply the fields they want to customise.

        Args:
            data: A dict whose structure mirrors ``TableConvention``.

        Returns:
            A ``TableConvention`` instance.
        """
        # Normalise top-level keys to lowercase to be forgiving of casing
        normalised = self._normalise_keys(data)

        # Build sub-models, tolerating missing sections
        naming_data = normalised.get("naming", {})
        types_data = normalised.get("data_types", normalised.get("datatypes", normalised.get("data types", {})))
        partition_data = normalised.get("partition", {})
        comments_data = normalised.get("comments", {})
        quality_data = normalised.get("quality", {})
        storage_data = normalised.get("storage", {})

        naming = self._safe_build(NamingConvention, naming_data)
        data_types = self._safe_build(DataTypeStandard, types_data)
        partition = self._safe_build(PartitionConvention, partition_data)
        comments = self._safe_build(CommentConvention, comments_data)
        quality = self._safe_build(QualityRule, quality_data)
        storage = self._safe_build(StorageConvention, storage_data)

        custom_rules = normalised.get("custom_rules", normalised.get("custom", {}))
        if not isinstance(custom_rules, dict):
            custom_rules = {}

        return TableConvention(
            version=str(normalised.get("version", "1.0.0")),
            description=str(normalised.get("description", "")),
            naming=naming,
            data_types=data_types,
            partition=partition,
            comments=comments,
            quality=quality,
            storage=storage,
            custom_rules=custom_rules,
        )

    def load_auto(self, path: str) -> TableConvention:
        """Auto-detect file format (YAML or Markdown) and load.

        Detection is based on the file extension first (``.yaml`` / ``.yml``
        vs ``.md`` / ``.markdown``), with a content-based fallback.

        Cached results are returned when the file has not changed since the
        last load.

        Args:
            path: Filesystem path to the convention file.

        Returns:
            A ``TableConvention`` instance.

        Raises:
            ConventionParseError: If the format cannot be determined or
                the file cannot be parsed.
        """
        resolved = self._resolve_path(path)

        # Check cache
        cached = self._get_cached(resolved)
        if cached is not None:
            logger.debug("Returning cached convention for '%s'", resolved)
            return cached

        ext = Path(resolved).suffix.lower()

        if ext in (".yaml", ".yml"):
            return self.load_from_yaml(resolved)
        if ext in (".md", ".markdown"):
            return self.load_from_markdown(resolved)

        # Fallback: peek at content
        raw = self._read_file(resolved).strip()
        if raw.startswith("---") or (raw.startswith("#") and ":" in raw):
            # Looks like YAML (either explicit document start or key: value)
            try:
                return self.load_from_yaml(resolved)
            except ConventionParseError:
                pass

        if raw.startswith("#"):
            return self.load_from_markdown(resolved)

        raise ConventionParseError(
            f"Cannot auto-detect format for '{resolved}'.  "
            "Use a .yaml/.yml or .md extension, or call load_from_yaml / "
            "load_from_markdown explicitly.",
            path=resolved,
        )

    # -- Validation --------------------------------------------------------

    def validate_convention(self, convention: TableConvention) -> list[str]:
        """Validate a convention for completeness and internal consistency.

        Returns a list of human-readable warning strings.  An empty list
        means the convention passed all checks.

        Args:
            convention: The convention to validate.

        Returns:
            A list of warning messages.
        """
        warnings: list[str] = []

        # 1. Version format
        if not re.match(r"^\d+\.\d+\.\d+", convention.version):
            warnings.append(
                f"Version '{convention.version}' does not look like semver (x.y.z)."
            )

        # 2. Naming checks
        naming = convention.naming
        valid_styles = {"snake_case", "camelCase", "UPPER_SNAKE"}
        if naming.case_style not in valid_styles:
            warnings.append(
                f"Unknown case_style '{naming.case_style}'.  "
                f"Expected one of: {', '.join(sorted(valid_styles))}."
            )
        if not naming.prefix_rules:
            warnings.append("No prefix_rules defined -- table naming will have no layer prefix enforcement.")
        if not naming.reserved_words:
            warnings.append("reserved_words list is empty -- reserved-word collisions will not be caught.")

        # 3. Data type checks
        dt = convention.data_types
        if not dt.logical_to_physical:
            warnings.append("logical_to_physical mapping is empty -- type validation will be skipped.")
        for logical, engine_map in dt.logical_to_physical.items():
            if not isinstance(engine_map, dict) or not engine_map:
                warnings.append(
                    f"Logical type '{logical}' has no engine mappings defined."
                )

        # 4. Partition checks
        part = convention.partition
        valid_granularities = {"daily", "hourly", "monthly"}
        if part.granularity not in valid_granularities:
            warnings.append(
                f"Unknown partition granularity '{part.granularity}'.  "
                f"Expected one of: {', '.join(sorted(valid_granularities))}."
            )
        for layer_name, days in part.retention_days_by_layer.items():
            if days < 0:
                warnings.append(
                    f"Negative retention_days ({days}) for layer '{layer_name}'."
                )

        # 5. Comment checks
        cmt = convention.comments
        if cmt.column_comment_min_length < 1:
            warnings.append("column_comment_min_length < 1 effectively disables length checks.")
        if cmt.table_comment_pattern:
            try:
                re.compile(cmt.table_comment_pattern)
            except re.error as exc:
                warnings.append(
                    f"table_comment_pattern is not a valid regex: {exc}"
                )

        # 6. Cross-section consistency
        prefix_layers = set(naming.prefix_rules.keys())
        partition_layers = set(part.partition_by_layer.keys())
        set(part.retention_days_by_layer.keys())
        uncovered = partition_layers - prefix_layers
        if uncovered:
            warnings.append(
                f"Layers in partition_by_layer have no prefix_rule: {', '.join(sorted(uncovered))}."
            )

        return warnings

    # -- Merging -----------------------------------------------------------

    def merge_conventions(
        self,
        base: TableConvention,
        override: TableConvention,
    ) -> TableConvention:
        """Merge two conventions, with *override* taking precedence.

        Performs a deep merge at the dict level so that partial overrides
        (e.g. only changing ``naming.case_style``) work without requiring
        the caller to repeat every field.

        Args:
            base: The base convention providing defaults.
            override: The override convention whose values win.

        Returns:
            A new ``TableConvention`` instance.
        """
        base_dict = base.model_dump()
        override_dict = override.model_dump(exclude_defaults=True)
        merged = _deep_merge(base_dict, override_dict)
        return self.load_from_dict(merged)

    # -- Cache access ------------------------------------------------------

    def get_convention(self, name: str) -> TableConvention:
        """Retrieve a cached convention by file path or short name.

        Args:
            name: Either the full file path used during loading, or the
                file's stem (e.g. ``"standard"`` for ``"standard.yaml"``).

        Returns:
            The cached ``TableConvention``.

        Raises:
            ConventionError: If no convention with that name is cached.
        """
        # Direct path match
        if name in self._cache:
            return self._cache[name]

        # Stem match
        for path, conv in self._cache.items():
            if Path(path).stem == name:
                return conv

        raise ConventionError(
            f"No cached convention found for '{name}'.  "
            f"Available: {', '.join(self._cache.keys()) or '(none)'}."
        )

    def clear_cache(self) -> None:
        """Remove all cached conventions."""
        self._cache.clear()
        self._cache_hashes.clear()

    # -- Internal helpers --------------------------------------------------

    def _resolve_path(self, path: str) -> str:
        """Resolve and verify a file path, raising on missing files."""
        resolved = str(Path(path).resolve())
        if not os.path.isfile(resolved):
            raise ConventionParseError(
                f"Convention file not found: '{resolved}'.",
                path=path,
            )
        return resolved

    def _read_file(self, path: str) -> str:
        """Read a file as UTF-8 text."""
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            raise ConventionParseError(
                f"Cannot read convention file '{path}': {exc}",
                path=path,
            ) from exc

    def _store_cache(self, path: str, convention: TableConvention) -> None:
        """Store a convention in the cache with a content hash."""
        try:
            self._cache_hashes[path] = _file_hash(path)
        except OSError:
            self._cache_hashes[path] = ""
        self._cache[path] = convention

    def _get_cached(self, path: str) -> TableConvention | None:
        """Return the cached convention if the file has not changed."""
        if path not in self._cache:
            return None
        try:
            current_hash = _file_hash(path)
        except OSError:
            return None
        if current_hash != self._cache_hashes.get(path):
            # File changed on disk -- invalidate
            del self._cache[path]
            del self._cache_hashes[path]
            return None
        return self._cache[path]

    @staticmethod
    def _safe_build(model_cls: type, data: Any) -> Any:
        """Build a Pydantic model, falling back to defaults on bad input."""
        if not isinstance(data, dict):
            return model_cls()
        try:
            return model_cls(**data)
        except Exception:
            # Tolerate extra / invalid fields by filtering them out
            valid_fields = set(model_cls.model_fields.keys())
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            try:
                return model_cls(**filtered)
            except Exception:
                logger.warning(
                    "Could not build %s from provided data; using defaults.",
                    model_cls.__name__,
                )
                return model_cls()

    @staticmethod
    def _normalise_keys(data: dict) -> dict:
        """Lowercase and strip top-level keys for forgiving parsing."""
        return {str(k).strip().lower(): v for k, v in data.items()}

    def _markdown_to_dict(self, md_text: str) -> dict:
        """Convert a Markdown convention document into a dict suitable for
        ``load_from_dict``.

        Supports three content styles within each section:
        1. Embedded YAML code blocks (preferred).
        2. Markdown tables (converted to list-of-dicts).
        3. Bullet-list key-value pairs (``- **key**: value``).
        """
        sections = _section_map(md_text)
        result: dict[str, Any] = {}

        # Top-level metadata (before any heading, or from specific headings)
        meta_kvs = _parse_md_key_value(md_text.split("##")[0] if "##" in md_text else md_text)
        if "version" in meta_kvs:
            result["version"] = meta_kvs["version"]
        if "description" in meta_kvs:
            result["description"] = meta_kvs["description"]

        # Map common heading keywords to convention sections
        heading_keywords: dict[str, list[str]] = {
            "naming": ["naming", "name", "names"],
            "data_types": ["data type", "datatype", "type", "types"],
            "partition": ["partition", "partitioning"],
            "comments": ["comment", "comments", "description"],
            "quality": ["quality", "constraint", "constraints"],
            "storage": ["storage", "format", "compression"],
            "custom": ["custom", "extra", "misc", "other"],
        }

        for heading, body in sections.items():
            heading_lower = heading.lower()

            # Determine which section this heading maps to
            target_key: str | None = None
            for key, keywords in heading_keywords.items():
                if any(kw in heading_lower for kw in keywords):
                    target_key = key
                    break

            if target_key is None:
                # Check for top-level metadata headings
                if "version" in heading_lower:
                    kvs = _parse_md_key_value(body)
                    if "version" in kvs:
                        result["version"] = kvs["version"]
                elif "metadata" in heading_lower or "overview" in heading_lower:
                    kvs = _parse_md_key_value(body)
                    result.update(kvs)
                continue

            # Try YAML block first
            yaml_data = _parse_md_yaml_block(body)
            if yaml_data is not None:
                result[target_key] = yaml_data
                continue

            # Try Markdown table
            table_rows = _parse_md_table(body)
            if table_rows:
                result[target_key] = self._table_rows_to_section(target_key, table_rows)
                continue

            # Try key-value bullets
            kvs = _parse_md_key_value(body)
            if kvs:
                result[target_key] = self._kvs_to_section(target_key, kvs)
                continue

        return result

    @staticmethod
    def _table_rows_to_section(section_key: str, rows: list[dict[str, str]]) -> dict:
        """Convert Markdown table rows into a dict appropriate for *section_key*."""
        if not rows:
            return {}

        headers = list(rows[0].keys())

        # For naming.prefix_rules / suffix_rules -- two-column "key | value"
        if len(headers) == 2:
            h0, h1 = headers
            return {
                h0.lower().replace(" ", "_"): r.get(h1, "")
                for r in rows
            }

        # For data_types.logical_to_physical -- first col = logical, rest = engines
        if section_key == "data_types" and len(headers) >= 3:
            logical_col = headers[0]
            engine_cols = headers[1:]
            mapping: dict[str, dict[str, str]] = {}
            for row in rows:
                logical = row.get(logical_col, "")
                mapping[logical] = {
                    eng.lower().replace(" ", "_"): row.get(eng, "")
                    for eng in engine_cols
                }
            return {"logical_to_physical": mapping}

        # Generic: return as list of dicts under a "rows" key
        return {"rows": rows}

    @staticmethod
    def _kvs_to_section(section_key: str, kvs: dict[str, str]) -> dict:
        """Convert key-value pairs into a dict appropriate for *section_key*."""
        result: dict[str, Any] = {}
        bool_fields: set[str] = {
            "table_comment_required", "column_comment_required",
            "primary_key_required",
        }
        int_fields: set[str] = {
            "column_comment_min_length",
        }

        for raw_key, raw_val in kvs.items():
            key = raw_key.lower().replace(" ", "_").replace("-", "_")
            if key in bool_fields:
                result[key] = raw_val.lower() in ("true", "yes", "1")
            elif key in int_fields:
                try:
                    result[key] = int(raw_val)
                except ValueError:
                    result[key] = raw_val
            else:
                result[key] = raw_val

        return result


# ---------------------------------------------------------------------------
# ConventionValidator
# ---------------------------------------------------------------------------

class ConventionValidator:
    """Validates a table schema against a loaded ``TableConvention``.

    Usage::

        validator = ConventionValidator()
        result = validator.validate_table(schema, convention)
        print(f"Score: {result.score}/100  Valid: {result.is_valid}")
    """

    # Penalty weights used when computing the compliance score.
    _ERROR_PENALTY = 15
    _WARNING_PENALTY = 5
    _INFO_PENALTY = 1

    # -- Public API --------------------------------------------------------

    def validate_table(
        self,
        schema: Any,
        convention: TableConvention,
        target_engine: str = "hive",
    ) -> ValidationResult:
        """Validate a complete table schema against a convention.

        Accepts either the Pydantic ``TableSchema`` from ``core.schemas`` or
        the dataclass ``TableSchema`` from ``warehouse.schema_manager``.

        Args:
            schema: The table schema to validate.
            convention: The convention to validate against.
            target_engine: Target engine name used for data-type checks.

        Returns:
            A ``ValidationResult`` with violations, warnings, and a score.
        """
        violations: list[Violation] = []
        warnings: list[str] = []

        table_name = self._get_attr(schema, "table_name", "unknown_table")
        table_comment = self._get_attr(schema, "comment", "") or ""
        columns = self._get_attr(schema, "columns", [])

        # Extract column info in a uniform way
        col_infos = self._normalise_columns(columns)

        # 1. Naming
        naming_violations = self.validate_naming(table_name, col_infos, convention.naming)
        violations.extend(naming_violations)

        # 2. Data types
        type_violations = self.validate_data_types(col_infos, convention.data_types, target_engine)
        violations.extend(type_violations)

        # 3. Comments
        comment_violations = self.validate_comments(table_comment, col_infos, convention.comments)
        violations.extend(comment_violations)

        # 4. Partition (basic check)
        layer = self._detect_layer(table_name, convention.naming)
        partition_violations = self._validate_partition(schema, layer, convention.partition)
        violations.extend(partition_violations)

        # 5. Quality
        quality_violations = self._validate_quality(table_name, col_infos, convention.quality)
        violations.extend(quality_violations)

        # 6. Storage
        storage_violations = self._validate_storage(schema, target_engine, convention.storage)
        violations.extend(storage_violations)

        # Warnings from convention-level validation
        loader = ConventionLoader()
        convention_warnings = loader.validate_convention(convention)
        warnings.extend(convention_warnings)

        # Compute score
        error_count = sum(1 for v in violations if v.severity == "error")
        warning_count = sum(1 for v in violations if v.severity == "warning")
        info_count = sum(1 for v in violations if v.severity == "info")
        penalty = (
            error_count * self._ERROR_PENALTY
            + warning_count * self._WARNING_PENALTY
            + info_count * self._INFO_PENALTY
        )
        score = max(0, 100 - penalty)
        is_valid = error_count == 0

        return ValidationResult(
            is_valid=is_valid,
            violations=violations,
            warnings=warnings,
            score=score,
        )

    def validate_naming(
        self,
        table_name: str,
        columns: list[_ColumnInfo],
        naming: NamingConvention,
    ) -> list[Violation]:
        """Check naming conventions for a table and its columns.

        Args:
            table_name: The table name to check.
            columns: Normalised column info objects.
            naming: The naming convention rules.

        Returns:
            A list of naming-related violations.
        """
        violations: list[Violation] = []

        # -- Table name case style --
        if not _is_valid_case(table_name, naming.case_style):
            expected = _normalise_case(table_name, naming.case_style)
            violations.append(Violation(
                severity="error",
                rule="naming.table_case",
                message=(
                    f"Table name '{table_name}' does not conform to "
                    f"'{naming.case_style}' convention."
                ),
                location=table_name,
                suggestion=f"Rename to '{expected}'.",
            ))

        # -- Table name prefix --
        layer = self._detect_layer_from_name(table_name, naming.prefix_rules)
        if layer is not None:
            expected_prefix = naming.prefix_rules.get(layer, "")
            if expected_prefix and not table_name.startswith(expected_prefix):
                violations.append(Violation(
                    severity="error",
                    rule="naming.table_prefix",
                    message=(
                        f"Table '{table_name}' is in layer '{layer}' but does not "
                        f"start with the required prefix '{expected_prefix}'."
                    ),
                    location=table_name,
                    suggestion=f"Rename to '{expected_prefix}{table_name}'.",
                ))

        # -- Reserved words in table name --
        name_parts = set(re.split(r"[_\-\s]+", table_name.lower()))
        reserved_hit = name_parts & {w.lower() for w in naming.reserved_words}
        if reserved_hit:
            violations.append(Violation(
                severity="error",
                rule="naming.reserved_word_table",
                message=f"Table name contains reserved word(s): {', '.join(sorted(reserved_hit))}.",
                location=table_name,
                suggestion="Choose a different name that avoids reserved SQL keywords.",
            ))

        # -- Column-level checks --
        for col in columns:
            col_name = col.name

            # Case style
            if not _is_valid_case(col_name, naming.case_style):
                expected = _normalise_case(col_name, naming.case_style)
                violations.append(Violation(
                    severity="warning",
                    rule="naming.column_case",
                    message=(
                        f"Column '{col_name}' does not conform to "
                        f"'{naming.case_style}' convention."
                    ),
                    location=col_name,
                    suggestion=f"Rename to '{expected}'.",
                ))

            # Reserved words
            col_parts = set(re.split(r"[_\-\s]+", col_name.lower()))
            col_reserved = col_parts & {w.lower() for w in naming.reserved_words}
            if col_reserved:
                violations.append(Violation(
                    severity="error",
                    rule="naming.reserved_word_column",
                    message=(
                        f"Column '{col_name}' contains reserved word(s): "
                        f"{', '.join(sorted(col_reserved))}."
                    ),
                    location=col_name,
                    suggestion="Rename the column to avoid SQL keyword conflicts.",
                ))

        return violations

    def validate_data_types(
        self,
        columns: list[_ColumnInfo],
        standards: DataTypeStandard,
        target_engine: str,
    ) -> list[Violation]:
        """Check data type standards for a list of columns.

        Args:
            columns: Normalised column info objects.
            standards: The data type standard rules.
            target_engine: Target engine name (e.g. ``"hive"``, ``"clickhouse"``).

        Returns:
            A list of data-type-related violations.
        """
        violations: list[Violation] = []
        engine_lower = target_engine.lower()
        forbidden_upper = {t.upper() for t in standards.forbidden_types}

        for col in columns:
            col_type_upper = col.data_type.upper().strip()

            # Check forbidden types (exact or prefix match)
            for forbidden in forbidden_upper:
                if col_type_upper == forbidden or col_type_upper.startswith(forbidden + "("):
                    violations.append(Violation(
                        severity="error",
                        rule="datatype.forbidden",
                        message=(
                            f"Column '{col.name}' uses forbidden data type "
                            f"'{col.data_type}'."
                        ),
                        location=col.name,
                        suggestion=(
                            "Replace with an allowed type from the logical_to_physical mapping."
                        ),
                    ))
                    break

            # Check if the type is in the known physical types for the engine
            known_types_for_engine: set[str] = set()
            for _logical, engine_map in standards.logical_to_physical.items():
                physical = engine_map.get(engine_lower, "")
                if physical:
                    known_types_for_engine.add(physical.upper())

            if known_types_for_engine and col_type_upper:
                # Extract base type (strip params like VARCHAR(255) -> VARCHAR)
                base_type = re.split(r"[(\s]", col_type_upper)[0]
                # Warn if the type is not in the standard mapping for this engine
                # AND it is not already flagged as forbidden (avoids double-reporting).
                if (
                    base_type not in known_types_for_engine
                    and base_type not in forbidden_upper
                ):
                    violations.append(Violation(
                        severity="warning",
                        rule="datatype.unknown_for_engine",
                        message=(
                            f"Column '{col.name}' uses type '{col.data_type}' which is "
                            f"not in the standard mapping for engine '{target_engine}'."
                        ),
                        location=col.name,
                        suggestion="Consider using a type from the logical_to_physical mapping.",
                    ))

        return violations

    def validate_comments(
        self,
        table_comment: str,
        columns: list[_ColumnInfo],
        rules: CommentConvention,
    ) -> list[Violation]:
        """Check comment requirements for a table and its columns.

        Args:
            table_comment: The table-level comment string.
            columns: Normalised column info objects.
            rules: The comment convention rules.

        Returns:
            A list of comment-related violations.
        """
        violations: list[Violation] = []

        # Table comment
        if rules.table_comment_required and not (table_comment and table_comment.strip()):
            violations.append(Violation(
                severity="error",
                rule="comment.table_required",
                message="Table comment is required but missing or empty.",
                location="(table)",
                suggestion="Add a descriptive COMMENT to the CREATE TABLE statement.",
            ))

        if (
            table_comment
            and rules.table_comment_pattern
        ):
            try:
                if not re.search(rules.table_comment_pattern, table_comment):
                    violations.append(Violation(
                        severity="warning",
                        rule="comment.table_pattern",
                        message=(
                            f"Table comment does not match the required pattern "
                            f"'{rules.table_comment_pattern}'."
                        ),
                        location="(table)",
                        suggestion=(
                            f"Adjust the comment to match: '{rules.table_comment_pattern}'."
                        ),
                    ))
            except re.error:
                pass  # Bad regex in convention -- already caught by validate_convention

        # Column comments
        for col in columns:
            col_comment = col.comment or ""
            if rules.column_comment_required and not col_comment.strip():
                violations.append(Violation(
                    severity="warning",
                    rule="comment.column_required",
                    message=f"Column '{col.name}' is missing a required comment.",
                    location=col.name,
                    suggestion="Add a COMMENT describing this column's purpose.",
                ))
            elif col_comment and len(col_comment.strip()) < rules.column_comment_min_length:
                violations.append(Violation(
                    severity="info",
                    rule="comment.column_min_length",
                    message=(
                        f"Column '{col.name}' comment is too short "
                        f"(minimum {rules.column_comment_min_length} characters)."
                    ),
                    location=col.name,
                    suggestion="Provide a more descriptive comment.",
                ))

        return violations

    # -- Private validation helpers ----------------------------------------

    def _validate_partition(
        self,
        schema: Any,
        layer: str | None,
        partition: PartitionConvention,
    ) -> list[Violation]:
        """Check partitioning conventions."""
        violations: list[Violation] = []

        partition_keys = self._get_attr(schema, "partition_keys", [])
        if isinstance(partition_keys, list) and partition_keys:
            # Has partition keys -- check against convention
            if layer and layer.upper() in partition.partition_by_layer:
                expected_col = partition.partition_by_layer[layer.upper()]
                if expected_col not in partition_keys:
                    violations.append(Violation(
                        severity="warning",
                        rule="partition.column_mismatch",
                        message=(
                            f"Layer '{layer}' expects partition column "
                            f"'{expected_col}', but table uses: "
                            f"{', '.join(partition_keys)}."
                        ),
                        location=self._get_attr(schema, "table_name", "(table)"),
                        suggestion=f"Include '{expected_col}' in the partition keys.",
                    ))
        else:
            # No partition keys -- warn if the layer normally requires partitioning
            if layer and layer.upper() in partition.partition_by_layer:
                violations.append(Violation(
                    severity="info",
                    rule="partition.missing",
                    message=(
                        f"Table in layer '{layer}' has no partition keys.  "
                        f"Convention expects partition by '{partition.partition_by_layer[layer.upper()]}'."
                    ),
                    location=self._get_attr(schema, "table_name", "(table)"),
                    suggestion="Add a PARTITIONED BY clause.",
                ))

        return violations

    def _validate_quality(
        self,
        table_name: str,
        columns: list[_ColumnInfo],
        quality: QualityRule,
    ) -> list[Violation]:
        """Check data quality constraints."""
        violations: list[Violation] = []

        # Primary key required
        if quality.primary_key_required:
            has_pk = any(col.is_primary_key for col in columns)
            if not has_pk:
                violations.append(Violation(
                    severity="warning",
                    rule="quality.primary_key_required",
                    message="Table has no primary key column defined.",
                    location=table_name,
                    suggestion="Designate at least one column as the primary key.",
                ))

        # NOT NULL columns
        for col in columns:
            if _matches_any_pattern(col.name, quality.not_null_columns) and col.nullable:
                violations.append(Violation(
                    severity="warning",
                    rule="quality.not_null",
                    message=(
                        f"Column '{col.name}' matches a NOT NULL pattern "
                        f"but is marked as nullable."
                    ),
                    location=col.name,
                    suggestion="Set the column to NOT NULL.",
                ))

        # UNIQUE columns
        for col in columns:
            if _matches_any_pattern(col.name, quality.unique_constraints):
                # We cannot fully validate uniqueness from schema alone,
                # but we flag it as an info note.
                violations.append(Violation(
                    severity="info",
                    rule="quality.unique_hint",
                    message=(
                        f"Column '{col.name}' matches a unique-constraint pattern.  "
                        "Ensure a UNIQUE constraint or index is defined."
                    ),
                    location=col.name,
                ))

        return violations

    def _validate_storage(
        self,
        schema: Any,
        target_engine: str,
        storage: StorageConvention,
    ) -> list[Violation]:
        """Check storage format and compression conventions."""
        violations: list[Violation] = []
        engine_lower = target_engine.lower()

        storage_format = self._get_attr(schema, "storage_format", "")
        if storage_format and engine_lower in storage.default_format_by_engine:
            expected_format = storage.default_format_by_engine[engine_lower]
            if storage_format.upper() != expected_format.upper():
                violations.append(Violation(
                    severity="info",
                    rule="storage.format",
                    message=(
                        f"Storage format '{storage_format}' differs from the "
                        f"recommended '{expected_format}' for engine '{target_engine}'."
                    ),
                    location=self._get_attr(schema, "table_name", "(table)"),
                    suggestion=f"Consider using '{expected_format}' for better compatibility.",
                ))

        compression = self._get_attr(schema, "compression", "")
        if compression and engine_lower in storage.compression_by_engine:
            expected_compression = storage.compression_by_engine[engine_lower]
            if compression.upper() != expected_compression.upper():
                violations.append(Violation(
                    severity="info",
                    rule="storage.compression",
                    message=(
                        f"Compression '{compression}' differs from the recommended "
                        f"'{expected_compression}' for engine '{target_engine}'."
                    ),
                    location=self._get_attr(schema, "table_name", "(table)"),
                    suggestion=f"Consider using '{expected_compression}' codec.",
                ))

        return violations

    # -- Utility -----------------------------------------------------------

    @staticmethod
    def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
        """Get an attribute from either a Pydantic model, dataclass, or dict."""
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    @staticmethod
    def _detect_layer(
        table_name: str,
        naming: NamingConvention,
    ) -> str | None:
        """Detect the warehouse layer from a table name based on prefix rules."""
        for layer, prefix in naming.prefix_rules.items():
            if table_name.startswith(prefix):
                return layer
        return None

    @staticmethod
    def _detect_layer_from_name(
        table_name: str,
        prefix_rules: dict[str, str],
    ) -> str | None:
        """Detect the layer from a table name using prefix rules."""
        for layer, prefix in prefix_rules.items():
            if table_name.startswith(prefix):
                return layer
        return None

    @staticmethod
    def _normalise_columns(columns: Any) -> list[_ColumnInfo]:
        """Convert various column representations into ``_ColumnInfo`` objects."""
        result: list[_ColumnInfo] = []
        if not columns:
            return result

        for col in columns:
            if isinstance(col, _ColumnInfo):
                result.append(col)
            elif isinstance(col, dict):
                result.append(_ColumnInfo(
                    name=col.get("name", ""),
                    data_type=col.get("data_type", col.get("type", "STRING")),
                    nullable=col.get("nullable", True),
                    is_primary_key=col.get("is_primary_key", False),
                    comment=col.get("comment", ""),
                ))
            elif hasattr(col, "name"):
                # Dataclass or Pydantic model (ColumnSchema, ColumnInfo)
                result.append(_ColumnInfo(
                    name=getattr(col, "name", ""),
                    data_type=getattr(col, "data_type", getattr(col, "type", "STRING")),
                    nullable=getattr(col, "nullable", True),
                    is_primary_key=getattr(col, "is_primary_key", False),
                    comment=getattr(col, "comment", "") or "",
                ))
            else:
                logger.warning("Skipping unrecognised column object: %r", col)

        return result


# ---------------------------------------------------------------------------
# Internal lightweight column info (decouples validator from specific models)
# ---------------------------------------------------------------------------

class _ColumnInfo:
    """Lightweight internal column representation used by the validator.

    This decouples the validator from both the Pydantic ``ColumnInfo`` in
    ``core.schemas`` and the dataclass ``ColumnSchema`` in
    ``warehouse.schema_manager``.
    """

    __slots__ = ("comment", "data_type", "is_primary_key", "name", "nullable")

    def __init__(
        self,
        name: str,
        data_type: str = "STRING",
        nullable: bool = True,
        is_primary_key: bool = False,
        comment: str = "",
    ) -> None:
        self.name = name
        self.data_type = data_type
        self.nullable = nullable
        self.is_primary_key = is_primary_key
        self.comment = comment

    def __repr__(self) -> str:
        return (
            f"_ColumnInfo(name={self.name!r}, data_type={self.data_type!r}, "
            f"nullable={self.nullable!r}, is_primary_key={self.is_primary_key!r})"
        )
