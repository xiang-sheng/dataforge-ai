#!/usr/bin/env python3
"""
DataForge AI - DDL 自动生成演示
=================================

直接运行，无需启动 FastAPI 服务：

    python demo.py

功能流程:
  1. 在 DuckDB 中创建模拟业务源表（电商场景）
  2. AI Agent（Ollama 本地模型）自主探索源表结构
  3. Agent 读取建表规范文件
  4. Agent 为数仓 DWD 层设计目标表
  5. Agent 生成 DDL 并在 DuckDB 沙箱中验证

依赖安装:
    pip install langchain langchain-community langchain-core duckdb pyyaml

前置条件:
    ollama serve  # 确保 Ollama 正在运行
    ollama pull qwen2.5:14b  # 或其他支持 tool-calling 的模型
"""

from __future__ import annotations

import sys
import os
import tempfile
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# 将项目根目录加入 sys.path，支持直接 python demo.py 运行
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import duckdb


# ===================================================================
#  Step 1: 创建模拟业务源表
# ===================================================================

SAMPLE_DDL = """
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
    shipping_addr   VARCHAR(500),
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE  orders IS '订单主表';
COMMENT ON COLUMN orders.id IS '订单ID';
COMMENT ON COLUMN orders.order_no IS '订单编号';
COMMENT ON COLUMN orders.user_id IS '用户ID';
COMMENT ON COLUMN orders.total_amount IS '订单总金额';
COMMENT ON COLUMN orders.status IS '订单状态(pending/paid/shipped/completed/cancelled)';
COMMENT ON COLUMN orders.payment_method IS '支付方式(alipay/wechat/card)';
COMMENT ON COLUMN orders.order_time IS '下单时间';
COMMENT ON COLUMN orders.pay_time IS '支付时间';
COMMENT ON COLUMN orders.shipping_addr IS '收货地址';
COMMENT ON COLUMN orders.created_at IS '创建时间';
COMMENT ON COLUMN orders.updated_at IS '更新时间';

-- 订单明细表
CREATE TABLE IF NOT EXISTS order_items (
    id            BIGINT        PRIMARY KEY,
    order_id      BIGINT        NOT NULL,
    product_id    BIGINT        NOT NULL,
    product_name  VARCHAR(200)  NOT NULL,
    category      VARCHAR(100),
    unit_price    DECIMAL(10,2) NOT NULL,
    quantity      INTEGER       NOT NULL,
    subtotal      DECIMAL(10,2) NOT NULL,
    created_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE  order_items IS '订单商品明细表';
COMMENT ON COLUMN order_items.id IS '明细ID';
COMMENT ON COLUMN order_items.order_id IS '关联订单ID';
COMMENT ON COLUMN order_items.product_id IS '商品ID';
COMMENT ON COLUMN order_items.product_name IS '商品名称';
COMMENT ON COLUMN order_items.category IS '商品类目';
COMMENT ON COLUMN order_items.unit_price IS '单价';
COMMENT ON COLUMN order_items.quantity IS '购买数量';
COMMENT ON COLUMN order_items.subtotal IS '小计金额';
COMMENT ON COLUMN order_items.created_at IS '创建时间';

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id            BIGINT       PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL,
    phone         VARCHAR(20),
    email         VARCHAR(100),
    gender        VARCHAR(10),
    age           INTEGER,
    city          VARCHAR(50),
    province      VARCHAR(50),
    register_time TIMESTAMP    NOT NULL,
    last_login    TIMESTAMP,
    vip_level     INTEGER      DEFAULT 0
);

COMMENT ON TABLE  users IS '用户信息表';
COMMENT ON COLUMN users.id IS '用户ID';
COMMENT ON COLUMN users.username IS '用户名';
COMMENT ON COLUMN users.phone IS '手机号';
COMMENT ON COLUMN users.email IS '邮箱';
COMMENT ON COLUMN users.gender IS '性别';
COMMENT ON COLUMN users.age IS '年龄';
COMMENT ON COLUMN users.city IS '城市';
COMMENT ON COLUMN users.province IS '省份';
COMMENT ON COLUMN users.register_time IS '注册时间';
COMMENT ON COLUMN users.last_login IS '最后登录时间';
COMMENT ON COLUMN users.vip_level IS '会员等级(0-5)';
"""

SAMPLE_DATA = """
-- 用户数据
INSERT INTO users VALUES
(1001, '张三', '13800001111', 'zhang@example.com', '男', 28, '杭州', '浙江', '2024-01-15 10:00:00', '2025-06-01 14:30:00', 2),
(1002, '李四', '13900002222', 'li@example.com',   '女', 35, '上海', '上海', '2024-03-20 09:00:00', '2025-05-28 08:15:00', 3),
(1003, '王五', '13700003333', NULL,                 '男', 42, '北京', '北京', '2024-06-10 16:00:00', '2025-06-05 20:00:00', 1);

-- 订单数据
INSERT INTO orders VALUES
(1, 'ORD20250601001', 1001, 299.00, 'completed', 'alipay', '2025-06-01 14:30:00', '2025-06-01 14:31:00', '浙江省杭州市西湖区xxx路1号', '2025-06-01 14:30:00', '2025-06-01 14:31:00'),
(2, 'ORD20250602001', 1002, 1580.00, 'shipped',  'wechat', '2025-06-02 09:15:00', '2025-06-02 09:16:00', '上海市浦东新区xxx路2号',     '2025-06-02 09:15:00', '2025-06-02 10:00:00'),
(3, 'ORD20250603001', 1003, 89.90,  'pending',   NULL,     '2025-06-03 20:00:00', NULL,                   '北京市朝阳区xxx路3号',         '2025-06-03 20:00:00', '2025-06-03 20:00:00');

-- 订单明细
INSERT INTO order_items VALUES
(1, 1, 5001, '机械键盘',   '数码配件', 259.00, 1, 259.00, '2025-06-01 14:30:00'),
(2, 1, 5002, '鼠标垫',     '数码配件', 40.00,  1, 40.00,  '2025-06-01 14:30:00'),
(3, 2, 6001, '运动T恤',    '服饰',     199.00, 2, 398.00,  '2025-06-02 09:15:00'),
(4, 2, 6002, '运动短裤',   '服饰',     159.00, 1, 159.00,  '2025-06-02 09:15:00'),
(5, 2, 6003, '运动袜3双装', '服饰',     69.00,  1, 69.00,   '2025-06-02 09:15:00'),
(6, 3, 7001, '保温杯',     '生活用品', 89.90,  1, 89.90,   '2025-06-03 20:00:00');
"""


def create_sample_db(path: str) -> duckdb.DuckDBPyConnection:
    """创建包含模拟业务数据的 DuckDB 数据库。"""
    conn = duckdb.connect(path)
    conn.execute(SAMPLE_DDL)
    conn.execute(SAMPLE_DATA)
    return conn


# ===================================================================
#  Step 2-5: 主流程
# ===================================================================

def main():
    print()
    print("=" * 64)
    print("  DataForge AI - DDL 自动生成演示")
    print("=" * 64)
    print()

    # ------------------------------------------------------------------
    #  创建源库
    # ------------------------------------------------------------------
    source_path = os.path.join(tempfile.gettempdir(), "dataforge_source.duckdb")
    if os.path.exists(source_path):
        os.remove(source_path)

    print("  [Step 1] 创建模拟业务源库 ...")
    source_conn = create_sample_db(source_path)

    tables = source_conn.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' ORDER BY table_name
    """).fetchall()
    for t in tables:
        cnt = source_conn.execute(f'SELECT COUNT(*) FROM "{t[0]}"').fetchone()[0]
        print(f"    - {t[0]}  ({cnt} 行)")
    print()

    # ------------------------------------------------------------------
    #  初始化 LLM
    # ------------------------------------------------------------------
    print("  [Step 2] 初始化 Ollama LLM ...")
    try:
        from langchain_community.chat_models import ChatOllama
    except ImportError:
        print("    !! 请先安装依赖: pip install langchain-community")
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
        # 验证连通性
        llm.invoke("ping")
        print(f"    模型: {model_name}  (base_url: {base_url})")
    except Exception as e:
        print(f"    !! 无法连接 Ollama: {e}")
        print("    请确认:")
        print("      1. ollama serve 已启动")
        print(f"      2. 模型 {model_name} 已拉取 (ollama pull {model_name})")
        sys.exit(1)
    print()

    # ------------------------------------------------------------------
    #  定位规范文件
    # ------------------------------------------------------------------
    convention_file = str(PROJECT_ROOT / "conventions" / "default_convention.yaml")
    if not os.path.exists(convention_file):
        print(f"    !! 规范文件不存在: {convention_file}")
        convention_file = None

    # ------------------------------------------------------------------
    #  创建 Agent
    # ------------------------------------------------------------------
    print("  [Step 3] 创建 DDL Agent ...")
    from src.warehouse.ddl_agent import DDLAgent

    target_conn = duckdb.connect(":memory:")

    agent = DDLAgent(
        llm=llm,
        source_db=source_conn,       # 传入已有的连接对象
        target_db=target_conn,
        convention_file=convention_file,
    )
    print(f"    工具: {[t.name for t in agent.tools]}")
    print()

    # ------------------------------------------------------------------
    #  驱动 Agent 生成 DDL
    # ------------------------------------------------------------------
    demo_tables = [
        ("orders",      "DWD", "订单事实表，需要关联用户维度退化"),
        ("order_items", "DWD", "订单商品明细事实表"),
    ]

    all_results = []

    for table_name, layer, desc in demo_tables:
        print(f"  {'=' * 60}")
        print(f"  [Step 4] Agent 处理: {table_name} → {layer} 层")
        print(f"  业务说明: {desc}")
        print(f"  {'=' * 60}")
        print()
        print("    Agent 工具调用过程:")

        result = agent.build(
            source_table=table_name,
            target_layer=layer,
            business_desc=desc,
        )

        print()
        if result.success:
            print(f"    DDL 生成成功:")
            print()
            for line in (result.ddl or "").splitlines():
                print(f"      {line}")
            print()
            print(f"    验证结果: {result.verification}")
        else:
            print(f"    !! DDL 生成失败: {result.error}")

        print()
        print(f"    共 {len(result.tool_calls_log)} 次工具调用:")
        for entry in result.tool_calls_log:
            print(f"      [{entry['step']}] {entry['tool']}({entry['args']})")

        print()
        all_results.append(result)

    # ------------------------------------------------------------------
    #  检查目标库建表结果
    # ------------------------------------------------------------------
    print(f"  {'=' * 60}")
    print("  [Step 5] 目标仓库库建表结果")
    print(f"  {'=' * 60}")
    print()

    try:
        created = target_conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main' ORDER BY table_name
        """).fetchall()

        if created:
            for t in created:
                cols = target_conn.execute("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = ? AND table_schema = 'main'
                    ORDER BY ordinal_position
                """, [t[0]]).fetchall()
                print(f"    表: {t[0]}  ({len(cols)} 个字段)")
                for col_name, col_type in cols:
                    print(f"      - {col_name:<25} {col_type}")
                print()
        else:
            print("    目标库中没有表（Agent 可能未成功建表）")
    except Exception as e:
        print(f"    查询目标库失败: {e}")

    # ------------------------------------------------------------------
    #  汇总
    # ------------------------------------------------------------------
    print(f"  {'=' * 60}")
    print("  汇总")
    print(f"  {'=' * 60}")
    print()
    for r in all_results:
        status = "OK" if r.success else "FAIL"
        calls = len(r.tool_calls_log)
        print(f"    [{status}] {r.source_table} → {r.target_layer}  (工具调用 {calls} 次)")
    print()
    print("  演示完成!")
    print()

    # 清理
    source_conn.close()
    target_conn.close()


if __name__ == "__main__":
    main()
