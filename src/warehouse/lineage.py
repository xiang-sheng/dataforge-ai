"""
DataForge AI - Data lineage tracking.

Provides SQL-based lineage parsing at both table and column level, upstream /
downstream tracing, cycle detection, and lineage visualization.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ColumnLineage:
    """A column-level lineage edge.

    Attributes:
        source_table: Name of the source table.
        source_column: Name of the source column.
        target_table: Name of the target table.
        target_column: Name of the target column.
        transformation: Description of the transformation applied
            (e.g. "direct", "SUM()", "COALESCE()", "derived").
    """

    source_table: str
    source_column: str
    target_table: str
    target_column: str
    transformation: str = "direct"


@dataclass
class TableLineage:
    """A table-level lineage edge.

    Attributes:
        source_table: The upstream table.
        target_table: The downstream table.
        relationship: The type of relationship (INSERT INTO, CTAS, MERGE, etc.).
    """

    source_table: str
    target_table: str
    relationship: str = "INSERT INTO"


@dataclass
class LineageGraph:
    """A complete lineage graph containing both table and column-level edges.

    Attributes:
        table_edges: Set of table-level lineage edges.
        column_edges: List of column-level lineage edges.
        tables: Set of all tables referenced in the graph.
    """

    table_edges: set[TableLineage] = field(default_factory=set)
    column_edges: list[ColumnLineage] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)

    def add_table_edge(self, source: str, target: str, relationship: str = "INSERT INTO") -> None:
        """Add a table-level lineage edge.

        Args:
            source: Source table name.
            target: Target table name.
            relationship: Type of data flow relationship.
        """
        self.table_edges.add(TableLineage(source, target, relationship))
        self.tables.add(source)
        self.tables.add(target)

    def add_column_edge(
        self,
        source_table: str,
        source_column: str,
        target_table: str,
        target_column: str,
        transformation: str = "direct",
    ) -> None:
        """Add a column-level lineage edge.

        Args:
            source_table: Source table name.
            source_column: Source column name.
            target_table: Target table name.
            target_column: Target column name.
            transformation: Description of the transformation.
        """
        self.column_edges.append(
            ColumnLineage(source_table, source_column, target_table, target_column, transformation)
        )
        self.tables.add(source_table)
        self.tables.add(target_table)

    def get_upstream_tables(self, table: str) -> set[str]:
        """Return all direct upstream tables for the given table.

        Args:
            table: The table to trace upstream from.

        Returns:
            A set of upstream table names.
        """
        return {edge.source_table for edge in self.table_edges if edge.target_table == table}

    def get_downstream_tables(self, table: str) -> set[str]:
        """Return all direct downstream tables for the given table.

        Args:
            table: The table to trace downstream from.

        Returns:
            A set of downstream table names.
        """
        return {edge.target_table for edge in self.table_edges if edge.source_table == table}


@dataclass
class LineageNode:
    """A node in a lineage trace result.

    Attributes:
        table: The table name.
        column: Optional column name.
        depth: Distance from the starting point (0 = origin).
        path: The full path from the origin to this node.
    """

    table: str
    column: str | None = None
    depth: int = 0
    path: list[str] = field(default_factory=list)


@dataclass
class CycleInfo:
    """Information about a detected circular dependency.

    Attributes:
        tables: The list of tables forming the cycle.
        description: Human-readable description of the cycle.
    """

    tables: list[str]
    description: str = ""


# ---------------------------------------------------------------------------
# SQL lineage parser
# ---------------------------------------------------------------------------

class _SQLLineageParser:
    """Internal parser that extracts lineage information from SQL statements.

    Supports common DML patterns: INSERT INTO ... SELECT, CREATE TABLE AS
    SELECT (CTAS), MERGE INTO, and plain SELECT with INTO.

    This is a regex-based heuristic parser.  For production-grade column-level
    lineage, consider integrating a full SQL parser like ``sqlglot`` or
    ``sqllineage``.
    """

    # Patterns for detecting target tables
    _INSERT_INTO_RE = re.compile(
        r"INSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?(\S+)",
        re.IGNORECASE,
    )
    _CTAS_RE = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)\s+AS\s+SELECT",
        re.IGNORECASE,
    )
    _MERGE_RE = re.compile(
        r"MERGE\s+INTO\s+(\S+)",
        re.IGNORECASE,
    )

    # Patterns for detecting source tables
    _FROM_RE = re.compile(
        r"\bFROM\s+(\S+)",
        re.IGNORECASE,
    )
    _JOIN_RE = re.compile(
        r"\bJOIN\s+(\S+)",
        re.IGNORECASE,
    )
    _SUBQUERY_FROM_RE = re.compile(
        r"\bFROM\s+(\S+)\s+(?:AS\s+)?(\w+)?",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, sql: str) -> LineageGraph:
        """Parse a SQL statement and extract lineage information.

        Args:
            sql: The SQL statement to parse.

        Returns:
            A ``LineageGraph`` with table and (best-effort) column lineage.
        """
        graph = LineageGraph()

        # Detect target table
        target_table = cls._detect_target(sql)

        # Detect source tables
        source_tables = cls._detect_sources(sql)

        if target_table:
            graph.tables.add(target_table)
            for source in source_tables:
                if source != target_table:  # Avoid self-loops in basic detection
                    graph.add_table_edge(source, target_table)

        # Best-effort column lineage
        if target_table:
            cls._parse_column_lineage(sql, target_table, source_tables, graph)

        return graph

    @classmethod
    def _detect_target(cls, sql: str) -> str | None:
        """Detect the target (output) table of a SQL statement."""
        for pattern in (cls._INSERT_INTO_RE, cls._CTAS_RE, cls._MERGE_RE):
            match = pattern.search(sql)
            if match:
                return match.group(1).strip("`\";(")
        return None

    @classmethod
    def _detect_sources(cls, sql: str) -> set[str]:
        """Detect all source tables referenced in FROM and JOIN clauses."""
        sources: set[str] = set()

        for match in cls._FROM_RE.finditer(sql):
            table = match.group(1).strip("`\";(")
            if table.upper() not in ("SELECT", "DUAL", "LATERAL"):
                sources.add(table)

        for match in cls._JOIN_RE.finditer(sql):
            table = match.group(1).strip("`\";(")
            if table.upper() not in ("SELECT",):
                sources.add(table)

        return sources

    @classmethod
    def _parse_column_lineage(
        cls,
        sql: str,
        target_table: str,
        source_tables: set[str],
        graph: LineageGraph,
    ) -> None:
        """Best-effort column-level lineage extraction.

        Parses the SELECT clause to map output columns to their source
        columns.  Handles simple cases like ``SELECT a.col1, b.col2`` and
        aliased expressions like ``SELECT SUM(a.amount) AS total_amount``.

        Args:
            sql: The SQL statement.
            target_table: The detected target table.
            source_tables: Detected source tables.
            graph: The lineage graph to add column edges to.
        """
        # Extract the SELECT clause
        select_match = re.search(
            r"\bSELECT\s+(.*?)\bFROM\b",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not select_match:
            return

        select_clause = select_match.group(1).strip()

        # Build alias -> table mapping from FROM/JOIN
        alias_map = cls._build_alias_map(sql, source_tables)

        # Parse each select expression
        expressions = cls._split_select_expressions(select_clause)

        for expr in expressions:
            expr = expr.strip()
            if not expr or expr == "*":
                continue

            # Detect alias: "expr AS alias" or trailing identifier
            alias_match = re.search(r"\bAS\s+(\w+)\s*$", expr, re.IGNORECASE)
            target_col = alias_match.group(1) if alias_match else expr.split(".")[-1].strip()

            # Clean target column name
            target_col = re.sub(r"[^a-zA-Z0-9_]", "", target_col)
            if not target_col:
                continue

            # Detect source references (table.column patterns)
            col_refs = re.findall(r"(\w+)\.(\w+)", expr)
            if col_refs:
                for alias, col in col_refs:
                    source_table = alias_map.get(alias, alias)
                    transformation = "direct"
                    # Detect if wrapped in a function
                    func_match = re.match(r"(\w+)\s*\(", expr)
                    if func_match:
                        transformation = f"{func_match.group(1).upper()}()"

                    graph.add_column_edge(
                        source_table=source_table,
                        source_column=col,
                        target_table=target_table,
                        target_column=target_col,
                        transformation=transformation,
                    )
            else:
                # No table qualifier -- try to infer from first source table
                if source_tables:
                    first_source = sorted(source_tables)[0]
                    graph.add_column_edge(
                        source_table=first_source,
                        source_column=target_col,
                        target_table=target_table,
                        target_column=target_col,
                        transformation="direct",
                    )

    @classmethod
    def _build_alias_map(cls, sql: str, source_tables: set[str]) -> dict[str, str]:
        """Build a mapping from table aliases to actual table names."""
        alias_map: dict[str, str] = {}

        # Match "FROM table alias" and "FROM table AS alias"
        patterns = [
            re.compile(r"\bFROM\s+(\S+)\s+(?:AS\s+)?(\w+)", re.IGNORECASE),
            re.compile(r"\bJOIN\s+(\S+)\s+(?:AS\s+)?(\w+)", re.IGNORECASE),
        ]

        for pattern in patterns:
            for match in pattern.finditer(sql):
                table = match.group(1).strip("`\"(")
                alias = match.group(2)
                if alias.upper() not in (
                    "ON", "WHERE", "SET", "GROUP", "ORDER", "HAVING",
                    "LIMIT", "UNION", "INNER", "LEFT", "RIGHT", "FULL",
                    "CROSS", "NATURAL", "USING", "LATERAL",
                ):
                    alias_map[alias] = table

        return alias_map

    @staticmethod
    def _split_select_expressions(select_clause: str) -> list[str]:
        """Split a SELECT clause into individual expressions.

        Handles nested parentheses to avoid splitting on commas inside
        function calls.

        Args:
            select_clause: The text between SELECT and FROM.

        Returns:
            A list of individual expression strings.
        """
        expressions: list[str] = []
        current = ""
        depth = 0

        for char in select_clause:
            if char == "(":
                depth += 1
                current += char
            elif char == ")":
                depth -= 1
                current += char
            elif char == "," and depth == 0:
                expressions.append(current.strip())
                current = ""
            else:
                current += char

        if current.strip():
            expressions.append(current.strip())

        return expressions


# ---------------------------------------------------------------------------
# LineageTracker
# ---------------------------------------------------------------------------

class LineageTracker:
    """Data lineage tracking service for the data warehouse.

    Parses SQL statements to build lineage graphs, traces upstream and
    downstream dependencies, detects circular dependencies, and provides
    visualization-friendly output.

    Usage::

        tracker = LineageTracker()

        # Parse SQL to build lineage
        graph = tracker.parse_sql_lineage(
            "INSERT INTO dwd_order SELECT * FROM ods_order"
        )

        # Trace dependencies
        upstream = tracker.trace_upstream("ads_report", graph=graph)
        for node in upstream:
            print(f"  {'  ' * node.depth}{node.table}")

        # Detect cycles
        cycles = tracker.detect_circular_dependencies(graph)
    """

    def __init__(self) -> None:
        self._global_graph = LineageGraph()

    # -- Public API ---------------------------------------------------------

    def parse_sql_lineage(self, sql: str) -> LineageGraph:
        """Parse a SQL statement and extract lineage information.

        The parsed lineage is also merged into the tracker's global graph
        for cumulative lineage building across multiple SQL statements.

        Args:
            sql: The SQL statement to parse.  Supports INSERT INTO ... SELECT,
                CREATE TABLE AS SELECT, and MERGE INTO statements.

        Returns:
            A ``LineageGraph`` representing the lineage of this statement.
        """
        statement_graph = _SQLLineageParser.parse(sql)

        # Merge into global graph
        for edge in statement_graph.table_edges:
            self._global_graph.add_table_edge(
                edge.source_table, edge.target_table, edge.relationship
            )
        for col_edge in statement_graph.column_edges:
            self._global_graph.add_column_edge(
                col_edge.source_table,
                col_edge.source_column,
                col_edge.target_table,
                col_edge.target_column,
                col_edge.transformation,
            )

        return statement_graph

    def parse_multiple_sql(self, sql_statements: list[str]) -> LineageGraph:
        """Parse multiple SQL statements and build a cumulative lineage graph.

        Args:
            sql_statements: List of SQL statements to parse.

        Returns:
            The cumulative ``LineageGraph`` after parsing all statements.
        """
        for sql in sql_statements:
            self.parse_sql_lineage(sql)
        return self._global_graph

    def trace_upstream(
        self,
        table: str,
        column: str | None = None,
        graph: LineageGraph | None = None,
        max_depth: int = 20,
    ) -> list[LineageNode]:
        """Trace all upstream dependencies for a table or column.

        Performs a breadth-first traversal of the lineage graph to find all
        tables (and optionally columns) that the given table depends on.

        Args:
            table: The starting table name.
            column: Optional column name for column-level tracing.
            graph: The lineage graph to traverse.  Defaults to the global graph.
            max_depth: Maximum traversal depth to prevent infinite loops.

        Returns:
            A list of ``LineageNode`` objects representing the upstream chain.
        """
        g = graph or self._global_graph
        return self._bfs_trace(table, column, g, direction="upstream", max_depth=max_depth)

    def trace_downstream(
        self,
        table: str,
        column: str | None = None,
        graph: LineageGraph | None = None,
        max_depth: int = 20,
    ) -> list[LineageNode]:
        """Trace all downstream dependencies for a table or column.

        Args:
            table: The starting table name.
            column: Optional column name for column-level tracing.
            graph: The lineage graph to traverse.  Defaults to the global graph.
            max_depth: Maximum traversal depth.

        Returns:
            A list of ``LineageNode`` objects representing the downstream chain.
        """
        g = graph or self._global_graph
        return self._bfs_trace(table, column, g, direction="downstream", max_depth=max_depth)

    def visualize_lineage(
        self,
        graph: LineageGraph | None = None,
        format: str = "text",
    ) -> str:
        """Generate a formatted visualization of the lineage graph.

        Args:
            graph: The lineage graph to visualize.  Defaults to the global graph.
            format: Output format.  Options:
                - ``"text"``: Plain-text tree representation.
                - ``"mermaid"``: Mermaid diagram syntax for rendering in Markdown.
                - ``"dot"``: Graphviz DOT format.

        Returns:
            A formatted string representing the lineage.
        """
        g = graph or self._global_graph

        if format == "mermaid":
            return self._render_mermaid(g)
        elif format == "dot":
            return self._render_dot(g)
        else:
            return self._render_text(g)

    def detect_circular_dependencies(
        self,
        graph: LineageGraph | None = None,
    ) -> list[CycleInfo]:
        """Detect circular dependencies in the lineage graph.

        Uses depth-first search to find cycles in the table-level lineage.

        Args:
            graph: The lineage graph to check.  Defaults to the global graph.

        Returns:
            A list of ``CycleInfo`` objects describing each detected cycle.
            An empty list means no cycles were found.
        """
        g = graph or self._global_graph

        # Build adjacency list
        adj: dict[str, set[str]] = defaultdict(set)
        for edge in g.table_edges:
            adj[edge.source_table].add(edge.target_table)

        cycles: list[list[str]] = []
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def _dfs(node: str, path: list[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    _dfs(neighbor, path)
                elif neighbor in rec_stack:
                    # Found a cycle: extract it
                    cycle_start = path.index(neighbor)
                    cycle = [*path[cycle_start:], neighbor]
                    cycles.append(cycle)

            path.pop()
            rec_stack.discard(node)

        for table in g.tables:
            if table not in visited:
                _dfs(table, [])

        # Convert to CycleInfo
        results: list[CycleInfo] = []
        for cycle in cycles:
            results.append(
                CycleInfo(
                    tables=cycle,
                    description=" -> ".join(cycle),
                )
            )

        return results

    def get_impact_analysis(
        self,
        table: str,
        graph: LineageGraph | None = None,
    ) -> dict[str, Any]:
        """Perform an impact analysis for changes to a given table.

        Identifies all downstream tables and columns that would be affected
        if the specified table were modified.

        Args:
            table: The table to analyze.
            graph: The lineage graph.  Defaults to the global graph.

        Returns:
            A dict with ``affected_tables``, ``affected_columns``, and
            ``impact_depth`` keys.
        """
        g = graph or self._global_graph

        downstream = self.trace_downstream(table, graph=g)
        affected_tables = list({n.table for n in downstream if n.table != table})

        # Column-level impact
        affected_columns: list[dict[str, str]] = []
        for col_edge in g.column_edges:
            if col_edge.source_table == table:
                affected_columns.append({
                    "source_column": col_edge.source_column,
                    "affected_table": col_edge.target_table,
                    "affected_column": col_edge.target_column,
                    "transformation": col_edge.transformation,
                })

        max_depth = max((n.depth for n in downstream), default=0)

        return {
            "source_table": table,
            "affected_tables": affected_tables,
            "affected_columns": affected_columns,
            "impact_depth": max_depth,
            "total_affected": len(affected_tables),
        }

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    def _bfs_trace(
        table: str,
        column: str | None,
        graph: LineageGraph,
        direction: str = "upstream",
        max_depth: int = 20,
    ) -> list[LineageNode]:
        """Breadth-first traversal for lineage tracing.

        Args:
            table: Starting table.
            column: Optional starting column.
            graph: The lineage graph.
            direction: ``"upstream"`` or ``"downstream"``.
            max_depth: Maximum depth to traverse.

        Returns:
            A list of ``LineageNode`` objects in BFS order.
        """
        results: list[LineageNode] = []
        visited: set[str] = set()
        queue: deque[tuple[str, str | None, int, list[str]]] = deque()

        queue.append((table, column, 0, [table]))
        visited.add(table)

        while queue:
            current_table, current_col, depth, path = queue.popleft()

            if depth > 0:
                results.append(
                    LineageNode(
                        table=current_table,
                        column=current_col,
                        depth=depth,
                        path=list(path),
                    )
                )

            if depth >= max_depth:
                continue

            if direction == "upstream":
                neighbors = graph.get_upstream_tables(current_table)
                # Column-level tracing
                if column and depth == 0:
                    for col_edge in graph.column_edges:
                        if (
                            col_edge.target_table == current_table
                            and col_edge.target_column == column
                        ):
                            neighbor_key = col_edge.source_table
                            if neighbor_key not in visited:
                                visited.add(neighbor_key)
                                queue.append((
                                    neighbor_key,
                                    col_edge.source_column,
                                    depth + 1,
                                    [*path, neighbor_key],
                                ))
                else:
                    for neighbor in neighbors:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append((
                                neighbor,
                                None,
                                depth + 1,
                                [*path, neighbor],
                            ))
            else:  # downstream
                neighbors = graph.get_downstream_tables(current_table)
                if column and depth == 0:
                    for col_edge in graph.column_edges:
                        if (
                            col_edge.source_table == current_table
                            and col_edge.source_column == column
                        ):
                            neighbor_key = col_edge.target_table
                            if neighbor_key not in visited:
                                visited.add(neighbor_key)
                                queue.append((
                                    neighbor_key,
                                    col_edge.target_column,
                                    depth + 1,
                                    [*path, neighbor_key],
                                ))
                else:
                    for neighbor in neighbors:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append((
                                neighbor,
                                None,
                                depth + 1,
                                [*path, neighbor],
                            ))

        return results

    @staticmethod
    def _render_text(graph: LineageGraph) -> str:
        """Render the lineage graph as a plain-text tree."""
        lines: list[str] = ["=== Data Lineage Graph ===", ""]

        # Build adjacency for display
        downstream: dict[str, list[str]] = defaultdict(list)
        for edge in graph.table_edges:
            downstream[edge.source_table].append(edge.target_table)

        # Find root tables (no upstream)
        all_targets = {e.target_table for e in graph.table_edges}
        all_sources = {e.source_table for e in graph.table_edges}
        roots = all_sources - all_targets

        if not roots:
            roots = all_sources  # Fallback if cycles exist

        def _render_tree(table: str, indent: int, visited: set[str]) -> None:
            prefix = "  " * indent + ("|-- " if indent > 0 else "")
            lines.append(f"{prefix}{table}")
            if table in visited:
                return
            visited.add(table)
            for child in sorted(downstream.get(table, [])):
                _render_tree(child, indent + 1, visited)

        for root in sorted(roots):
            _render_tree(root, 0, set())

        # Column lineage section
        if graph.column_edges:
            lines.append("")
            lines.append("=== Column-Level Lineage ===")
            for edge in graph.column_edges:
                lines.append(
                    f"  {edge.source_table}.{edge.source_column} "
                    f"--[{edge.transformation}]--> "
                    f"{edge.target_table}.{edge.target_column}"
                )

        return "\n".join(lines)

    @staticmethod
    def _render_mermaid(graph: LineageGraph) -> str:
        """Render the lineage graph as a Mermaid flowchart."""
        lines = ["graph LR"]
        for edge in graph.table_edges:
            src = edge.source_table.replace(".", "_")
            tgt = edge.target_table.replace(".", "_")
            lines.append(f"    {src}[{edge.source_table}] --> {tgt}[{edge.target_table}]")
        return "\n".join(lines)

    @staticmethod
    def _render_dot(graph: LineageGraph) -> str:
        """Render the lineage graph in Graphviz DOT format."""
        lines = [
            "digraph lineage {",
            "    rankdir=LR;",
            '    node [shape=box, style="rounded,filled", fillcolor="#E8F4FD"];',
        ]
        for table in graph.tables:
            node_id = table.replace(".", "_")
            lines.append(f'    {node_id} [label="{table}"];')
        for edge in graph.table_edges:
            src = edge.source_table.replace(".", "_")
            tgt = edge.target_table.replace(".", "_")
            lines.append(f"    {src} -> {tgt};")
        lines.append("}")
        return "\n".join(lines)
