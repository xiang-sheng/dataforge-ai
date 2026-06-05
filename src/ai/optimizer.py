# -*- coding: utf-8 -*-
"""
DataForge AI - SQL query optimizer.

Provides AI-powered query analysis, optimization suggestions, query rewriting,
and complexity estimation for data warehouse workloads.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.ai.provider import BaseAIProvider, ChatMessage
from src.ai.prompts import default_registry as prompt_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class QueryComplexity(str, Enum):
    """Classification of SQL query complexity."""

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    VERY_COMPLEX = "very_complex"


class OptimizationGoal(str, Enum):
    """Common optimization objectives."""

    LATENCY = "latency"
    THROUGHPUT = "throughput"
    MEMORY = "memory"
    COST = "cost"
    READABILITY = "readability"


@dataclass
class QueryAnalysis:
    """Detailed analysis of a SQL query.

    Attributes:
        complexity: The assessed complexity level.
        estimated_cost: Relative cost estimate (1-100 scale).
        operations: List of identified operations (scan, join, aggregate, etc.).
        bottleneck_operations: Operations likely to cause performance issues.
        table_references: Tables referenced in the query.
        join_count: Number of JOIN operations.
        subquery_count: Number of sub-queries.
        has_window_functions: Whether the query uses window functions.
        has_cte: Whether the query uses Common Table Expressions.
        warnings: Potential issues detected.
    """

    complexity: QueryComplexity = QueryComplexity.SIMPLE
    estimated_cost: int = 0
    operations: List[str] = field(default_factory=list)
    bottleneck_operations: List[str] = field(default_factory=list)
    table_references: List[str] = field(default_factory=list)
    join_count: int = 0
    subquery_count: int = 0
    has_window_functions: bool = False
    has_cte: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class OptimizationSuggestion:
    """A single optimization suggestion.

    Attributes:
        category: The category of optimization (index, rewrite, partition, etc.).
        title: Short title for the suggestion.
        description: Detailed explanation.
        impact: Estimated impact level (low / medium / high).
        effort: Implementation effort (low / medium / high).
        rewritten_sql: Optional rewritten SQL implementing the suggestion.
    """

    category: str = ""
    title: str = ""
    description: str = ""
    impact: str = "medium"
    effort: str = "medium"
    rewritten_sql: str = ""


@dataclass
class OptimizationResult:
    """Complete optimization analysis result.

    Attributes:
        original_sql: The input SQL query.
        analysis: The query analysis.
        suggestions: Ordered list of suggestions (highest impact first).
        optimized_sql: The best optimized version of the query.
        estimated_improvement: Estimated performance improvement percentage.
        raw_response: Full AI response for debugging.
    """

    original_sql: str = ""
    analysis: QueryAnalysis = field(default_factory=QueryAnalysis)
    suggestions: List[OptimizationSuggestion] = field(default_factory=list)
    optimized_sql: str = ""
    estimated_improvement: str = ""
    raw_response: str = ""


@dataclass
class TableStatistics:
    """Statistics about a table, used to inform optimization decisions.

    Attributes:
        table_name: Fully qualified table name.
        row_count: Approximate number of rows.
        size_bytes: Approximate size on disk in bytes.
        column_stats: Per-column statistics (min, max, distinct count, null ratio).
        partition_info: Partitioning metadata if applicable.
    """

    table_name: str = ""
    row_count: int = 0
    size_bytes: int = 0
    column_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    partition_info: Optional[str] = None

    def to_summary_string(self) -> str:
        """Serialize to a concise summary string for prompt injection.

        Returns:
            A multi-line summary of the table statistics.
        """
        lines = [
            f"Table: {self.table_name}",
            f"  Rows: {self.row_count:,}",
            f"  Size: {self.size_bytes / (1024**3):.2f} GB",
        ]
        if self.partition_info:
            lines.append(f"  Partitioning: {self.partition_info}")
        for col, stats in self.column_stats.items():
            distinct = stats.get("distinct_count", "?")
            null_pct = stats.get("null_ratio", 0) * 100
            lines.append(
                f"  Column '{col}': distinct={distinct}, null%={null_pct:.1f}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Static SQL analysis helpers
# ---------------------------------------------------------------------------

def _static_analyze_sql(sql: str) -> QueryAnalysis:
    """Perform a lightweight static analysis of a SQL query.

    This does not require an AI call and provides a fast first-pass analysis
    based on regex pattern matching.

    Args:
        sql: The SQL query to analyze.

    Returns:
        A ``QueryAnalysis`` with heuristically determined fields.
    """
    sql_upper = sql.upper()
    analysis = QueryAnalysis()

    # Count JOINs
    analysis.join_count = len(re.findall(r"\bJOIN\b", sql_upper))

    # Count sub-queries (rough heuristic)
    analysis.subquery_count = sql_upper.count("SELECT") - 1

    # Detect CTEs
    analysis.has_cte = bool(re.search(r"\bWITH\b\s+\w+\s+AS\s*\(", sql_upper))

    # Detect window functions
    analysis.has_window_functions = bool(re.search(r"\bOVER\s*\(", sql_upper))

    # Extract table references (FROM / JOIN targets)
    table_pattern = re.compile(
        r"(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)",
        re.IGNORECASE,
    )
    analysis.table_references = list(
        set(m.group(1) for m in table_pattern.finditer(sql))
    )

    # Identify operations
    if "GROUP BY" in sql_upper:
        analysis.operations.append("aggregation")
    if "ORDER BY" in sql_upper:
        analysis.operations.append("sorting")
    if "DISTINCT" in sql_upper:
        analysis.operations.append("deduplication")
    if analysis.join_count > 0:
        analysis.operations.append("join")
    if analysis.subquery_count > 0:
        analysis.operations.append("subquery")
    if analysis.has_window_functions:
        analysis.operations.append("window_function")
    if analysis.has_cte:
        analysis.operations.append("cte")
    if "UNION" in sql_upper:
        analysis.operations.append("union")
    if re.search(r"\bLIKE\b", sql_upper):
        analysis.operations.append("pattern_match")
    if "EXISTS" in sql_upper:
        analysis.operations.append("existence_check")
    if "CASE" in sql_upper:
        analysis.operations.append("conditional_logic")

    # Determine complexity
    score = 0
    score += analysis.join_count * 2
    score += analysis.subquery_count * 3
    score += 2 if analysis.has_window_functions else 0
    score += 1 if analysis.has_cte else 0
    score += len(analysis.operations)

    if score <= 5:
        analysis.complexity = QueryComplexity.SIMPLE
        analysis.estimated_cost = min(score * 5, 20)
    elif score <= 15:
        analysis.complexity = QueryComplexity.MODERATE
        analysis.estimated_cost = min(score * 4, 50)
    elif score <= 30:
        analysis.complexity = QueryComplexity.COMPLEX
        analysis.estimated_cost = min(score * 3, 80)
    else:
        analysis.complexity = QueryComplexity.VERY_COMPLEX
        analysis.estimated_cost = min(score * 2, 100)

    # Detect potential issues
    if analysis.join_count >= 5:
        analysis.warnings.append(
            f"High join count ({analysis.join_count}) may cause performance issues. "
            "Consider denormalization or pre-aggregation."
        )
    if "SELECT *" in sql_upper:
        analysis.warnings.append(
            "SELECT * detected.  Select only the required columns to reduce I/O."
        )
    if analysis.subquery_count >= 3:
        analysis.warnings.append(
            "Multiple nested sub-queries detected.  Consider rewriting with CTEs "
            "or temporary tables for readability and potential performance gains."
        )
    if re.search(r"\bNOT\s+IN\s*\(SELECT", sql_upper):
        analysis.warnings.append(
            "NOT IN (SELECT ...) pattern detected.  Consider using NOT EXISTS or "
            "LEFT JOIN ... IS NULL for better NULL handling and performance."
        )
    if re.search(r"\bOR\b", sql_upper) and "WHERE" in sql_upper:
        analysis.warnings.append(
            "OR conditions in WHERE clause may prevent index usage.  "
            "Consider UNION ALL or restructuring predicates."
        )

    # Identify bottleneck operations
    if analysis.join_count >= 3:
        analysis.bottleneck_operations.append("multi-table joins")
    if "aggregation" in analysis.operations and analysis.join_count > 0:
        analysis.bottleneck_operations.append("join + aggregation combination")
    if analysis.subquery_count >= 2:
        analysis.bottleneck_operations.append("nested sub-queries")
    if "sorting" in analysis.operations and "aggregation" in analysis.operations:
        analysis.bottleneck_operations.append("sort after aggregation")

    return analysis


# ---------------------------------------------------------------------------
# SQLOptimizer
# ---------------------------------------------------------------------------

class SQLOptimizer:
    """AI-powered SQL query optimizer and analyzer.

    Combines fast static analysis with deep AI-powered optimization to provide
    actionable suggestions for improving query performance.

    Args:
        provider: An initialized ``BaseAIProvider`` instance.

    Usage::

        optimizer = SQLOptimizer(provider)
        result = await optimizer.suggest_optimizations(
            sql="SELECT * FROM orders JOIN ...",
            table_stats=[orders_stats, customers_stats],
        )
        for suggestion in result.suggestions:
            print(suggestion.title, "-", suggestion.impact)
    """

    def __init__(self, provider: BaseAIProvider) -> None:
        self._provider = provider

    # -- Public API ---------------------------------------------------------

    async def analyze_query(
        self,
        sql: str,
        execution_plan: Optional[str] = None,
        dialect: str = "PostgreSQL",
    ) -> QueryAnalysis:
        """Analyze a SQL query combining static analysis with AI insights.

        Performs a fast static analysis first, then optionally enriches it
        with AI-powered insights when an execution plan is provided.

        Args:
            sql: The SQL query to analyze.
            execution_plan: Optional output of ``EXPLAIN ANALYZE`` or similar.
            dialect: The SQL dialect of the query.

        Returns:
            A ``QueryAnalysis`` with both static and AI-enhanced insights.
        """
        # Fast static analysis (no AI call needed)
        analysis = _static_analyze_sql(sql)

        # If an execution plan is available, enrich with AI analysis
        if execution_plan:
            prompt = (
                f"Analyze this {dialect} query and its execution plan.  "
                f"Identify bottlenecks and estimate relative cost.\n\n"
                f"## Query\n```sql\n{sql}\n```\n\n"
                f"## Execution Plan\n```\n{execution_plan}\n```\n\n"
                f"Provide:\n"
                f"1. Which operations consume the most time/resources.\n"
                f"2. Whether indexes are being used effectively.\n"
                f"3. Estimated cardinality at each step.\n"
                f"4. Any red flags in the plan.\n"
            )

            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        "You are a database query execution plan analyst.  "
                        "Provide precise, actionable analysis."
                    ),
                ),
                ChatMessage(role="user", content=prompt),
            ]

            response = await self._provider.chat(messages)

            # Enrich static analysis with AI insights
            ai_warnings = self._extract_warnings(response.content)
            analysis.warnings.extend(ai_warnings)

        return analysis

    async def suggest_optimizations(
        self,
        sql: str,
        table_stats: Optional[List[TableStatistics]] = None,
        dialect: str = "PostgreSQL",
        goals: Optional[List[OptimizationGoal]] = None,
    ) -> OptimizationResult:
        """Analyze a query and provide optimization suggestions.

        Args:
            sql: The SQL query to optimize.
            table_stats: Optional list of table statistics for context-aware
                recommendations.
            dialect: The SQL dialect.
            goals: Optimization goals to prioritize (latency, throughput, etc.).

        Returns:
            An ``OptimizationResult`` with analysis, suggestions, and an
            optimized version of the query.
        """
        # Perform static analysis first
        analysis = _static_analyze_sql(sql)

        # Build stats summary for the prompt
        stats_summary = "N/A"
        if table_stats:
            stats_summary = "\n\n".join(s.to_summary_string() for s in table_stats)

        # Build goals string
        goal_list = goals or [OptimizationGoal.LATENCY, OptimizationGoal.READABILITY]
        goals_str = ", ".join(g.value for g in goal_list)

        template = prompt_registry.get("sql_optimization")
        rendered = template.render(
            dialect=dialect,
            sql=sql,
            execution_plan="N/A",
            table_stats=stats_summary,
            optimization_goals=goals_str,
        )

        messages = [
            ChatMessage(role="system", content=rendered["system"]),
            ChatMessage(role="user", content=rendered["user"]),
        ]

        response = await self._provider.chat(messages)

        # Parse suggestions from the response
        suggestions = self._parse_suggestions(response.content)

        # Extract the optimized SQL
        optimized_sql = self._extract_optimized_sql(response.content, sql)

        return OptimizationResult(
            original_sql=sql,
            analysis=analysis,
            suggestions=suggestions,
            optimized_sql=optimized_sql,
            estimated_improvement=self._extract_improvement_estimate(response.content),
            raw_response=response.content,
        )

    async def rewrite_query(
        self,
        sql: str,
        optimization_goals: Optional[List[OptimizationGoal]] = None,
        dialect: str = "PostgreSQL",
        constraints: str = "",
    ) -> str:
        """Rewrite a SQL query applying optimization techniques.

        This method focuses specifically on producing an optimized rewrite
        rather than providing a full analysis report.

        Args:
            sql: The original SQL query.
            optimization_goals: Goals to prioritize during rewriting.
            dialect: The target SQL dialect.
            constraints: Additional constraints (e.g. "Must preserve the exact
                output schema", "Cannot add new indexes").

        Returns:
            The optimized SQL query string.
        """
        goals = optimization_goals or [OptimizationGoal.LATENCY]
        goals_str = ", ".join(g.value for g in goals)

        prompt = (
            f"Rewrite the following {dialect} SQL query for better performance.\n\n"
            f"## Original Query\n```sql\n{sql}\n```\n\n"
            f"## Optimization Goals\n{goals_str}\n\n"
        )
        if constraints:
            prompt += f"## Constraints\n{constraints}\n\n"

        prompt += (
            "## Requirements\n"
            "- Preserve the exact same output (same columns, same semantics).\n"
            "- Apply techniques such as: CTE refactoring, join reordering, "
            "predicate pushdown, subquery flattening, window function optimization.\n"
            "- Add inline comments explaining each optimization applied.\n"
            "- Wrap the final query in a ```sql code block.\n"
        )

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a SQL performance engineer.  Rewrite queries for "
                    "maximum performance while preserving correctness."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages)
        return self._extract_optimized_sql(response.content, sql)

    async def estimate_complexity(
        self,
        sql: str,
    ) -> QueryAnalysis:
        """Estimate the complexity of a SQL query using static analysis only.

        This is a fast, local operation that does not make an AI call.

        Args:
            sql: The SQL query to analyze.

        Returns:
            A ``QueryAnalysis`` with complexity, cost estimate, and warnings.
        """
        return _static_analyze_sql(sql)

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    def _extract_warnings(text: str) -> List[str]:
        """Extract warning / issue lines from the AI analysis response."""
        warnings: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if any(
                kw in stripped.lower()
                for kw in ("warning", "issue", "problem", "concern", "risk", "red flag")
            ):
                clean = re.sub(r"^[#\-*\d.]+\s*", "", stripped)
                if clean:
                    warnings.append(clean)
        return warnings

    @staticmethod
    def _parse_suggestions(text: str) -> List[OptimizationSuggestion]:
        """Parse optimization suggestions from the AI response.

        Looks for numbered or headed sections that describe individual
        optimization suggestions.

        Args:
            text: The raw AI response text.

        Returns:
            A list of ``OptimizationSuggestion`` objects.
        """
        suggestions: List[OptimizationSuggestion] = []

        # Split on numbered headers or bold headers
        sections = re.split(
            r"\n(?=\d+\.\s+\*\*|##\s+(?:Suggestion|Recommendation|Optimization))",
            text,
        )

        for section in sections:
            stripped = section.strip()
            if not stripped:
                continue

            # Extract title from the first line
            title_match = re.match(
                r"(?:\d+\.\s+\*\*|##\s+)(.+?)(?:\*\*|\n|$)",
                stripped,
            )
            title = title_match.group(1).strip() if title_match else ""

            if not title:
                continue

            # Determine category
            category = "general"
            title_lower = title.lower()
            if "index" in title_lower:
                category = "index"
            elif "rewrite" in title_lower or "restructur" in title_lower:
                category = "rewrite"
            elif "partition" in title_lower:
                category = "partition"
            elif "join" in title_lower:
                category = "join"
            elif "filter" in title_lower or "predicate" in title_lower:
                category = "predicate"
            elif "materializ" in title_lower or "cache" in title_lower:
                category = "materialization"

            # Determine impact
            impact = "medium"
            if "high" in stripped.lower() or "significant" in stripped.lower():
                impact = "high"
            elif "low" in stripped.lower() or "minor" in stripped.lower():
                impact = "low"

            # Extract SQL if present
            sql_match = re.search(r"```sql\s*\n(.*?)```", stripped, re.DOTALL)
            rewritten = sql_match.group(1).strip() if sql_match else ""

            # Clean description
            desc = re.sub(r"^(?:\d+\.\s+\*\*.*?\*\*|##\s+.*?)\n?", "", stripped).strip()

            suggestions.append(
                OptimizationSuggestion(
                    category=category,
                    title=title,
                    description=desc[:500],  # Truncate very long descriptions
                    impact=impact,
                    effort="medium",
                    rewritten_sql=rewritten,
                )
            )

        return suggestions

    @staticmethod
    def _extract_optimized_sql(text: str, fallback: str) -> str:
        """Extract the optimized SQL from the AI response.

        Args:
            text: The full AI response.
            fallback: The original SQL to return if no optimized version is found.

        Returns:
            The extracted optimized SQL, or the fallback.
        """
        # Look for the last SQL code block (typically the final optimized version)
        matches = re.findall(r"```sql\s*\n(.*?)```", text, re.DOTALL)
        if matches:
            return matches[-1].strip()
        return fallback

    @staticmethod
    def _extract_improvement_estimate(text: str) -> str:
        """Extract an estimated improvement percentage from the response."""
        match = re.search(
            r"(\d+)\s*[-–]\s*(\d+)\s*%\s*(?:improvement|faster|reduction|gain)",
            text,
            re.IGNORECASE,
        )
        if match:
            return f"{match.group(1)}-{match.group(2)}%"

        match = re.search(
            r"(?:improve|faster|reduction|gain)[d]?\s*(?:by\s*)?(?:up\s*to\s*)?(\d+)\s*%",
            text,
            re.IGNORECASE,
        )
        if match:
            return f"~{match.group(1)}%"

        return "Not estimated"
