"""
DataForge AI - LangChain Tools for Database Introspection & DDL Verification

These tools are registered with the LLM agent via LangChain's tool-calling
protocol. The model decides autonomously which tools to invoke, in what order,
to inspect source schemas, read conventions, and verify generated DDL.

Tools:
  - list_source_tables:   List all tables in the source database
  - describe_source_table: Get column details of a source table
  - get_sample_data:      Peek at sample rows from a table
  - read_convention:      Read the table-creation convention file
  - ddl_verify:           Verify DDL by executing in DuckDB sandbox
  - list_target_tables:   List tables already created in the target DB
  - query_target:         Run a SELECT on target tables
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import duckdb
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Module-level DB handles (set once by init_tool_context)
# ---------------------------------------------------------------------------
_source_conn: Optional[duckdb.DuckDBPyConnection] = None
_target_conn: Optional[duckdb.DuckDBPyConnection] = None
_convention_path: Optional[str] = None


def init_tool_context(
    source_db: str | duckdb.DuckDBPyConnection,
    target_db: str | duckdb.DuckDBPyConnection = ":memory:",
    convention_file: Optional[str] = None,
) -> None:
    """Bind DuckDB connections and convention path for all tools.

    Call this once before creating the agent.  Accepts either a file path
    (str) or an already-opened DuckDB connection object.
    """
    global _source_conn, _target_conn, _convention_path

    if isinstance(source_db, str):
        _source_conn = duckdb.connect(source_db, read_only=True)
    else:
        _source_conn = source_db

    if isinstance(target_db, str):
        _target_conn = duckdb.connect(target_db)
    else:
        _target_conn = target_db

    _convention_path = convention_file


def get_source_conn() -> duckdb.DuckDBPyConnection:
    if _source_conn is None:
        raise RuntimeError("init_tool_context() not called — source DB not connected")
    return _source_conn


def get_target_conn() -> duckdb.DuckDBPyConnection:
    if _target_conn is None:
        raise RuntimeError("init_tool_context() not called — target DB not connected")
    return _target_conn


# ===================================================================
#  Tool definitions (decorated with @tool for LangChain registration)
# ===================================================================


@tool
def list_source_tables() -> str:
    """列出源数据库中的所有表名。

    返回每张表的表名、表类型（BASE TABLE / VIEW）和行数。
    这是了解源库全貌的第一步。
    """
    try:
        rows = get_source_conn().execute("""
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
            return "源数据库中没有任何表。"

        lines = [f"共 {len(rows)} 张表：\n"]
        for name, ttype, cnt in rows:
            lines.append(f"  - {name} ({ttype}, 约 {cnt} 行)")
        return "\n".join(lines)

    except Exception as e:
        return f"错误: {e}"


@tool
def describe_source_table(table_name: str) -> str:
    """获取指定源表的完整结构信息。

    返回每个字段的：序号、字段名、数据类型、是否可空、默认值、注释。
    用于深入了解一张表的 schema 设计。

    Args:
        table_name: 要查看的表名
    """
    try:
        cols = get_source_conn().execute("""
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
            return f"未找到表 '{table_name}'，请确认表名是否正确。"

        lines = [f"表 '{table_name}' 的结构（{len(cols)} 个字段）：\n"]
        lines.append(f"{'序号':<4}  {'字段名':<25} {'数据类型':<20} {'可空':<5} {'默认值'}")
        lines.append("-" * 80)
        for pos, name, dtype, nullable, default in cols:
            d = default if default else "-"
            lines.append(f"{pos:<4}  {name:<25} {dtype:<20} {nullable:<5} {d}")

        # 追加行数
        try:
            cnt = get_source_conn().execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            lines.append(f"\n总行数: {cnt}")
        except Exception:
            pass

        return "\n".join(lines)

    except Exception as e:
        return f"错误: {e}"


@tool
def get_sample_data(table_name: str, limit: int = 5) -> str:
    """查看源表的前 N 行样本数据，了解实际数据内容。

    Args:
        table_name: 表名
        limit: 返回行数，默认 5，最大 20
    """
    limit = min(max(1, limit), 20)
    try:
        rows = get_source_conn().execute(
            f'SELECT * FROM "{table_name}" LIMIT {limit}'
        ).fetchall()

        col_names = [
            desc[0] for desc in get_source_conn().description
        ]

        if not rows:
            return f"表 '{table_name}' 为空。"

        lines = [f"表 '{table_name}' 样本数据（前 {len(rows)} 行）：\n"]
        lines.append("  ".join(f"{c:<18}" for c in col_names))
        lines.append("-" * (20 * len(col_names)))
        for row in rows:
            lines.append("  ".join(f"{str(v):<18}" for v in row))
        return "\n".join(lines)

    except Exception as e:
        return f"错误: {e}"


@tool
def read_convention() -> str:
    """读取建表规范文件（YAML / Markdown）。

    规范文件定义了数仓建设的命名规则、数据类型映射、分区策略、
    注释要求、质量约束等标准。生成 DDL 时必须遵守这些规范。

    直接返回文件的完整文本内容。
    """
    if not _convention_path:
        return "未配置建表规范文件。"

    path = Path(_convention_path)
    if not path.exists():
        return f"规范文件不存在: {_convention_path}"

    try:
        content = path.read_text(encoding="utf-8")
        return f"规范文件: {path.name}\n{'=' * 50}\n{content}"
    except Exception as e:
        return f"读取规范文件失败: {e}"


@tool
def ddl_verify(ddl: str) -> str:
    """在 DuckDB 沙箱中验证 DDL 语句。

    将 CREATE TABLE 等 DDL 在目标 DuckDB 数据库中执行，
    验证语法正确性并确认建表成功。可一次提交多条语句（用分号分隔）。

    Args:
        ddl: 要验证的 DDL 语句（支持多条，以分号分隔）
    """
    try:
        conn = get_target_conn()
        stmts = [s.strip() for s in ddl.split(";") if s.strip()]
        results = []

        for i, stmt in enumerate(stmts, 1):
            upper = stmt.upper()

            # 跳过纯注释或空语句
            if upper.startswith("--") or not upper:
                continue

            # CREATE 语句：先 DROP 同名表防止重复执行时报错
            if "CREATE TABLE" in upper:
                try:
                    tbl = _extract_table_name(stmt)
                    if tbl:
                        conn.execute(f'DROP TABLE IF EXISTS "{tbl}" CASCADE')
                except Exception:
                    pass

            try:
                conn.execute(stmt)
                results.append(f"[{i}] OK")
            except Exception as e:
                results.append(f"[{i}] FAIL: {e}")

        if not results:
            return "没有可执行的语句。"

        # 列出目标库中当前所有表
        tables = conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main' ORDER BY table_name
        """).fetchall()

        summary = "\n".join(results)
        tbl_list = ", ".join(t[0] for t in tables) if tables else "无"
        return f"验证结果:\n{summary}\n\n目标库当前表: {tbl_list}"

    except Exception as e:
        return f"错误: {e}"


@tool
def list_target_tables() -> str:
    """列出目标 DuckDB 仓库库中已创建的所有表。

    用于确认 DDL 执行后的建表结果。
    """
    try:
        rows = get_target_conn().execute("""
            SELECT
                t.table_name,
                t.table_type,
                COALESCE(s.estimated_size, 0) AS row_count
            FROM information_schema.tables t
            LEFT JOIN duckdb_tables() s
                ON s.table_name = t.table_name AND s.schema_name = 'main'
            WHERE t.table_schema = 'main'
            ORDER BY t.table_name
        """).fetchall()

        if not rows:
            return "目标库中暂无表。"

        lines = [f"目标库共 {len(rows)} 张表：\n"]
        for name, ttype, cnt in rows:
            lines.append(f"  - {name} ({ttype}, {cnt} 行)")
        return "\n".join(lines)

    except Exception as e:
        return f"错误: {e}"


@tool
def query_target(sql: str) -> str:
    """在目标 DuckDB 仓库库上执行 SELECT 查询，用于验证数据。

    Args:
        sql: SELECT 查询语句
    """
    try:
        result = get_target_conn().execute(sql)
        rows = result.fetchall()
        cols = [desc[0] for desc in result.description]

        lines = [f"查询结果（{len(rows)} 行 x {len(cols)} 列）：\n"]
        lines.append("  ".join(f"{c:<18}" for c in cols))
        lines.append("-" * (20 * len(cols)))
        for row in rows[:20]:
            lines.append("  ".join(f"{str(v):<18}" for v in row))
        if len(rows) > 20:
            lines.append(f"... 省略 {len(rows) - 20} 行")
        return "\n".join(lines)

    except Exception as e:
        return f"错误: {e}"


# ===================================================================
#  Helpers
# ===================================================================


def _extract_table_name(ddl: str) -> Optional[str]:
    """从 CREATE TABLE 语句中提取表名。"""
    import re
    m = re.search(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?(\w+)["`]?',
        ddl, re.IGNORECASE
    )
    return m.group(1) if m else None


# ===================================================================
#  Tool list for agent registration
# ===================================================================

ALL_TOOLS = [
    list_source_tables,
    describe_source_table,
    get_sample_data,
    read_convention,
    ddl_verify,
    list_target_tables,
    query_target,
]
