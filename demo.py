#!/usr/bin/env python3
"""
DataForge AI - 多 Agent 智能数据分析演示
==========================================

直接运行：  python demo.py

演示场景：
  通过 AgentOrchestrator 统一入口，用户用自然语言交互。
  系统自动识别意图 → 路由到对应 Agent（智能问数 / 智能建表）。

能力展示：
  1. 智能问数：自然语言 → 思考过程 → SQL → 执行验证 → 建议固化
  2. 智能建表：源表结构 → 读取规范 → 生成数仓 DDL → 验证
  3. 意图路由：同一入口自动分类到不同 Agent

依赖：  pip install langchain langchain-community langchain-core duckdb pyyaml
前置：  ollama serve && ollama pull qwen2.5:14b
"""

from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import duckdb


# ===================================================================
#  模拟业务数据库（电商场景）
# ===================================================================

INIT_SQL = """
-- 订单主表
CREATE TABLE IF NOT EXISTS orders (
    id              BIGINT       PRIMARY KEY,
    order_no        VARCHAR(32)  NOT NULL,
    user_id         BIGINT       NOT NULL,
    total_amount    DECIMAL(10,2) NOT NULL,
    status          VARCHAR(20)  NOT NULL,
    payment_method  VARCHAR(20),
    order_time      TIMESTAMP    NOT NULL,
    pay_time        TIMESTAMP,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON TABLE orders IS '订单主表';

-- 订单明细表
CREATE TABLE IF NOT EXISTS order_items (
    id            BIGINT        PRIMARY KEY,
    order_id      BIGINT        NOT NULL,
    product_id    BIGINT        NOT NULL,
    product_name  VARCHAR(200)  NOT NULL,
    category      VARCHAR(100),
    unit_price    DECIMAL(10,2) NOT NULL,
    quantity      INTEGER       NOT NULL,
    subtotal      DECIMAL(10,2) NOT NULL
);
COMMENT ON TABLE order_items IS '订单商品明细表';

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id            BIGINT       PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL,
    phone         VARCHAR(20),
    gender        VARCHAR(10),
    city          VARCHAR(50),
    province      VARCHAR(50),
    register_time TIMESTAMP    NOT NULL,
    vip_level     INTEGER      DEFAULT 0
);
COMMENT ON TABLE users IS '用户信息表';

-- 冗余表：订单主表的备份（结构高度相似）
CREATE TABLE IF NOT EXISTS orders_bak (
    id              BIGINT       PRIMARY KEY,
    order_no        VARCHAR(32)  NOT NULL,
    user_id         BIGINT       NOT NULL,
    total_amount    DECIMAL(10,2) NOT NULL,
    status          VARCHAR(20)  NOT NULL,
    payment_method  VARCHAR(20),
    order_time      TIMESTAMP    NOT NULL,
    pay_time        TIMESTAMP,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON TABLE orders_bak IS '订单主表备份（冗余）';

-- 冗余表：订单明细表的副本（结构几乎相同）
CREATE TABLE IF NOT EXISTS order_items_v2 (
    id            BIGINT        PRIMARY KEY,
    order_id      BIGINT        NOT NULL,
    product_id    BIGINT        NOT NULL,
    product_name  VARCHAR(200)  NOT NULL,
    category      VARCHAR(100),
    unit_price    DECIMAL(10,2) NOT NULL,
    quantity      INTEGER       NOT NULL,
    subtotal      DECIMAL(10,2) NOT NULL,
    extra_note    VARCHAR(200)
);
COMMENT ON TABLE order_items_v2 IS '订单商品明细表V2版本（冗余）';
"""

SAMPLE_DATA = """
INSERT INTO users VALUES
(1001, '张三', '138****1111', '男', '杭州', '浙江', '2024-01-15 10:00:00', 2),
(1002, '李四', '139****2222', '女', '上海', '上海', '2024-03-20 09:00:00', 3),
(1003, '王五', '137****3333', '男', '北京', '北京', '2024-06-10 16:00:00', 1),
(1004, '赵六', '136****4444', '女', '深圳', '广东', '2024-02-01 08:00:00', 4),
(1005, '孙七', '135****5555', '男', '成都', '四川', '2024-07-20 11:00:00', 0);

INSERT INTO orders VALUES
(1,  'ORD-20250501-001', 1001,  299.00,  'completed', 'alipay',  '2025-05-01 14:30:00', '2025-05-01 14:31:00', '2025-05-01 14:30:00'),
(2,  'ORD-20250505-002', 1002, 1580.00,  'completed', 'wechat',  '2025-05-05 09:15:00', '2025-05-05 09:16:00', '2025-05-05 09:15:00'),
(3,  'ORD-20250515-003', 1004,  458.00,  'completed', 'alipay',  '2025-05-15 16:00:00', '2025-05-15 16:01:00', '2025-05-15 16:00:00'),
(4,  'ORD-20250601-004', 1001,  359.00,  'completed', 'alipay',  '2025-06-01 10:00:00', '2025-06-01 10:01:00', '2025-06-01 10:00:00'),
(5,  'ORD-20250603-005', 1003,   89.90,  'shipped',   'wechat',  '2025-06-03 20:00:00', '2025-06-03 20:01:00', '2025-06-03 20:00:00'),
(6,  'ORD-20250610-006', 1002, 2100.00,  'completed', 'card',    '2025-06-10 11:30:00', '2025-06-10 11:31:00', '2025-06-10 11:30:00'),
(7,  'ORD-20250615-007', 1004,  670.00,  'completed', 'alipay',  '2025-06-15 14:00:00', '2025-06-15 14:01:00', '2025-06-15 14:00:00'),
(8,  'ORD-20250620-008', 1005,  129.00,  'pending',   NULL,      '2025-06-20 19:00:00', NULL,                   '2025-06-20 19:00:00'),
(9,  'ORD-20250625-009', 1001,  888.00,  'completed', 'wechat',  '2025-06-25 08:00:00', '2025-06-25 08:01:00', '2025-06-25 08:00:00');

INSERT INTO order_items VALUES
(1,  1, 5001, '机械键盘',     '数码配件', 259.00,  1, 259.00),
(2,  1, 5002, '鼠标垫',       '数码配件',  40.00,  1,  40.00),
(3,  2, 6001, '运动T恤',      '服饰',     199.00,  2, 398.00),
(4,  2, 6002, '运动短裤',      '服饰',     159.00,  1, 159.00),
(5,  2, 6003, '跑步鞋',       '鞋靴',    1023.00,  1,1023.00),
(6,  3, 6004, '瑜伽垫',       '运动',     128.00,  1, 128.00),
(7,  3, 6005, '运动水壶',      '运动',      68.00,  1,  68.00),
(8,  3, 6001, '运动T恤',      '服饰',     199.00,  1, 199.00),
(9,  4, 5003, '无线鼠标',      '数码配件', 159.00,  1, 159.00),
(10, 4, 5004, 'USB-C 扩展坞',  '数码配件', 200.00,  1, 200.00),
(11, 5, 7001, '保温杯',       '生活用品',  89.90,  1,  89.90),
(12, 6, 8001, '蓝牙音箱',      '数码配件', 899.00,  1, 899.00),
(13, 6, 6006, '运动背包',      '配饰',    1201.00,  1,1201.00),
(14, 7, 6007, '运动手环',      '数码配件', 399.00,  1, 399.00),
(15, 7, 6008, '运动毛巾',      '运动',      29.00,  3,  87.00),
(16, 7, 6009, '蛋白粉',       '食品',     184.00,  1, 184.00),
(17, 8, 7002, '收纳盒',       '生活用品',  49.00,  2,  98.00),
(18, 8, 7003, '桌面台灯',      '生活用品',  31.00,  1,  31.00),
(19, 9, 5001, '机械键盘',     '数码配件', 259.00,  2, 518.00),
(20, 9, 5005, '显示器支架',     '数码配件', 370.00,  1, 370.00);
"""


def create_demo_db() -> duckdb.DuckDBPyConnection:
    db_path = os.path.join(tempfile.gettempdir(), "dataforge_demo.duckdb")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = duckdb.connect(db_path)
    conn.execute(INIT_SQL)
    conn.execute(SAMPLE_DATA)
    return conn


# ===================================================================
#  演示对话（混合意图：问数 + 建表）
# ===================================================================

DEMO_CONVERSATIONS = [
    {
        "message": "查2025年6月每个商品的购买数量和总金额，按金额降序排列",
        "note": "意图：智能问数 → 自动路由到 sql_query Agent",
        "expect_agent": "sql_query",
    },
    {
        "message": "统计各省份VIP用户（vip_level>=3）在6月的消费总额",
        "note": "意图：智能问数 → 多表关联分析",
        "expect_agent": "sql_query",
    },
    {
        "message": "请为 order_items 源表生成 DWS 层的目标表 DDL，按日期和商品维度汇总",
        "note": "意图：智能建表 → 自动路由到 ddl_build Agent",
        "expect_agent": "ddl_build",
    },
    {
        "message": "扫描当前数据库，检查有没有冗余表或重叠的表结构",
        "note": "意图：数据治理 → 自动路由到 data_governance Agent",
        "expect_agent": "data_governance",
    },
]


def print_section(title: str, char: str = "="):
    print()
    print(f"  {char * 60}")
    print(f"  {title}")
    print(f"  {char * 60}")
    print()


def main():
    print()
    print("  " + "=" * 60)
    print("  DataForge AI - 多 Agent 智能数据分析 演示")
    print("  " + "=" * 60)

    # ------------------------------------------------------------------
    #  Step 1: 创建业务数据库
    # ------------------------------------------------------------------
    print_section("Step 1: 创建模拟电商业务库")

    conn = create_demo_db()
    tables = conn.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' ORDER BY table_name
    """).fetchall()
    for t in tables:
        cnt = conn.execute(f'SELECT COUNT(*) FROM "{t[0]}"').fetchone()[0]
        print(f"    {t[0]:<20} {cnt} 行")

    # ------------------------------------------------------------------
    #  Step 2: 初始化 LLM
    # ------------------------------------------------------------------
    print_section("Step 2: 初始化 Ollama LLM")

    try:
        from langchain_community.chat_models import ChatOllama
    except ImportError:
        print("    !! pip install langchain-community")
        sys.exit(1)

    model_name = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    try:
        llm = ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=0.1,
            request_timeout=300,
        )
        llm.invoke("ping")
        print(f"    模型: {model_name}")
        print(f"    地址: {base_url}")
    except Exception as e:
        print(f"    !! 无法连接 Ollama: {e}")
        print(f"    请确认: ollama serve 已启动 && ollama pull {model_name}")
        sys.exit(1)

    # ------------------------------------------------------------------
    #  Step 3: 创建多 Agent 编排器
    # ------------------------------------------------------------------
    print_section("Step 3: 初始化多 Agent 编排器")

    from src.agents import (
        AgentRegistry,
        AgentOrchestrator,
        SQLAgentWrapper,
        DDLAgentWrapper,
        GovernanceAgentWrapper,
    )

    convention_file = str(PROJECT_ROOT / "conventions" / "default_convention.yaml")
    if not os.path.exists(convention_file):
        convention_file = None

    registry = AgentRegistry()

    sql_wrapper = SQLAgentWrapper(llm=llm, db=conn, convention_file=convention_file)
    ddl_wrapper = DDLAgentWrapper(llm=llm, db=conn, convention_file=convention_file)
    gov_wrapper = GovernanceAgentWrapper(llm=llm, db=conn, convention_file=convention_file)

    registry.register(sql_wrapper)
    registry.register(ddl_wrapper)
    registry.register(gov_wrapper)

    orchestrator = AgentOrchestrator(registry, llm)

    print("    已注册的 Agent:")
    for info in orchestrator.list_agents():
        print(f"      [{info['name']}] {info['description']}")
        print(f"        关键词: {info['keywords']}")

    # ------------------------------------------------------------------
    #  Step 4: 通过统一入口交互（自动意图路由）
    # ------------------------------------------------------------------
    results = []

    for i, demo in enumerate(DEMO_CONVERSATIONS, 1):
        msg = demo["message"]
        note = demo["note"]
        expected = demo["expect_agent"]

        print_section(f"对话 #{i}", "-")
        print(f"    用户: {msg}")
        print(f"    说明: {note}")
        print(f"    预期路由: {expected}")
        print()

        # Pass db connection as context
        result = orchestrator.chat(
            message=msg,
            context={"db": conn, "convention_file": convention_file},
        )

        results.append((demo, result))

        print(f"    → 路由到: {result.agent_name}")
        print(f"    → 状态: {'成功' if result.success else '失败'}")
        print()

        # Print content (truncated for readability)
        if result.content:
            lines = result.content.splitlines()
            print("    --- 回复内容 ---")
            for line in lines[:30]:
                print(f"    {line}")
            if len(lines) > 30:
                print(f"    ... 省略 {len(lines) - 30} 行")
            print()

        if result.error:
            print(f"    错误: {result.error}")
            print()

        # Show metadata
        meta = result.metadata
        if meta.get("tool_calls"):
            print(f"    工具调用次数: {meta['tool_calls']}")
        print()

    # ------------------------------------------------------------------
    #  Step 5: 显式指定 Agent（跳过意图分类）
    # ------------------------------------------------------------------
    print_section("Step 5: 显式指定 Agent（跳过意图分类）")
    print("    使用 target_agent 参数直接路由，无需 LLM 分类")
    print()

    explicit_result = orchestrator.chat(
        message="查2025年5月所有订单的总金额",
        target_agent="sql_query",
        context={"db": conn, "convention_file": convention_file},
    )

    print(f"    用户: 查2025年5月所有订单的总金额")
    print(f"    → 指定 Agent: sql_query（跳过分类）")
    print(f"    → 状态: {'成功' if explicit_result.success else '失败'}")
    if explicit_result.content:
        lines = explicit_result.content.splitlines()
        for line in lines[:15]:
            print(f"    {line}")
    print()

    # ------------------------------------------------------------------
    #  汇总
    # ------------------------------------------------------------------
    print_section("汇总")

    for i, (demo, result) in enumerate(results, 1):
        status = "OK" if result.success else "FAIL"
        routed = result.agent_name
        expected = demo["expect_agent"]
        match = "✓" if routed == expected else "✗"
        calls = result.metadata.get("tool_calls", 0)
        print(f"    #{i} [{status}] 路由: {routed} {match}  工具调用: {calls}次")
        print(f"        {demo['message'][:50]}...")

    print()
    print(f"    显式指定: [{'OK' if explicit_result.success else 'FAIL'}]")
    print()
    print("  演示完成！")
    print()

    conn.close()

    # Clean up temp DuckDB file
    db_path = os.path.join(tempfile.gettempdir(), "dataforge_demo.duckdb")
    if os.path.exists(db_path):
        os.remove(db_path)


if __name__ == "__main__":
    main()
