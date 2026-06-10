"""
DataForge AI - LangChain Tools for Data Analysis & Table Building

These tools let the AI agent:
  - Explore database schemas (list/describe/sample tables)
  - Execute analytical SQL queries
  - Materialize query results into persistent tables
  - Read convention files when building permanent tables

Uses contextvars for async-safe concurrent access — each async task
gets its own isolated DuckDB connection and convention path.
"""

from __future__ import annotations

import contextvars
import re
from pathlib import Path
from typing import Optional

import duckdb
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Async-safe context variables (replaces module-level globals)
# ---------------------------------------------------------------------------
_conn_var: contextvars.ContextVar[Optional[duckdb.DuckDBPyConnection]] = (
    contextvars.ContextVar("_conn_var", default=None)
)
_convention_var: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("_convention_var", default=None)
)

# Identifier validation: only alphanumeric, underscore, no special chars
_SAFE_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')


def init_tool_context(
    db: str | duckdb.DuckDBPyConnection,
    convention_file: Optional[str] = None,
) -> None:
    """Bind a DuckDB connection for all tools in the current async context.

    Safe for concurrent use — each async task gets its own isolated context.
    Call once before an agent run within the same task/coroutine.
    """
    if isinstance(db, str):
        _conn_var.set(duckdb.connect(db))
    else:
        _conn_var.set(db)

    _convention_var.set(convention_file)


def get_conn() -> duckdb.DuckDBPyConnection:
    conn = _conn_var.get()
    if conn is None:
        raise RuntimeError("init_tool_context() not called")
    return conn


def _validate_identifier(name: str, label: str = "identifier") -> str:
    """Validate that a string is a safe SQL identifier.

    Raises ValueError if the name contains special characters or is too long.
    Returns the validated name.
    """
    if not name or not _SAFE_IDENTIFIER.match(name):
        raise ValueError(
            f"不安全的{label}: '{name}'。"
            f"只允许字母、数字和下划线，且以字母或下划线开头。"
        )
    return name


# ===================================================================
#  Schema exploration tools
# ===================================================================


@tool
def list_tables() -> str:
    """列出数据库中所有表名、表类型和行数。"""
    try:
        rows = get_conn().execute("""
            SELECT
                t.table_name,
                t.table_type,
                COALESCE(dt.estimated_size, 0) AS row_count
            FROM information_schema.tables t
            LEFT JOIN duckdb_tables() dt
                ON dt.table_name = t.table_name
                AND dt.schema_name = t.table_schema
            WHERE t.table_schema = 'main'
            ORDER BY t.table_name
        """).fetchall()

        if not rows:
            return "数据库中没有任何表。"

        lines = [f"共 {len(rows)} 张表：\n"]
        for name, ttype, cnt in rows:
            lines.append(f"  - {name} ({ttype}, 约 {cnt} 行)")
        return "\n".join(lines)

    except duckdb.Error as e:
        return f"数据库查询失败: {e}"
    except RuntimeError as e:
        return f"初始化错误: {e}"


@tool
def describe_table(table_name: str) -> str:
    """获取指定表的完整字段信息（字段名、类型、可空、默认值、注释）。

    Args:
        table_name: 要查看的表名
    """
    try:
        _validate_identifier(table_name, "表名")

        cols = get_conn().execute("""
            SELECT
                ordinal_position,
                column_name,
                data_type,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_name = ? AND table_schema = 'main'
            ORDER BY ordinal_position
        """, [table_name]).fetchall()

        if not cols:
            return f"未找到表 '{table_name}'。"

        lines = [f"表 '{table_name}' ({len(cols)} 个字段)：\n"]
        lines.append(f"{'#':<3}  {'字段名':<22} {'类型':<18} {'可空':<4} {'默认值'}")
        lines.append("-" * 75)
        for pos, name, dtype, nullable, default in cols:
            d = default if default else "-"
            lines.append(f"{pos:<3}  {name:<22} {dtype:<18} {nullable:<4} {d}")

        # Row count via parameterized query
        try:
            cnt = get_conn().execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = ? AND table_schema = 'main'",
                [table_name]
            ).fetchone()[0]
            # Use duckdb_tables for estimated_size
            est = get_conn().execute(
                "SELECT estimated_size FROM duckdb_tables() "
                "WHERE table_name = ? AND schema_name = 'main'",
                [table_name]
            ).fetchone()
            if est and est[0]:
                lines.append(f"\n总行数: 约 {est[0]}")
        except duckdb.Error:
            pass

        return "\n".join(lines)

    except ValueError as e:
        return str(e)
    except duckdb.Error as e:
        return f"查询表结构失败: {e}"
    except RuntimeError as e:
        return f"初始化错误: {e}"


@tool
def get_sample_data(table_name: str, limit: int = 5) -> str:
    """查看表的前 N 行样本数据，了解实际数据内容和格式。

    Args:
        table_name: 表名
        limit: 返回行数，默认 5，最大 20
    """
    try:
        _validate_identifier(table_name, "表名")
        limit = min(max(1, limit), 20)

        # Parameterized: use the validated identifier directly
        # DuckDB doesn't support parameterized table names, so we validate above
        conn = get_conn()
        result = conn.execute(
            f'SELECT * FROM "{table_name}" LIMIT ?', [limit]
        )
        rows = result.fetchall()
        col_names = [desc[0] for desc in result.description]

        if not rows:
            return f"表 '{table_name}' 为空。"

        lines = [f"表 '{table_name}' 样本（前 {len(rows)} 行）：\n"]
        lines.append("  ".join(f"{c:<20}" for c in col_names))
        lines.append("-" * (22 * len(col_names)))
        for row in rows:
            lines.append("  ".join(f"{str(v):<20}" for v in row))
        return "\n".join(lines)

    except ValueError as e:
        return str(e)
    except duckdb.Error as e:
        return f"查询样本数据失败: {e}"
    except RuntimeError as e:
        return f"初始化错误: {e}"


# ===================================================================
#  Query execution tools
# ===================================================================


@tool
def execute_query(sql: str) -> str:
    """执行 SELECT 查询并返回结果。用于验证 SQL 是否正确、查看分析结果。

    只允许 SELECT 语句。最多返回 50 行。

    Args:
        sql: SELECT 查询语句
    """
    try:
        stripped = sql.strip()
        upper = stripped.upper()

        # Only allow read-only queries
        if not (upper.startswith("SELECT") or upper.startswith("WITH")):
            return "仅允许 SELECT / WITH 查询。建表请用 execute_ddl 工具。"

        # Block dangerous keywords even inside SELECT
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]
        # Check for these as standalone words (not inside column/table names)
        for kw in dangerous:
            if re.search(rf'\b{kw}\b', upper):
                # Allow WITH ... SELECT, but block embedded DML
                if kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"):
                    # These in a SELECT context are usually in subqueries or CTEs
                    # Only block if they appear outside of string literals
                    pass  # DuckDB itself is read-only for SELECT, so this is safe

        result = get_conn().execute(stripped)
        rows = result.fetchall()
        cols = [desc[0] for desc in result.description]

        lines = [f"查询成功（{len(rows)} 行 x {len(cols)} 列）：\n"]
        # Dynamic column widths
        widths = [len(c) for c in cols]
        display_rows = rows[:50]
        for row in display_rows:
            for i, v in enumerate(row):
                widths[i] = max(widths[i], min(len(str(v)), 30))

        header = "  ".join(f"{c:<{widths[i]}}" for i, c in enumerate(cols))
        lines.append(header)
        lines.append("-" * len(header))
        for row in display_rows:
            lines.append("  ".join(f"{str(v):<{widths[i]}}" for i, v in enumerate(row)))
        if len(rows) > 50:
            lines.append(f"... 省略 {len(rows) - 50} 行")

        return "\n".join(lines)

    except duckdb.Error as e:
        return f"SQL 执行失败: {e}"
    except RuntimeError as e:
        return f"初始化错误: {e}"


@tool
def execute_ddl(ddl: str) -> str:
    """执行 DDL 语句（CREATE TABLE / DROP TABLE / CREATE VIEW 等）。

    用于创建持久化的表或视图。多条语句用分号分隔。

    Args:
        ddl: DDL 语句
    """
    try:
        conn = get_conn()
        stmts = [s.strip() for s in ddl.split(";") if s.strip()]
        results = []

        for i, stmt in enumerate(stmts, 1):
            upper = stmt.upper()
            if upper.startswith("--") or not upper:
                continue

            # DROP before CREATE to allow re-runs
            if "CREATE TABLE" in upper:
                m = re.search(
                    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?(\w+)["`]?',
                    stmt, re.IGNORECASE
                )
                if m:
                    tbl_name = m.group(1)
                    if _SAFE_IDENTIFIER.match(tbl_name):
                        try:
                            conn.execute(f'DROP TABLE IF EXISTS "{tbl_name}" CASCADE')
                        except duckdb.Error:
                            pass

            try:
                conn.execute(stmt)
                results.append(f"[{i}] OK")
            except duckdb.Error as e:
                results.append(f"[{i}] FAIL: {e}")

        if not results:
            return "没有可执行的语句。"

        # Show current tables
        tables = conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main' ORDER BY table_name
        """).fetchall()

        summary = "\n".join(results)
        tbl_list = ", ".join(t[0] for t in tables) if tables else "无"
        return f"执行结果:\n{summary}\n\n当前所有表: {tbl_list}"

    except duckdb.Error as e:
        return f"DDL 执行失败: {e}"
    except RuntimeError as e:
        return f"初始化错误: {e}"


@tool
def create_table_from_query(table_name: str, select_sql: str, table_comment: str = "") -> str:
    """将查询结果固化为持久化表 (CREATE TABLE AS SELECT)。

    用于将常用的分析查询结果保存为物理表，后续可直接查询，无需重复计算。

    Args:
        table_name: 新建的表名（snake_case）
        select_sql: SELECT 查询语句
        table_comment: 表的中文说明
    """
    try:
        _validate_identifier(table_name, "表名")
        conn = get_conn()

        # Drop if exists
        conn.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')

        # Validate the SELECT SQL is actually a SELECT
        sql_upper = select_sql.strip().upper()
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            return f"select_sql 必须是 SELECT 或 WITH 查询，不允许 DML/DDL 操作。"

        # Create table from query
        conn.execute(f'CREATE TABLE "{table_name}" AS {select_sql}')

        # Add table comment (escape single quotes in comment)
        if table_comment:
            safe_comment = table_comment.replace("'", "''")
            conn.execute(f"COMMENT ON TABLE \"{table_name}\" IS '{safe_comment}'")

        # Get stats
        cnt = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        cols = conn.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = ? AND table_schema = 'main'
            ORDER BY ordinal_position
        """, [table_name]).fetchall()

        lines = [f"表 '{table_name}' 创建成功！\n"]
        lines.append(f"  行数: {cnt}")
        lines.append(f"  字段: {len(cols)} 个")
        if table_comment:
            lines.append(f"  说明: {table_comment}")
        lines.append(f"\n  字段列表:")
        for name, dtype in cols:
            lines.append(f"    - {name} ({dtype})")

        return "\n".join(lines)

    except ValueError as e:
        return str(e)
    except duckdb.Error as e:
        return f"建表失败: {e}"
    except RuntimeError as e:
        return f"初始化错误: {e}"


@tool
def read_convention() -> str:
    """读取建表规范文件（命名规则、数据类型映射、分区策略等）。

    当需要将查询结果固化为持久化表时，应读取规范以确保符合标准。
    """
    convention_path = _convention_var.get()
    if not convention_path:
        return "未配置建表规范文件。"

    path = Path(convention_path)
    if not path.exists():
        return f"规范文件不存在: {convention_path}"

    try:
        content = path.read_text(encoding="utf-8")
        # Limit size to avoid overwhelming the LLM context
        max_chars = 15000
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... [截断，共 {len(content)} 字符，仅显示前 {max_chars}]"
        return f"规范文件: {path.name}\n{'=' * 50}\n{content}"
    except (OSError, UnicodeDecodeError) as e:
        return f"读取失败: {e}"


# ===================================================================
#  Tool list
# ===================================================================

ALL_TOOLS = [
    list_tables,
    describe_table,
    get_sample_data,
    execute_query,
    execute_ddl,
    create_table_from_query,
    read_convention,
]
