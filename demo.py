#!/usr/bin/env python3
"""
DataForge AI - 智能数据分析演示
==================================

直接运行：  python demo.py

演示场景：
  用户在电商业务库中，用自然语言提出数据分析需求。
  AI Agent 自主探索表结构 → 打印思考过程 → 生成 SQL → 执行验证 → 建议固化。

示例提问：
  1. "查2025年6月每个商品的购买数量和总金额，按金额降序"
  2. "统计各省份VIP用户（vip_level>=3）的月均消费，找出高价值区域"

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
#  演示场景
# ===================================================================

DEMO_QUESTIONS = [
    {
        "question": "查2025年6月每个商品的购买数量和总金额，按金额降序排列",
        "note": "基础聚合查询，关联 orders + order_items，按月份筛选",
    },
    {
        "question": "统计各省份VIP用户（vip_level>=3）在6月的消费总额，找出高价值区域",
        "note": "多表关联（users + orders + order_items）+ 条件筛选 + 分组聚合",
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
    print("  DataForge AI - 智能数据分析助手 演示")
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
    #  Step 3: 创建 Agent
    # ------------------------------------------------------------------
    print_section("Step 3: 创建 SQL 分析 Agent")

    from src.warehouse.sql_agent import SQLAgent

    convention_file = str(PROJECT_ROOT / "conventions" / "default_convention.yaml")
    if not os.path.exists(convention_file):
        convention_file = None

    agent = SQLAgent(
        llm=llm,
        db=conn,
        convention_file=convention_file,
    )
    print(f"    已注册 {len(agent.tools)} 个工具:")
    for t in agent.tools:
        desc = t.description.split("\n")[0][:50]
        print(f"      - {t.name}: {desc}")

    # ------------------------------------------------------------------
    #  Step 4: 运行分析
    # ------------------------------------------------------------------
    all_results = []

    for i, demo in enumerate(DEMO_QUESTIONS, 1):
        q = demo["question"]
        note = demo["note"]

        print_section(f"提问 #{i}: {q}")
        print(f"    场景说明: {note}")
        print()
        print("    --- Agent 工作过程 ---")
        print()

        result = agent.analyze(q)
        all_results.append(result)

        print()
        print("    --- Agent 最终回复 ---")
        print()

        if result.reasoning:
            print("    【思考过程】")
            for line in result.reasoning.splitlines():
                stripped = line.strip()
                if stripped:
                    print(f"    {stripped}")
            print()

        if result.sql:
            print("    生成的 SQL:")
            for line in result.sql.splitlines():
                print(f"      {line}")
            print()

        print(f"    工具调用 ({len(result.tool_calls_log)} 次):")
        for entry in result.tool_calls_log:
            args = entry["args"]
            args_str = ", ".join(f"{k}={str(v)[:40]}" for k, v in args.items())
            print(f"      [{entry['step']}] {entry['tool']}({args_str})")
        print()

    # ------------------------------------------------------------------
    #  Step 5: 汇总
    # ------------------------------------------------------------------
    print_section("汇总")
    for i, r in enumerate(all_results, 1):
        status = "OK" if r.success else "FAIL"
        has_sql = "有SQL" if r.sql else "无SQL"
        has_reasoning = "有思考" if r.reasoning else "无思考"
        calls = len(r.tool_calls_log)
        print(f"    #{i} [{status}] {r.question[:40]}...  ({has_sql}, {has_reasoning}, {calls}次工具调用)")
    print()
    print("  演示完成！")
    print()

    conn.close()


if __name__ == "__main__":
    main()
