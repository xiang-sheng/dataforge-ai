# DataForge AI 功能文档

> 版本 0.1.0 · 最后更新 2026-06-06

---

## 目录

- [一、平台概述](#一平台概述)
- [二、环境准备与启动](#二环境准备与启动)
- [三、功能模块总览](#三功能模块总览)
- [四、数据源连接管理](#四数据源连接管理)
- [五、DDL 自动生成引擎（核心功能）](#五ddl-自动生成引擎核心功能)
- [六、建表规范管理](#六建表规范管理)
- [七、DuckDB 本地验证沙箱](#七duckdb-本地验证沙箱)
- [八、AI 智能建模](#八ai-智能建模)
- [九、SQL 生成与优化](#九sql-生成与优化)
- [十、数据血缘追踪](#十数据血缘追踪)
- [十一、ETL 流水线编排](#十一etl-流水线编排)
- [十二、数仓分层管理](#十二数仓分层管理)
- [十三、LLM 模型配置](#十三llm-模型配置)
- [十四、完整配置参考](#十四完整配置参考)
- [十五、API 端点速查表](#十五api-端点速查表)

---

## 一、平台概述

DataForge AI 是一款面向数据工程师和数据架构师的 AI 驱动型数据仓库构建平台。它将大语言模型的能力与企业数仓开发全流程深度融合，覆盖从数据源接入、元数据采集、维度建模、DDL/SQL 自动生成、数据血缘追踪到 ETL 流水线编排的完整链路。

**核心价值：**

| 痛点 | 解决方式 |
|---|---|
| 多异构数据库手工对接 | 统一连接层，支持 7+ 主流数据库即插即用 |
| 建表规范难以统一落地 | 规范文件（YAML）驱动，自动校验命名/类型/分区 |
| DDL 和计算 SQL 手写低效 | 基于源表元数据 + 规范自动生成，DuckDB 本地验证 |
| SQL 编写与调优门槛高 | 自然语言转 SQL + AI 智能优化 |
| 数据血缘关系不透明 | 自动解析 SQL 生成字段级血缘图谱 |
| ETL 流程设计复杂 | DAG 编排，自动生成 Airflow 可执行代码 |

---

## 二、环境准备与启动

### 2.1 前置条件

| 组件 | 最低版本 | 用途 | 是否必须 |
|---|---|---|---|
| Python | >= 3.11 | 运行时 | 必须 |
| Ollama | 最新版 | 本地 LLM 推理 | 推荐使用本地模型时需要 |
| PostgreSQL | >= 14 | 元数据存储 | 生产环境必须，开发可跳过 |
| Redis | >= 7 | 缓存 | 可选，不启动则降级运行 |

### 2.2 安装与启动

```bash
# 1. 克隆项目
git clone https://github.com/xiang-sheng/dataforge-ai.git
cd dataforge-ai

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 LLM 相关项（见第十三章）

# 5. 启动服务
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### 2.3 启动后可访问的地址

| 地址 | 说明 |
|---|---|
| http://localhost:8000/docs | Swagger UI 交互式 API 文档 |
| http://localhost:8000/redoc | ReDoc 文档视图 |
| http://localhost:8000/health | 健康检查 |

### 2.4 本地模型启动（推荐）

```bash
# 安装 Ollama 后
ollama serve                      # 启动 Ollama 服务
ollama pull qwen2.5:14b           # 拉取推荐模型
```

在 `.env` 中配置：

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
```

---

## 三、功能模块总览

平台包含 **7 大功能模块**，共 **37 个 API 端点**：

```
┌──────────────────────────────────────────────────────────────┐
│                    DataForge AI 功能架构                      │
├───────────┬───────────┬───────────┬───────────┬──────────────┤
│  数据源   │  DDL 自动  │  AI 建模  │  SQL 引擎 │  血缘 & ETL  │
│  连接管理 │  生成引擎  │  & 审查   │  生成优化  │   & 分层     │
├───────────┼───────────┼───────────┼───────────┼──────────────┤
│ ·CRUD连接 │ ·读取源表  │ ·维度建模 │ ·NL→SQL  │ ·表级血缘   │
│ ·测试连通 │ ·加载规范  │ ·模型审查 │ ·SQL解释  │ ·字段级血缘 │
│ ·浏览库表 │ ·生成DDL   │ ·分区建议 │ ·SQL优化  │ ·影响分析   │
│ ·获取列信息│ ·生成计算SQL│ ·建模建议│ ·方言翻译 │ ·ETL管道    │
│           │ ·DuckDB验证│          │ ·执行SQL  │ ·DAG生成    │
│           │ ·AI增强   │          │          │ ·管道验证   │
└───────────┴───────────┴───────────┴───────────┴──────────────┘
```

---

## 四、数据源连接管理

管理远程和本地数据库的连接。支持注册连接、测试连通性、浏览库表结构。

### 4.1 支持的数据库类型

| 数据库 | 典型场景 | 协议/驱动 |
|---|---|---|
| MySQL | OLTP 业务数据源 | aiomysql |
| PostgreSQL | OLTP / OLAP | asyncpg |
| SQL Server | 企业 ERP/CRM | pymssql |
| Oracle | 传统核心系统 | oracledb |
| ClickHouse | 高性能 OLAP | clickhouse-driver |
| Apache Doris | 实时分析数仓 | MySQL 协议兼容 |
| Hive | 离线大数据数仓 | PyHive |

### 4.2 操作步骤

**注册一个连接：**

```bash
curl -X POST http://localhost:8000/api/v1/connections \
  -H "Content-Type: application/json" \
  -d '{
    "name": "生产 MySQL",
    "db_type": "mysql",
    "host": "192.168.1.100",
    "port": 3306,
    "username": "readonly",
    "password": "your_password",
    "default_database": "trade_db",
    "tags": ["production", "trade"]
  }'
```

**测试连通性：**

```bash
curl -X POST http://localhost:8000/api/v1/connections/{connection_id}/test
```

返回示例：

```json
{
  "success": true,
  "latency_ms": 12.5,
  "server_version": "8.0.35",
  "message": "Connection successful."
}
```

**浏览数据库列表：**

```bash
curl http://localhost:8000/api/v1/connections/{connection_id}/databases
```

**浏览表列表：**

```bash
curl "http://localhost:8000/api/v1/connections/{connection_id}/tables?database=trade_db"
```

**获取表列信息：**

```bash
curl "http://localhost:8000/api/v1/connections/{connection_id}/tables/orders/columns?database=trade_db"
```

---

## 五、DDL 自动生成引擎（核心功能）

这是平台的核心能力。完整流程为：**读取源表元数据 → 加载建表规范 → 生成目标 DDL → 生成计算 SQL → DuckDB 本地验证 → (可选) AI 增强**。

### 5.1 工作流程图

```
源数据库                    建表规范文件                   AI 模型
(MySQL/PG/CH/...)          (YAML/Markdown)              (Ollama/GPT)
     │                          │                          │
     ▼                          ▼                          │
┌──────────┐             ┌────────────┐                   │
│ 读取源表  │             │ 解析命名规则│                   │
│ 元数据    │             │ 类型映射   │                   │
│ (列/类型) │             │ 分区策略   │                   │
└────┬─────┘             └─────┬──────┘                   │
     │                         │                          │
     └──────────┬──────────────┘                          │
                ▼                                         │
        ┌──────────────┐                                  │
        │  DDL 生成引擎 │                                  │
        │  ·应用命名规则│                                  │
        │  ·映射数据类型│                                  │
        │  ·添加分区/注释│                                  │
        │  ·生成CREATE  │                                  │
        │   TABLE       │                                  │
        └──────┬───────┘                                  │
               │                                          │
               ▼                                          │
        ┌──────────────┐                                  │
        │ 计算 SQL 生成 │                                  │
        │ ODS: SELECT* │                                  │
        │ DWD: 去重清洗 │                                  │
        │ DWS: 聚合汇总 │                                  │
        │ ADS: 业务查询 │                                  │
        └──────┬───────┘                                  │
               │                                          │
               ▼                                          ▼
        ┌──────────────┐                          ┌──────────────┐
        │  DuckDB 验证  │    ──── (可选) ────▶     │  AI 审查增强  │
        │ ·DDL语法检查  │                          │ ·命名建议    │
        │ ·模拟数据插入  │                          │ ·SQL优化     │
        │ ·计算SQL执行  │                          │ ·质量检查    │
        │ ·结果预览     │                          │              │
        └──────────────┘                          └──────────────┘
```

### 5.2 操作方式

**方式一：通过 API 调用（推荐）**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/build \
  -H "Content-Type: application/json" \
  -d '{
    "source_connection_id": "conn_001",
    "source_tables": ["orders", "customers", "products"],
    "target_layer": "DWD",
    "target_db_type": "clickhouse",
    "convention_path": "conventions/default_convention.yaml",
    "include_computation_sql": true,
    "local_verify": true,
    "sample_rows_for_verify": 100,
    "ai_enhance": false
  }'
```

**方式二：直接提供 Schema（无需连接源库）**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/build \
  -H "Content-Type: application/json" \
  -d '{
    "source_schemas": [
      {
        "database_name": "trade_db",
        "table_name": "orders",
        "columns": [
          {"name": "id", "data_type": "BIGINT", "is_primary_key": true, "comment": "订单ID"},
          {"name": "user_id", "data_type": "BIGINT", "comment": "用户ID"},
          {"name": "amount", "data_type": "DECIMAL(10,2)", "comment": "订单金额"},
          {"name": "status", "data_type": "VARCHAR(20)", "comment": "订单状态"},
          {"name": "created_at", "data_type": "DATETIME", "comment": "创建时间"}
        ],
        "comment": "交易订单表"
      }
    ],
    "target_layer": "ODS",
    "target_db_type": "hive",
    "local_verify": true
  }'
```

### 5.3 返回结果说明

```json
{
  "config": { "..." },
  "tables": [
    {
      "source_table": "orders",
      "target_table": "ods_trade_orders_di",
      "target_layer": "ODS",
      "ddl": "CREATE TABLE IF NOT EXISTS ods_trade_orders_di (\n  id BIGINT COMMENT '订单ID',\n  ...\n) PARTITIONED BY (dt STRING)\nSTORED AS ORC;",
      "computation_sql": "INSERT OVERWRITE TABLE ods_trade_orders_di PARTITION (dt = '${bizdate}')\nSELECT id, user_id, amount, status, created_at, NOW() AS etl_time\nFROM trade_db.orders;",
      "convention_violations": [],
      "verify_result": {
        "ddl_success": true,
        "sql_success": true,
        "rows_generated": 100,
        "sample_preview": [{"id": 1, "user_id": 42, "amount": 199.50, "..."}]
      },
      "column_mappings": [
        {"source_column": "id", "target_column": "id", "source_type": "BIGINT", "target_type": "BIGINT"}
      ]
    }
  ],
  "total_tables": 3,
  "succeeded": 3,
  "failed": 0
}
```

### 5.4 各层计算 SQL 生成规则

| 层级 | SQL 模式 | 说明 |
|---|---|---|
| **ODS** | `INSERT INTO ods_xxx SELECT *, NOW() AS etl_time FROM source` | 原样同步 + ETL 时间戳 |
| **DWD** | `INSERT INTO dwd_xxx SELECT ROW_NUMBER() OVER(PARTITION BY pk ORDER BY ...) AS rn, ... FROM ods_xxx WHERE dt='${bizdate}'` | 去重 + 清洗 + 分区过滤 |
| **DWS** | `INSERT INTO dws_xxx SELECT stat_date, dim1, SUM(amount), COUNT(*) FROM dwd_xxx GROUP BY stat_date, dim1` | 维度聚合 |
| **ADS** | `INSERT INTO ads_xxx SELECT ... FROM dws_xxx WHERE ...` | 业务条件过滤 |

### 5.5 支持的目标引擎

| 引擎 | DDL 特性 |
|---|---|
| ClickHouse | MergeTree 引擎、ORDER BY、PARTITION BY |
| Hive | PARTITIONED BY、STORED AS ORC/Parquet |
| Apache Doris | UNIQUE KEY、DISTRIBUTED BY HASH |
| MySQL | InnoDB 引擎、COMMENT |
| PostgreSQL | 标准 DDL |
| DuckDB | 用于本地验证 |

---

## 六、建表规范管理

建表规范是 YAML 格式的配置文件，定义了数仓建表的全部约束和标准。DDL 生成引擎会自动读取并遵循这些规则。

### 6.1 规范文件结构

```yaml
version: "1.0.0"
description: "描述这套规范的用途"

naming:                    # 命名规范
  table_pattern: "{prefix}{domain}_{description}{suffix}"
  case_style: "snake_case"
  prefix_rules:            # 每层的前缀
    ODS: "ods_"
    DWD: "dwd_"
  suffix_rules:            # 分类后缀
    daily_increment: "_di"
    dimension: "_dim"
  reserved_words: ["order", "group", "select"]

data_types:                # 数据类型标准
  logical_to_physical:     # 逻辑类型 → 各引擎物理类型
    STRING:
      clickhouse: "String"
      hive: "STRING"
      mysql: "VARCHAR(255)"
    BIGINT:
      clickhouse: "Int64"
      hive: "BIGINT"
      mysql: "BIGINT"
  preferred_types:         # 特定场景推荐类型
    id_column: "BIGINT"
    amount: "DECIMAL(18, 2)"
  forbidden_types: ["TEXT", "BLOB"]

partition:                 # 分区策略
  default_partition_column: "dt"
  partition_by_layer:
    ODS: "dt"
    DWS: "stat_date"
  retention_days_by_layer:
    ODS: 90
    DWD: 365

comments:                  # 注释要求
  table_comment_required: true
  column_comment_required: true
  column_comment_min_length: 3

quality:                   # 质量约束
  primary_key_required: true
  not_null_columns: ["*_id", "*_key", "dt"]
  check_constraints:
    - column_pattern: "amount"
      rule: ">= 0"

storage:                   # 存储格式
  default_format_by_engine:
    clickhouse: "MergeTree"
    hive: "ORC"
  compression_by_engine:
    hive: "SNAPPY"
```

### 6.2 操作方式

**下载规范模板：**

```bash
curl -O http://localhost:8000/api/v1/ddl/convention/template
```

**验证规范文件：**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/convention/validate \
  -H "Content-Type: application/json" \
  -d '{"convention_path": "conventions/default_convention.yaml"}'
```

**检查表是否符合规范：**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/convention/check-table \
  -H "Content-Type: application/json" \
  -d '{
    "table_schema": {
      "table_name": "ods_trade_orders",
      "columns": [
        {"name": "order_id", "data_type": "BIGINT", "comment": "订单ID"},
        {"name": "amt", "data_type": "FLOAT", "comment": "金额"}
      ]
    },
    "convention_path": "conventions/default_convention.yaml",
    "target_engine": "clickhouse"
  }'
```

返回包含合规分数（0-100）和详细违规项：

```json
{
  "is_valid": false,
  "score": 65,
  "violations": [
    {"severity": "error", "rule": "naming.prefix", "message": "表名缺少 ODS 层前缀 'ods_'", "location": "ods_trade_orders", "suggestion": "重命名为 ods_trade_orders_di"},
    {"severity": "warning", "rule": "data_types.preferred", "message": "金额字段建议使用 DECIMAL(18,2) 而非 FLOAT", "location": "amt", "suggestion": "改为 DECIMAL(18,2)"}
  ]
}
```

---

## 七、DuckDB 本地验证沙箱

DuckDB 是嵌入式 OLAP 数据库（类似 SQLite 但面向分析），不需要安装服务端。沙箱用于在部署到生产环境之前，在本地验证生成的 DDL 和计算 SQL。

### 7.1 验证能力

| 能力 | 说明 |
|---|---|
| DDL 语法验证 | 执行 CREATE TABLE，检查语法正确性 |
| 方言自动转换 | ClickHouse/Hive/MySQL DDL 自动翻译成 DuckDB 可执行语法 |
| 计算 SQL 验证 | 执行 INSERT INTO ... SELECT，检查逻辑正确性 |
| 模拟数据生成 | 根据列类型自动生成样本数据（整数、小数、日期、字符串等） |
| 端到端流水线验证 | ODS → DWD → DWS → ADS 全链路跑通 |
| 结果预览 | 查看计算 SQL 的输出数据 |

### 7.2 通过 API 使用

**验证单条 DDL：**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/verify \
  -H "Content-Type: application/json" \
  -d '{
    "ddl_statements": [
      "CREATE TABLE ods_trade_orders (id BIGINT, amount DECIMAL(10,2), dt VARCHAR) PARTITIONED BY (dt)",
      "CREATE TABLE dwd_trade_orders (order_sk BIGINT, id BIGINT, amount DECIMAL(10,2), dt VARCHAR)"
    ]
  }'
```

**验证完整流水线（DDL + 计算 SQL）：**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/verify \
  -H "Content-Type: application/json" \
  -d '{
    "ddl_statements": [
      "CREATE TABLE source_orders (id INTEGER, user_id INTEGER, amount DOUBLE, status VARCHAR, created_at TIMESTAMP)",
      "CREATE TABLE ods_orders (id INTEGER, user_id INTEGER, amount DOUBLE, status VARCHAR, created_at TIMESTAMP, etl_time TIMESTAMP, dt VARCHAR)"
    ],
    "computation_sql": [
      "INSERT INTO ods_orders SELECT *, NOW() AS etl_time, '2026-06-06' AS dt FROM source_orders"
    ],
    "sample_data": {
      "source_orders": [
        {"id": 1, "user_id": 100, "amount": 299.5, "status": "paid", "created_at": "2026-06-01 10:00:00"},
        {"id": 2, "user_id": 101, "amount": 150.0, "status": "pending", "created_at": "2026-06-02 14:30:00"}
      ]
    }
  }'
```

---

## 八、AI 智能建模

利用 LLM 能力辅助数据仓库建模，支持维度模型设计、模型审查、分区建议。

### 8.1 功能列表

| 功能 | 端点 | 说明 |
|---|---|---|
| 建模建议 | `POST /api/v1/modeling/suggest` | 根据业务描述和源表给出建模方案 |
| 维度模型设计 | `POST /api/v1/modeling/dimensional` | 生成星型/雪花模型的事实表和维度表 |
| 模型审查 | `POST /api/v1/modeling/review` | 对已有模型评分并提出改进建议 |
| 分区建议 | `POST /api/v1/modeling/partition` | 根据查询模式推荐分区策略 |

### 8.2 操作示例

**设计维度模型：**

```bash
curl -X POST http://localhost:8000/api/v1/modeling/dimensional \
  -H "Content-Type: application/json" \
  -d '{
    "tables": [
      {"table_name": "orders", "columns": [
        {"name": "id", "data_type": "BIGINT"},
        {"name": "user_id", "data_type": "BIGINT"},
        {"name": "product_id", "data_type": "BIGINT"},
        {"name": "amount", "data_type": "DECIMAL(10,2)"},
        {"name": "quantity", "data_type": "INT"},
        {"name": "order_date", "data_type": "DATE"}
      ]}
    ],
    "business_process": "电商订单履约",
    "grain": "每个订单行项",
    "preferred_schema": "star",
    "target_platform": "clickhouse"
  }'
```

返回会包含：事实表定义（grain、measures、FK）、维度表定义（attributes、hierarchies）、设计理由和注意事项。

**审查已有模型：**

```bash
curl -X POST http://localhost:8000/api/v1/modeling/review \
  -H "Content-Type: application/json" \
  -d '{
    "tables": [{"table_name": "dwd_trade_orders_di", "columns": ["..."]}],
    "warehouse_layer": "DWD",
    "standards": "OneData methodology"
  }'
```

返回包含 0-100 的评分和按严重程度（error/warning/info）分类的发现。

---

## 九、SQL 生成与优化

AI 驱动的 SQL 工具集，支持自然语言生成、解释、优化、方言翻译和执行。

### 9.1 功能列表

| 功能 | 端点 | 说明 |
|---|---|---|
| 自然语言→SQL | `POST /api/v1/sql/generate` | 用中文描述需求，自动生成 SQL |
| SQL 解释 | `POST /api/v1/sql/explain` | 逐步解释 SQL 的执行逻辑 |
| SQL 优化 | `POST /api/v1/sql/optimize` | 性能/可读性/成本维度的优化建议 |
| 方言翻译 | `POST /api/v1/sql/translate` | 在不同数据库方言间转换 SQL |
| SQL 执行 | `POST /api/v1/sql/execute` | 在指定连接上执行 SQL |

### 9.2 操作示例

**自然语言生成 SQL：**

```bash
curl -X POST http://localhost:8000/api/v1/sql/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "查询2026年每月的总销售额，按月份排序",
    "dialect": "clickhouse",
    "schema_context": "CREATE TABLE orders (id Int64, amount Decimal(10,2), created_at DateTime)",
    "connection_id": "conn_001",
    "max_results": 2
  }'
```

返回：

```json
{
  "sql": "SELECT toStartOfMonth(created_at) AS month, SUM(amount) AS total_sales FROM orders WHERE toYear(created_at) = 2026 GROUP BY month ORDER BY month",
  "explanation": "使用 toStartOfMonth 截断到月粒度，SUM 聚合金额...",
  "confidence": 0.95,
  "warnings": []
}
```

**SQL 方言翻译（ClickHouse → Hive）：**

```bash
curl -X POST http://localhost:8000/api/v1/sql/translate \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT toStartOfMonth(created_at), sum(amount) FROM orders GROUP BY 1",
    "source_dialect": "clickhouse",
    "target_dialect": "hive"
  }'
```

**在连接上执行 SQL：**

```bash
curl -X POST http://localhost:8000/api/v1/sql/execute \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT COUNT(*) AS cnt FROM orders WHERE created_at > '\''2026-01-01'\''",
    "connection_id": "conn_001",
    "database": "trade_db",
    "max_rows": 1000,
    "timeout_seconds": 30
  }'
```

---

## 十、数据血缘追踪

自动解析 SQL 语句提取数据流向，支持表级和字段级血缘，以及变更影响分析。

### 10.1 功能列表

| 功能 | 端点 | 说明 |
|---|---|---|
| 表级血缘 | `GET /api/v1/lineage/table/{table_id}` | 获取表的上游/下游依赖图 |
| 字段级血缘 | `GET /api/v1/lineage/column/{table_id}/{column}` | 追踪字段的数据流向 |
| SQL 血缘分析 | `POST /api/v1/lineage/analyze` | 从 SQL 语句中提取血缘关系 |
| 影响分析 | `GET /api/v1/lineage/impact/{table_id}` | 评估变更下游影响范围和风险 |

### 10.2 操作示例

**从 SQL 分析血缘：**

```bash
curl -X POST http://localhost:8000/api/v1/lineage/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "INSERT INTO dwd_trade_orders SELECT a.id, a.amount, b.user_name FROM ods_orders a JOIN ods_users b ON a.user_id = b.id",
    "dialect": "hive"
  }'
```

返回：

```json
{
  "source_tables": ["ods_orders", "ods_users"],
  "target_tables": ["dwd_trade_orders"],
  "edges": [
    {"source": "ods_orders", "target": "dwd_trade_orders", "type": "DATA_FLOW"},
    {"source": "ods_users", "target": "dwd_trade_orders", "type": "DATA_FLOW"}
  ],
  "column_mappings": [
    {"source_table": "ods_orders", "source_column": "id", "target_table": "dwd_trade_orders", "target_column": "id"},
    {"source_table": "ods_orders", "source_column": "amount", "target_table": "dwd_trade_orders", "target_column": "amount"},
    {"source_table": "ods_users", "source_column": "user_name", "target_table": "dwd_trade_orders", "target_column": "user_name"}
  ]
}
```

**影响分析：**

```bash
curl "http://localhost:8000/api/v1/lineage/impact/ods_orders?connection_id=conn_001"
```

返回包含直接下游、间接下游数量、受影响的流水线、风险等级（low/medium/high/critical）和建议。

---

## 十一、ETL 流水线编排

管理 ETL 任务管道，支持任务依赖图、调度配置、Airflow DAG 代码生成。

### 11.1 支持的任务类型

| 任务类型 | 说明 |
|---|---|
| `extract` | 从源数据库抽取数据 |
| `transform` | 数据清洗/转换 |
| `load` | 加载到目标表 |
| `sql_execute` | 执行 SQL 语句 |
| `data_quality_check` | 数据质量检查 |
| `custom_python` | 自定义 Python 脚本 |

### 11.2 操作示例

**创建 ETL 流水线：**

```bash
curl -X POST http://localhost:8000/api/v1/etl/pipelines \
  -H "Content-Type: application/json" \
  -d '{
    "name": "交易数据日同步",
    "description": "每日凌晨2点同步交易数据到数仓",
    "source_connection_id": "conn_mysql_prod",
    "target_connection_id": "conn_clickhouse_dw",
    "schedule_type": "cron",
    "schedule_expression": "0 2 * * *",
    "tasks": [
      {
        "name": "抽取订单数据",
        "task_type": "extract",
        "source_query": "SELECT * FROM orders WHERE created_at >= DATE_SUB(NOW(), INTERVAL 1 DAY)",
        "target_table": "ods_trade_orders_di"
      },
      {
        "name": "清洗去重",
        "task_type": "sql_execute",
        "source_query": "INSERT INTO dwd_trade_orders_di SELECT ... FROM ods_trade_orders_di WHERE dt = '\''${bizdate}'\''",
        "depends_on": ["抽取订单数据"]
      }
    ],
    "tags": ["trade", "daily"]
  }'
```

**生成 Airflow DAG：**

```bash
curl -X POST http://localhost:8000/api/v1/etl/pipelines/{pipeline_id}/generate-dag
```

返回可直接部署到 Airflow 的 Python DAG 代码。

**验证流水线：**

```bash
curl -X POST http://localhost:8000/api/v1/etl/pipelines/{pipeline_id}/validate
```

检查连接是否存在、任务依赖图是否有环、SQL 语法是否正确、调度配置是否合法。

---

## 十二、数仓分层管理

按 ODS/DWD/DWS/ADS 四层架构管理表，支持 AI 辅助分层设计。

### 12.1 分层说明

| 层级 | 中文 | 职责 | 数据来源 |
|---|---|---|---|
| **ODS** | 贴源层 | 原始数据 1:1 同步 | 业务数据库 |
| **DWD** | 明细层 | 清洗、标准化、去重 | ODS |
| **DWS** | 汇总层 | 按维度预聚合 | DWD |
| **ADS** | 应用层 | 面向业务报表 | DWS / DWD |
| **DIM** | 维度层 | 维度表统一管理 | 各层 |

### 12.2 操作示例

**在指定层创建表：**

```bash
curl -X POST http://localhost:8000/api/v1/warehouse/layers/DWD/tables \
  -H "Content-Type: application/json" \
  -d '{
    "table_name": "dwd_trade_orders_di",
    "connection_id": "conn_clickhouse",
    "database": "dw",
    "columns": [
      {"name": "order_sk", "data_type": "BIGINT", "comment": "代理键"},
      {"name": "order_id", "data_type": "BIGINT", "comment": "业务主键"},
      {"name": "amount", "data_type": "DECIMAL(18,2)", "comment": "金额"}
    ],
    "partition_by": "dt",
    "table_comment": "交易订单明细表"
  }'
```

**AI 辅助分层设计：**

```bash
curl -X POST http://localhost:8000/api/v1/warehouse/design \
  -H "Content-Type: application/json" \
  -d '{
    "source_connection_id": "conn_mysql",
    "source_database": "trade_db",
    "source_tables": ["orders", "users", "products"],
    "target_layer": "DWS",
    "business_domain": "电商交易",
    "requirements": "需要按日/周/月统计销售额、订单量、客单价"
  }'
```

---

## 十三、LLM 模型配置

平台支持多种 LLM 提供商，通过 `.env` 文件配置切换。

### 13.1 支持的模型提供商

| 提供商 | 配置值 | 说明 | 是否需要 API Key |
|---|---|---|---|
| **OpenAI** | `openai` | GPT-4o / GPT-4o-mini | 是 |
| **Azure OpenAI** | `azure_openai` | 企业级 Azure 部署 | 是 |
| **Ollama** | `ollama` | 本地模型推理，推荐 qwen2.5:14b | 否 |
| **通义千问** | `tongyi` | 阿里云大模型 | 是 |
| **DeepSeek** | `deepseek` | OpenAI 兼容接口 | 是 |

### 13.2 配置示例

**使用 Ollama（本地，推荐）：**

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
OLLAMA_TEMPERATURE=0.1
```

**使用 OpenAI：**

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o
```

**使用通义千问：**

```env
LLM_PROVIDER=tongyi
# 在 provider extra 中传入 DASHSCOPE_API_KEY
```

**使用 DeepSeek：**

```env
LLM_PROVIDER=deepseek
OPENAI_API_KEY=your-deepseek-key
OPENAI_API_BASE=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

---

## 十四、完整配置参考

所有配置项通过 `.env` 文件或环境变量设置（环境变量前缀为 `DATAFORGE_`）。

### 14.1 应用基础配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `APP_NAME` | DataForge AI | 应用名称 |
| `APP_DEBUG` | false | 调试模式 |
| `APP_HOST` | 0.0.0.0 | 监听地址 |
| `APP_PORT` | 8000 | 监听端口 |
| `APP_LOG_LEVEL` | INFO | 日志级别 |

### 14.2 元数据库配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | postgresql+asyncpg://... | 内部元数据库连接 |
| `DATABASE_POOL_SIZE` | 20 | 连接池大小 |
| `DATABASE_MAX_OVERFLOW` | 10 | 连接池溢出上限 |

### 14.3 LLM 配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | openai | LLM 提供商 |
| `OPENAI_API_KEY` | - | OpenAI API Key |
| `OPENAI_MODEL` | gpt-4o | 默认模型 |
| `OPENAI_TEMPERATURE` | 0.1 | 采样温度 |
| `OLLAMA_BASE_URL` | http://localhost:11434 | Ollama 地址 |
| `OLLAMA_MODEL` | qwen2.5:14b | Ollama 模型 |
| `OLLAMA_REQUEST_TIMEOUT` | 300 | Ollama 超时(秒) |

### 14.4 DuckDB 沙箱配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DUCKDB_ENABLED` | true | 启用沙箱 |
| `DUCKDB_DATABASE_PATH` | :memory: | 数据库路径 |
| `DUCKDB_VERIFY_SAMPLE_ROWS` | 100 | 验证用样本行数 |
| `DUCKDB_MEMORY_LIMIT_MB` | 512 | 内存上限(MB) |

### 14.5 规范文件配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `CONVENTION_FILE_PATH` | - | 默认建表规范文件路径 |

---

## 十五、API 端点速查表

### 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc 文档 |

### 数据源连接 `/api/v1/connections`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/` | 创建连接 |
| GET | `/` | 列出连接 |
| GET | `/{id}` | 连接详情 |
| PUT | `/{id}` | 更新连接 |
| DELETE | `/{id}` | 删除连接 |
| POST | `/{id}/test` | 测试连通性 |
| GET | `/{id}/databases` | 列出数据库 |
| GET | `/{id}/tables` | 列出表 |
| GET | `/{id}/tables/{table}/columns` | 获取列信息 |

### DDL 生成 `/api/v1/ddl`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/build` | DDL 生成流水线 |
| POST | `/verify` | DuckDB 沙箱验证 |
| POST | `/convention/validate` | 验证规范文件 |
| GET | `/convention/template` | 下载规范模板 |
| POST | `/convention/check-table` | 检查表合规性 |

### AI 建模 `/api/v1/modeling`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/suggest` | 建模建议 |
| POST | `/dimensional` | 维度模型设计 |
| POST | `/review` | 模型审查 |
| POST | `/partition` | 分区建议 |

### SQL 引擎 `/api/v1/sql`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/generate` | 自然语言生成 SQL |
| POST | `/explain` | SQL 解释 |
| POST | `/optimize` | SQL 优化 |
| POST | `/translate` | 方言翻译 |
| POST | `/execute` | 执行 SQL |

### 数据血缘 `/api/v1/lineage`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/table/{id}` | 表级血缘图 |
| GET | `/column/{id}/{col}` | 字段级血缘 |
| POST | `/analyze` | SQL 血缘分析 |
| GET | `/impact/{id}` | 影响分析 |

### ETL 流水线 `/api/v1/etl`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/pipelines` | 创建流水线 |
| GET | `/pipelines` | 列出流水线 |
| GET | `/pipelines/{id}` | 流水线详情 |
| POST | `/pipelines/{id}/generate-dag` | 生成 Airflow DAG |
| POST | `/pipelines/{id}/validate` | 验证流水线 |

### 数仓分层 `/api/v1/warehouse`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/layers/{layer}/tables` | 在层中创建表 |
| GET | `/layers/{layer}/tables` | 列出层中的表 |
| POST | `/design` | AI 辅助分层设计 |
| GET | `/lineage/{table}` | 表血缘 |
| POST | `/migration` | 生成迁移脚本 |
