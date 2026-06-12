"""
DataForge AI - LangChain structured output parsers.

Provides Pydantic models and LangChain-compatible output parsers for converting
raw LLM responses into strongly typed, structured data.  Covers SQL generation,
data modeling, query optimization, schema review, and lineage analysis outputs.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Sub-models used across multiple output types
# ---------------------------------------------------------------------------


class FactTableDef(BaseModel):
    """Definition of a single fact table produced by the modeling advisor.

    Attributes:
        name: Fully qualified fact table name.
        grain: The grain (row-level meaning) of the fact table.
        measures: List of measure / metric column names.
        foreign_keys: List of foreign-key references expressed as
            ``table.column`` strings.
    """

    name: str = Field(description="Fully qualified fact table name.")
    grain: str = Field(description="The grain of the fact table (one row per ...).")
    measures: list[str] = Field(
        default_factory=list,
        description="List of measure / metric column names.",
    )
    foreign_keys: list[str] = Field(
        default_factory=list,
        description="Foreign-key references expressed as 'table.column' strings.",
    )


class DimensionTableDef(BaseModel):
    """Definition of a single dimension table produced by the modeling advisor.

    Attributes:
        name: Fully qualified dimension table name.
        attributes: List of attribute column names.
        hierarchies: List of hierarchy descriptions (e.g. ``country > state > city``).
    """

    name: str = Field(description="Fully qualified dimension table name.")
    attributes: list[str] = Field(
        default_factory=list,
        description="List of attribute column names.",
    )
    hierarchies: list[str] = Field(
        default_factory=list,
        description="Hierarchy descriptions (e.g. 'country > state > city').",
    )


class Bottleneck(BaseModel):
    """A single performance bottleneck identified during query optimization.

    Attributes:
        description: Human-readable description of the bottleneck.
        severity: Severity level -- one of ``low``, ``medium``, ``high``,
            ``critical``.
        location: Where in the query the bottleneck occurs (e.g. clause or
            sub-query identifier).
    """

    description: str = Field(description="Human-readable description of the bottleneck.")
    severity: str = Field(
        default="medium",
        description="Severity level: low, medium, high, or critical.",
    )
    location: str = Field(
        default="",
        description="Where in the query the bottleneck occurs.",
    )

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: Any) -> str:
        """Coerce severity to one of the accepted values."""
        if not isinstance(v, str):
            return "medium"
        v_lower = v.strip().lower()
        if v_lower in ("low", "medium", "high", "critical"):
            return v_lower
        return "medium"


class Change(BaseModel):
    """A single change applied during query optimization.

    Attributes:
        description: What was changed.
        expected_improvement: Expected performance improvement from this change.
    """

    description: str = Field(description="What was changed.")
    expected_improvement: str = Field(
        default="",
        description="Expected performance improvement from this change.",
    )


class IndexRec(BaseModel):
    """A recommended index for query optimization.

    Attributes:
        table_name: The table the index applies to.
        index_name: Suggested index name.
        columns: Ordered list of columns to include in the index.
        index_type: Type of index (btree, bitmap, hash, etc.).
        rationale: Why this index is recommended.
    """

    table_name: str = Field(description="The table the index applies to.")
    index_name: str = Field(description="Suggested index name.")
    columns: list[str] = Field(
        default_factory=list,
        description="Ordered list of columns to include in the index.",
    )
    index_type: str = Field(default="btree", description="Type of index.")
    rationale: str = Field(default="", description="Why this index is recommended.")


class PartitionRec(BaseModel):
    """A recommended partitioning strategy for a table.

    Attributes:
        table_name: The table to partition.
        partition_columns: Columns to partition on.
        granularity: Partition granularity (hourly, daily, monthly, yearly).
        description: Full description of the strategy.
    """

    table_name: str = Field(description="The table to partition.")
    partition_columns: list[str] = Field(
        default_factory=list,
        description="Columns to partition on.",
    )
    granularity: str = Field(default="daily", description="Partition granularity.")
    description: str = Field(default="", description="Full description of the strategy.")


class Finding(BaseModel):
    """A single finding from a schema review.

    Attributes:
        severity: Severity level -- one of ``info``, ``warning``, ``error``,
            ``critical``.
        category: The review category (naming, data_types, constraints,
            partitioning, indexing, scalability, etc.).
        description: Human-readable description of the finding.
        recommendation: Actionable recommendation to address the finding.
    """

    severity: str = Field(description="Severity level: info, warning, error, or critical.")
    category: str = Field(
        default="general",
        description="Review category (naming, data_types, constraints, etc.).",
    )
    description: str = Field(description="Human-readable description of the finding.")
    recommendation: str = Field(
        default="",
        description="Actionable recommendation to address the finding.",
    )

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: Any) -> str:
        """Coerce severity to one of the accepted values."""
        if not isinstance(v, str):
            return "warning"
        v_lower = v.strip().lower()
        if v_lower in ("info", "warning", "error", "critical"):
            return v_lower
        return "warning"


class ColumnMapping(BaseModel):
    """A single column-level lineage mapping between source and target.

    Attributes:
        source_table: The source table name.
        source_column: The source column name.
        target_table: The target table name.
        target_column: The target column name.
        transformation: Description of any transformation applied.
    """

    source_table: str = Field(description="The source table name.")
    source_column: str = Field(description="The source column name.")
    target_table: str = Field(description="The target table name.")
    target_column: str = Field(description="The target column name.")
    transformation: str = Field(
        default="",
        description="Description of any transformation applied.",
    )


# ---------------------------------------------------------------------------
# Primary output models
# ---------------------------------------------------------------------------


class SQLGenerationOutput(BaseModel):
    """Structured output for SQL generation tasks.

    Attributes:
        sql: The generated SQL query.
        explanation: Natural language explanation of what the query does.
        confidence: Self-assessed confidence score between 0.0 and 1.0.
        assumptions: Assumptions made when the question was ambiguous.
        warnings: Potential issues or caveats with the generated query.
    """

    sql: str = Field(description="The generated SQL query.")
    explanation: str = Field(
        default="",
        description="Natural language explanation of what the query does.",
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Self-assessed confidence score between 0.0 and 1.0.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions made when the question was ambiguous.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Potential issues or caveats with the generated query.",
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: Any) -> float:
        """Ensure confidence is within [0, 1]."""
        try:
            val = float(v)
        except (TypeError, ValueError):
            return 0.8
        return max(0.0, min(1.0, val))


class DataModelingOutput(BaseModel):
    """Structured output for data modeling tasks.

    Attributes:
        fact_tables: List of fact table definitions.
        dimension_tables: List of dimension table definitions.
        ddl_statements: DDL statements to create the modeled tables.
        rationale: Design rationale explaining key decisions.
        confidence: Self-assessed confidence score between 0.0 and 1.0.
    """

    fact_tables: list[FactTableDef] = Field(
        default_factory=list,
        description="List of fact table definitions.",
    )
    dimension_tables: list[DimensionTableDef] = Field(
        default_factory=list,
        description="List of dimension table definitions.",
    )
    ddl_statements: list[str] = Field(
        default_factory=list,
        description="DDL statements to create the modeled tables.",
    )
    rationale: str = Field(
        default="",
        description="Design rationale explaining key decisions.",
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Self-assessed confidence score between 0.0 and 1.0.",
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: Any) -> float:
        """Ensure confidence is within [0, 1]."""
        try:
            val = float(v)
        except (TypeError, ValueError):
            return 0.8
        return max(0.0, min(1.0, val))


class SQLOptimizationOutput(BaseModel):
    """Structured output for SQL optimization tasks.

    Attributes:
        original_complexity: Complexity classification of the original query.
        bottlenecks: Performance bottlenecks identified in the original query.
        optimized_sql: The rewritten, optimized SQL query.
        changes: List of changes applied during optimization.
        index_recommendations: Recommended indexes to support the query.
        partition_recommendations: Recommended partitioning strategies.
    """

    original_complexity: str = Field(
        default="moderate",
        description="Complexity of the original query: simple, moderate, or complex.",
    )
    bottlenecks: list[Bottleneck] = Field(
        default_factory=list,
        description="Performance bottlenecks identified in the original query.",
    )
    optimized_sql: str = Field(
        default="",
        description="The rewritten, optimized SQL query.",
    )
    changes: list[Change] = Field(
        default_factory=list,
        description="List of changes applied during optimization.",
    )
    index_recommendations: list[IndexRec] = Field(
        default_factory=list,
        description="Recommended indexes to support the query.",
    )
    partition_recommendations: list[PartitionRec] = Field(
        default_factory=list,
        description="Recommended partitioning strategies.",
    )

    @field_validator("original_complexity", mode="before")
    @classmethod
    def _normalize_complexity(cls, v: Any) -> str:
        """Coerce complexity to one of the accepted values."""
        if not isinstance(v, str):
            return "moderate"
        v_lower = v.strip().lower()
        if v_lower in ("simple", "moderate", "complex"):
            return v_lower
        return "moderate"


class SchemaReviewOutput(BaseModel):
    """Structured output for schema review tasks.

    Attributes:
        score: Overall quality score from 0 (poor) to 100 (excellent).
        findings: Individual findings / issues discovered during review.
        summary: High-level summary of the review results.
    """

    score: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Overall quality score from 0 (poor) to 100 (excellent).",
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description="Individual findings / issues discovered during review.",
    )
    summary: str = Field(
        default="",
        description="High-level summary of the review results.",
    )

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, v: Any) -> int:
        """Ensure score is within [0, 100]."""
        try:
            val = int(v)
        except (TypeError, ValueError):
            return 50
        return max(0, min(100, val))


class LineageAnalysisOutput(BaseModel):
    """Structured output for data lineage analysis tasks.

    Attributes:
        source_tables: Tables identified as data sources.
        target_tables: Tables identified as data targets.
        column_mappings: Column-level mappings between source and target tables.
        transformation_descriptions: Human-readable descriptions of
            transformations applied along the lineage path.
    """

    source_tables: list[str] = Field(
        default_factory=list,
        description="Tables identified as data sources.",
    )
    target_tables: list[str] = Field(
        default_factory=list,
        description="Tables identified as data targets.",
    )
    column_mappings: list[ColumnMapping] = Field(
        default_factory=list,
        description="Column-level mappings between source and target tables.",
    )
    transformation_descriptions: list[str] = Field(
        default_factory=list,
        description="Human-readable descriptions of transformations applied.",
    )


# ---------------------------------------------------------------------------
# Format instructions for each output type
# ---------------------------------------------------------------------------

_FORMAT_INSTRUCTIONS: dict[str, str] = {
    "sql_generation": (
        "Return your response as a JSON object with the following structure:\n"
        "{\n"
        '  "sql": "<the generated SQL query>",\n'
        '  "explanation": "<natural language explanation of the query>",\n'
        '  "confidence": <float between 0.0 and 1.0>,\n'
        '  "assumptions": ["<assumption 1>", "<assumption 2>", ...],\n'
        '  "warnings": ["<warning 1>", "<warning 2>", ...]\n'
        "}\n"
        "Wrap the JSON in a ```json code block.  Ensure the SQL is valid and "
        "the confidence score reflects your certainty that the query correctly "
        "answers the question."
    ),
    "data_modeling": (
        "Return your response as a JSON object with the following structure:\n"
        "{\n"
        '  "fact_tables": [\n'
        '    {"name": "<table_name>", "grain": "<grain>", '
        '"measures": [...], "foreign_keys": [...]}],\n'
        '  "dimension_tables": [\n'
        '    {"name": "<table_name>", "attributes": [...], '
        '"hierarchies": [...]}],\n'
        '  "ddl_statements": ["<CREATE TABLE ...>", ...],\n'
        '  "rationale": "<design rationale>",\n'
        '  "confidence": <float between 0.0 and 1.0>\n'
        "}\n"
        "Wrap the JSON in a ```json code block."
    ),
    "sql_optimization": (
        "Return your response as a JSON object with the following structure:\n"
        "{\n"
        '  "original_complexity": "<simple|moderate|complex>",\n'
        '  "bottlenecks": [\n'
        '    {"description": "<desc>", "severity": "<low|medium|high|critical>", '
        '"location": "<clause or subquery>"}],\n'
        '  "optimized_sql": "<the rewritten SQL>",\n'
        '  "changes": [\n'
        '    {"description": "<what was changed>", '
        '"expected_improvement": "<expected gain>"}],\n'
        '  "index_recommendations": [\n'
        '    {"table_name": "<table>", "index_name": "<name>", '
        '"columns": [...], "index_type": "<type>", "rationale": "<why>"}],\n'
        '  "partition_recommendations": [\n'
        '    {"table_name": "<table>", "partition_columns": [...], '
        '"granularity": "<granularity>", "description": "<desc>"}]\n'
        "}\n"
        "Wrap the JSON in a ```json code block."
    ),
    "schema_review": (
        "Return your response as a JSON object with the following structure:\n"
        "{\n"
        '  "score": <int 0-100>,\n'
        '  "findings": [\n'
        '    {"severity": "<info|warning|error|critical>", '
        '"category": "<category>", "description": "<desc>", '
        '"recommendation": "<action>"}],\n'
        '  "summary": "<high-level summary>"\n'
        "}\n"
        "Wrap the JSON in a ```json code block."
    ),
    "lineage_analysis": (
        "Return your response as a JSON object with the following structure:\n"
        "{\n"
        '  "source_tables": ["<table1>", "<table2>", ...],\n'
        '  "target_tables": ["<table1>", "<table2>", ...],\n'
        '  "column_mappings": [\n'
        '    {"source_table": "<t>", "source_column": "<c>", '
        '"target_table": "<t>", "target_column": "<c>", '
        '"transformation": "<desc>"}],\n'
        '  "transformation_descriptions": ["<desc 1>", "<desc 2>", ...]\n'
        "}\n"
        "Wrap the JSON in a ```json code block."
    ),
}


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------


def _extract_json_from_text(text: str) -> str:
    """Extract a JSON payload from raw LLM output.

    Tries the following strategies in order:

    1. Find a `````json ... ````` fenced code block.
    2. Find any ````` ... ````` fenced code block.
    3. Locate the outermost ``{ ... }`` in the text.

    Args:
        text: The raw text response from the LLM.

    Returns:
        The extracted JSON string.

    Raises:
        ValueError: If no JSON object can be located in the text.
    """
    # Strategy 1: ```json ... ``` block
    match = re.search(r"```json\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Strategy 2: ``` ... ``` block (generic)
    match = re.search(r"```\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    # Strategy 3: outermost { ... }
    # Find the first { and the last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1]

    raise ValueError("No JSON object found in the LLM response.")


# ---------------------------------------------------------------------------
# Structured output parser
# ---------------------------------------------------------------------------


class StructuredOutputParser:
    """Parser that converts raw LLM text into a typed Pydantic model.

    Wraps the parsing logic with fallback heuristics and clear error
    reporting.  Designed to work both as a standalone parser and as a
    building block for LangChain's ``with_structured_output()`` pattern.

    Args:
        output_model: The Pydantic ``BaseModel`` subclass to parse into.
        output_type: An optional string key (e.g. ``sql_generation``) used
            to look up format instructions.

    Usage::

        parser = StructuredOutputParser(
            output_model=SQLGenerationOutput,
            output_type="sql_generation",
        )
        result = parser.parse(raw_llm_text)
        print(result.sql)
    """

    def __init__(
        self,
        output_model: type[T],
        output_type: str | None = None,
    ) -> None:
        self._output_model = output_model
        self._output_type = output_type

    # -- Public API ---------------------------------------------------------

    @property
    def output_model(self) -> type[T]:
        """Return the Pydantic model class this parser targets."""
        return self._output_model

    def get_format_instructions(self) -> str:
        """Return prompt-level formatting instructions for the target output type.

        Returns:
            A string containing JSON schema and formatting guidelines that
            should be injected into the LLM prompt to steer structured output.
        """
        if self._output_type and self._output_type in _FORMAT_INSTRUCTIONS:
            return _FORMAT_INSTRUCTIONS[self._output_type]

        # Fallback: generate instructions from the Pydantic model schema
        return self._generate_format_instructions_from_schema()

    def parse(self, text: str) -> T:
        """Parse raw LLM output text into the target Pydantic model.

        Attempts JSON extraction first, then falls back to heuristic field
        extraction if JSON parsing fails.

        Args:
            text: The raw text response from the LLM.

        Returns:
            An instance of the configured Pydantic model.

        Raises:
            ValueError: If the text cannot be parsed into the target model
                even after fallback strategies are exhausted.
        """
        # Attempt 1: extract JSON and validate with Pydantic
        try:
            json_str = _extract_json_from_text(text)
            data = json.loads(json_str)
            return self._output_model.model_validate(data)
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            logger.debug("Primary JSON parse failed: %s", exc)

        # Attempt 2: try direct Pydantic validation on the whole text as JSON
        try:
            data = json.loads(text.strip())
            return self._output_model.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.debug("Direct JSON parse failed: %s", exc)

        # Attempt 3: fallback heuristic extraction
        try:
            fallback_data = self._fallback_extract(text)
            return self._output_model.model_validate(fallback_data)
        except (ValidationError, Exception) as exc:
            logger.warning("Fallback extraction also failed: %s", exc)

        raise ValueError(
            f"Unable to parse LLM output into {self._output_model.__name__}.  "
            f"Ensure the LLM response contains a valid JSON object matching "
            f"the expected schema."
        )

    # -- Internal helpers ---------------------------------------------------

    def _generate_format_instructions_from_schema(self) -> str:
        """Generate format instructions from the Pydantic model's JSON schema.

        Returns:
            A string with JSON structure guidelines derived from the model
            schema.
        """
        try:
            schema = self._output_model.model_json_schema()
        except Exception:
            return (
                f"Return your response as a JSON object matching the "
                f"{self._output_model.__name__} schema.  "
                f"Wrap the JSON in a ```json code block."
            )

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        lines = [
            "Return your response as a JSON object with the following fields:",
            "{",
        ]
        for field_name, field_info in properties.items():
            field_type = field_info.get("type", "any")
            description = field_info.get("description", "")
            is_required = field_name in required
            req_marker = " (required)" if is_required else " (optional)"
            lines.append(
                f'  "{field_name}": <{field_type}>  '
                f"-- {description}{req_marker},"
            )
        lines.append("}")
        lines.append("Wrap the JSON in a ```json code block.")

        return "\n".join(lines)

    def _fallback_extract(self, text: str) -> dict[str, Any]:
        """Attempt to extract fields heuristically from unstructured text.

        This is a best-effort strategy used when JSON extraction fails.
        It looks for common patterns in LLM output such as SQL code blocks,
        confidence scores, and list items.

        Args:
            text: The raw LLM response text.

        Returns:
            A dictionary with heuristically extracted field values.
        """
        model_name = self._output_model.__name__
        data: dict[str, Any] = {}

        if model_name == "SQLGenerationOutput":
            data = self._fallback_sql_generation(text)
        elif model_name == "DataModelingOutput":
            data = self._fallback_data_modeling(text)
        elif model_name == "SQLOptimizationOutput":
            data = self._fallback_sql_optimization(text)
        elif model_name == "SchemaReviewOutput":
            data = self._fallback_schema_review(text)
        elif model_name == "LineageAnalysisOutput":
            data = self._fallback_lineage(text)

        return data

    @staticmethod
    def _fallback_sql_generation(text: str) -> dict[str, Any]:
        """Heuristic extraction for SQLGenerationOutput fields."""
        data: dict[str, Any] = {}

        # Extract SQL from code blocks
        sql_match = re.search(r"```sql\s*\n(.*?)```", text, re.DOTALL)
        if sql_match:
            data["sql"] = sql_match.group(1).strip()
        else:
            fallback_match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
            if fallback_match:
                data["sql"] = fallback_match.group(1).strip()
            else:
                data["sql"] = text.strip()

        # Extract confidence
        conf_match = re.search(
            r"confidence[:\s]+(0?\.\d+|1\.0?|\d+%)",
            text,
            re.IGNORECASE,
        )
        if conf_match:
            conf_str = conf_match.group(1).rstrip("%")
            try:
                val = float(conf_str)
                if val > 1.0:
                    val = val / 100.0
                data["confidence"] = val
            except ValueError:
                data["confidence"] = 0.8
        else:
            data["confidence"] = 0.8

        # Extract explanation (text outside code blocks)
        parts = re.split(r"```(?:sql)?\s*\n.*?```", text, flags=re.DOTALL)
        explanation_lines = [p.strip() for p in parts if p.strip()]
        data["explanation"] = "\n".join(explanation_lines[:3]) if explanation_lines else ""

        # Extract assumptions
        assumptions: list[str] = []
        in_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("assumption"):
                in_section = True
                continue
            if in_section:
                if stripped.startswith(("-", "*")):
                    assumptions.append(stripped.lstrip("-* ").strip())
                elif stripped == "" and assumptions:
                    in_section = False
        data["assumptions"] = assumptions

        # Extract warnings
        warnings: list[str] = []
        in_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("warning") or stripped.lower().startswith("caveat"):
                in_section = True
                continue
            if in_section:
                if stripped.startswith(("-", "*")):
                    warnings.append(stripped.lstrip("-* ").strip())
                elif stripped == "" and warnings:
                    in_section = False
        data["warnings"] = warnings

        return data

    @staticmethod
    def _fallback_data_modeling(text: str) -> dict[str, Any]:
        """Heuristic extraction for DataModelingOutput fields."""
        data: dict[str, Any] = {
            "fact_tables": [],
            "dimension_tables": [],
            "ddl_statements": [],
            "rationale": "",
            "confidence": 0.8,
        }

        # Extract DDL statements from code blocks
        ddl_blocks = re.findall(r"```sql\s*\n(.*?)```", text, re.DOTALL)
        if ddl_blocks:
            data["ddl_statements"] = [block.strip() for block in ddl_blocks]

        # Extract fact table names
        fact_matches = re.findall(
            r"(?:fact[_\s]*(?:table)?|##\s+Fact)\s*[:\-]?\s*(\w+)",
            text,
            re.IGNORECASE,
        )
        for name in fact_matches:
            data["fact_tables"].append({"name": name, "grain": "", "measures": [], "foreign_keys": []})

        # Extract dimension table names
        dim_matches = re.findall(
            r"(?:dim[_\w]*|dimension)\s*[:\-]?\s*(\w+)",
            text,
            re.IGNORECASE,
        )
        for name in dim_matches:
            data["dimension_tables"].append({"name": name, "attributes": [], "hierarchies": []})

        return data

    @staticmethod
    def _fallback_sql_optimization(text: str) -> dict[str, Any]:
        """Heuristic extraction for SQLOptimizationOutput fields."""
        data: dict[str, Any] = {
            "original_complexity": "moderate",
            "bottlenecks": [],
            "optimized_sql": "",
            "changes": [],
            "index_recommendations": [],
            "partition_recommendations": [],
        }

        # Extract complexity
        for level in ("simple", "moderate", "complex"):
            if level in text.lower():
                data["original_complexity"] = level
                break

        # Extract optimized SQL (last SQL code block)
        sql_blocks = re.findall(r"```sql\s*\n(.*?)```", text, re.DOTALL)
        if sql_blocks:
            data["optimized_sql"] = sql_blocks[-1].strip()

        return data

    @staticmethod
    def _fallback_schema_review(text: str) -> dict[str, Any]:
        """Heuristic extraction for SchemaReviewOutput fields."""
        data: dict[str, Any] = {
            "score": 50,
            "findings": [],
            "summary": "",
        }

        # Extract score
        score_match = re.search(r"(\d{1,3})\s*/\s*100", text)
        if score_match:
            data["score"] = int(score_match.group(1))
        else:
            score_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", text)
            if score_match:
                data["score"] = int(float(score_match.group(1)) * 10)
            else:
                score_match = re.search(r"score[:\s]+(\d+)", text, re.IGNORECASE)
                if score_match:
                    data["score"] = min(100, int(score_match.group(1)))

        # Extract summary from last paragraph
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if paragraphs:
            data["summary"] = paragraphs[-1][:500]

        return data

    @staticmethod
    def _fallback_lineage(text: str) -> dict[str, Any]:
        """Heuristic extraction for LineageAnalysisOutput fields."""
        data: dict[str, Any] = {
            "source_tables": [],
            "target_tables": [],
            "column_mappings": [],
            "transformation_descriptions": [],
        }

        # Try to extract table names from FROM / INTO patterns
        source_matches = re.findall(
            r"(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)",
            text,
            re.IGNORECASE,
        )
        data["source_tables"] = list(set(source_matches))

        target_matches = re.findall(
            r"(?:INTO|INSERT\s+INTO|CREATE\s+TABLE)\s+([a-zA-Z_][\w.]*)",
            text,
            re.IGNORECASE,
        )
        data["target_tables"] = list(set(target_matches))

        return data


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

# Mapping from output_type string to (Pydantic model, output_type key)
_OUTPUT_TYPE_REGISTRY: dict[str, type[BaseModel]] = {
    "sql_generation": SQLGenerationOutput,
    "data_modeling": DataModelingOutput,
    "sql_optimization": SQLOptimizationOutput,
    "schema_review": SchemaReviewOutput,
    "lineage_analysis": LineageAnalysisOutput,
}


def create_output_parser(
    output_type: str,
) -> StructuredOutputParser:
    """Create a ``StructuredOutputParser`` for the given output type.

    This is the primary factory function for obtaining a parser.  It returns
    a parser configured with the correct Pydantic model and format
    instructions for the specified task type.

    When using LangChain's ``with_structured_output()`` pattern, you can
    also access the underlying Pydantic model via ``parser.output_model``
    and pass it directly::

        parser = create_output_parser("sql_generation")
        structured_llm = llm.with_structured_output(parser.output_model)

    Args:
        output_type: The task type identifier.  Must be one of:
            ``sql_generation``, ``data_modeling``, ``sql_optimization``,
            ``schema_review``, ``lineage_analysis``.

    Returns:
        A ``StructuredOutputParser`` instance for the specified output type.

    Raises:
        ValueError: If the ``output_type`` is not recognised.

    Usage::

        parser = create_output_parser("sql_generation")
        instructions = parser.get_format_instructions()
        # ... inject instructions into prompt ...
        result = parser.parse(llm_response_text)
        print(result.sql, result.confidence)
    """
    model_cls = _OUTPUT_TYPE_REGISTRY.get(output_type)
    if model_cls is None:
        available = ", ".join(sorted(_OUTPUT_TYPE_REGISTRY.keys()))
        raise ValueError(
            f"Unknown output type: '{output_type}'.  "
            f"Available types: {available}"
        )

    return StructuredOutputParser(
        output_model=model_cls,  # type: ignore[arg-type]
        output_type=output_type,
    )


def register_output_type(
    name: str,
    model_cls: type[BaseModel],
) -> None:
    """Register a custom output type for use with ``create_output_parser``.

    Args:
        name: The output type identifier.
        model_cls: A Pydantic ``BaseModel`` subclass defining the output
            schema.
    """
    _OUTPUT_TYPE_REGISTRY[name] = model_cls
    logger.info("Registered custom output type: %s -> %s", name, model_cls.__name__)
