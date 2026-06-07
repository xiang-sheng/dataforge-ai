# DataForge AI 功能文档

> 版本 0.2.0 · 最后更新 2026-06-07

---

## 目录

- [一、平台概述](#一平台概述)
- [二、核心工作流](#二核心工作流)
- [三、功能模块一：智能问数（SQLAgent）](#三功能模块一智能问数sqlagent)
- [四、功能模块二：固化建表（数据验证后落地）](#四功能模块二固化建表数据验证后落地)
- [五、功能模块三：ETL 建表（DDLAgent + DDLAutoBuilder）](#五功能模块三etl-建表ddlagent--ddlautobuilder)
- [六、功能模块四：建表规范管理](#六功能模块四建表规范管理)
- [七、功能模块五：DuckDB 本地验证沙箱](#七功能模块五duckdb-本地验证沙箱)
- [八、功能模块六：数据源连接管理](#八功能模块六数据源连接管理)
- [九、功能模块七：AI 数据建模辅助](#九功能模块七ai-数据建模辅助)
- [十、功能模块八：SQL 生成与优化](#十功能模块八sql-生成与优化)
- [十一、功能模块九：数据血缘追踪](#十一功能模块九数据血缘追踪)
- [十二、功能模块十：ETL 管道编排](#十二功能模块十etl-管道编排)
- [十三、本地快速体验（demo.py）](#十三本地快速体验demopy)
- [十四、LLM 模型配置](#十四llm-模型配置)
- [十五、完整配置参考](#十五完整配置参考)
- [十六、API 端点速查表](#十六api-端点速查表)
- [十七、项目结构](#十七项目结构)

---

## 一、平台概述

DataForge AI 是一款 **AI 驱动的数仓建设辅助平台**，面向数据工程师和数据架构师，将大语言模型的能力与企业数仓开发全流程深度融合。

### 1.1 核心能力

| 能力 | 说明 |
|---|---|
| **智能问数** | 用自然语言描述数据需求 → AI 自动生成 SQL → 执行验证 → 确认数据正确后固化为物理表 |
| **ETL 建表** | 从源表元数据 + 建表规范 → 自动生成数仓 DDL → DuckDB 沙箱验证 → 部署到生产环境 |

### 1.2 支持的数据库引擎

平台支持 **7 种外部数据库** + **1 种本地验证引擎**：

| 数据库 | 典型场景 | 驱动协议 |
|---|---|---|
| MySQL | OLTP 业务数据源 | aiomysql |
| PostgreSQL | OLTP / OLAP | asyncpg |
| ClickHouse | 高性能 OLAP 分析 | clickhouse-driver |
| Apache Doris | 实时分析数仓 | MySQL 协议兼容 |
| Hive | 离线大数据数仓 | PyHive |
| SQL Server | 企业 ERP / CRM | pymssql |
| Oracle | 传统核心系统 | oracledb |
| **DuckDB**（本地） | 嵌入式 OLAP 验证沙箱 | 内置 |

### 1.3 支持的 LLM 模型

| 提供商 | 说明 | 是否需要 API Key |
|---|---|---|
| OpenAI | GPT-4o / GPT-4o-mini | 是 |
| Azure OpenAI | 企业级 Azure 部署 | 是 |
| Ollama | 本地模型推理（推荐 qwen2.5:14b） | 否 |
| 通义千问 | 阿里云大模型 | 是 |
| DeepSeek | OpenAI 兼容接口 | 是 |

### 1.4 技术栈

```
Python 3.11+ / FastAPI / LangChain / DuckDB / SQLAlchemy 2.0 async
```

- **FastAPI**：异步 Web 框架，提供高性能 REST API
- **LangChain**：Agent 工具调用框架，驱动 SQLAgent 和 DDLAgent 的 ReAct 循环
- **DuckDB**：嵌入式 OLAP 数据库，用于本地验证 DDL/SQL 正确性
- **SQLAlchemy 2.0 async**：异步 ORM，管理内部元数据和外部数据库连接
- **Pydantic v2**：数据校验与序列化

---

## 二、核心工作流

DataForge AI 的核心工作流由三个紧密衔接的阶段组成：

```
用户自然语言提问
    │
    ▼
┌──────────────────────────────────────────────┐
│  [智能问数] SQLAgent                          │
│  探索表结构 → 【思考过程】→ 生成SQL            │
│  → 执行验证 → 展示数据                        │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
         用户确认数据符合需求
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  [固化建表] create_table_from_query           │
│  将查询结果持久化为物理表                      │
│  (避免重复执行相同查询，避免盲目建表)           │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  [ETL建表] DDLAgent / DDLAutoBuilder          │
│  从源表结构 + 建表规范                         │
│  → 自动生成数仓 DDL                           │
│  → DuckDB 沙箱验证                            │
│  → 生成计算 SQL (INSERT INTO...SELECT)        │
└──────────────────────────────────────────────┘
```

**设计理念**：先问再建。用户先用自然语言探索数据、验证查询逻辑，确认数据符合业务需求后再将结果固化为物理表。这避免了传统方式中「先建一堆表、再验证数据对不对」导致的表膨胀和重复建设问题。

---

## 三、功能模块一：智能问数（SQLAgent）

### 3.1 用途

用自然语言描述数据需求，AI 自动生成 SQL 并执行验证。这是平台最核心的交互能力。

### 3.2 核心设计

SQLAgent 采用 **ReAct（Reasoning + Acting）** 模式，基于 LangChain 的 tool-calling 协议实现。其核心设计原则是：**强制输出思考过程**。

在生成任何 SQL 之前，模型必须先输出结构化的分析思路：

```
【思考过程】
1. 需求理解：用户想要什么数据？
2. 数据来源：需要哪些表？哪些字段？
3. 关联关系：表之间怎么 JOIN？关联条件是什么？
4. 筛选条件：需要 WHERE 过滤什么？时间范围？
5. 聚合逻辑：需要 GROUP BY 什么？用什么聚合函数？
6. 排序展示：结果怎么排序？
```

这一设计显著提升了模型（尤其是本地小模型）的 SQL 生成准确率。

### 3.3 七个 LangChain 工具

SQLAgent 在执行过程中可自主调用以下 7 个工具：

| 工具 | 参数 | 用途 |
|---|---|---|
| `list_tables` | 无 | 列出所有表名和行数 |
| `describe_table` | `table_name` | 查看表的字段详情（名称、类型、可空、默认值） |
| `get_sample_data` | `table_name, limit=5` | 查看样本数据，了解实际数据内容和格式 |
| `execute_query` | `sql` | 执行 SELECT 查询，返回结果（最多 50 行） |
| `execute_ddl` | `ddl` | 执行 CREATE TABLE / DROP TABLE 等 DDL |
| `create_table_from_query` | `table_name, select_sql, table_comment` | 将查询结果固化为持久化表 |
| `read_convention` | 无 | 读取建表规范（固化建表时需要参考命名规范） |

所有工具定义在 `src/warehouse/tools.py` 中，通过 `init_tool_context()` 绑定 DuckDB 连接。

### 3.4 返回结果结构

```python
@dataclass
class AnalysisResult:
    question: str                    # 用户的原始问题
    sql: Optional[str]               # 生成的 SQL
    query_result: Optional[str]      # 查询结果
    reasoning: Optional[str]         # 【思考过程】内容
    materialized_table: Optional[str]# 如果固化了，新表名
    tool_calls_log: list[dict]       # 工具调用日志 [{step, tool, args}]
    success: bool                    # 是否成功
    error: Optional[str]             # 错误信息
```

### 3.5 操作示例

**Python 调用：**

```python
import duckdb
from langchain_community.chat_models import ChatOllama
from src.warehouse.sql_agent import SQLAgent

# 初始化 LLM
llm = ChatOllama(
    model="qwen2.5:14b",
    base_url="http://localhost:11434",
    temperature=0.1,
    request_timeout=300,
)

# 连接数据库（DuckDB 或已有的业务库）
conn = duckdb.connect("my_database.duckdb")

# 创建 Agent
agent = SQLAgent(
    llm=llm,
    db=conn,
    convention_file="conventions/default_convention.yaml",
)

# 提问
result = agent.analyze("查2025年6月每个商品的购买数量和总金额，按金额降序排列")

# 查看结果
print(result.reasoning)       # 思考过程
print(result.sql)             # 生成的 SQL
print(result.tool_calls_log)  # 工具调用记录
```

**通过 demo.py 体验：**

```bash
# 前置条件
ollama serve && ollama pull qwen2.5:14b

# 安装依赖
pip install langchain langchain-community langchain-core duckdb pyyaml

# 运行
python demo.py
```

---

## 四、功能模块二：固化建表（数据验证后落地）

### 4.1 用途

当智能问数的查询被反复使用时，将结果固化为持久表，后续直接查询即可，无需重复计算。

### 4.2 流程

```
用户看到查询结果 → 确认数据正确 → 调用 create_table_from_query → 自动建表
```

优势：
- **避免盲目建表**：先看到数据，再决定是否建表，避免表膨胀
- **数据驱动**：基于实际查询结果建表，而非基于猜测
- **命名规范**：固化时自动读取建表规范，确保符合标准

### 4.3 底层实现

`create_table_from_query` 工具执行 `CREATE TABLE AS SELECT`，自动完成：
1. `DROP TABLE IF EXISTS` 清理旧表
2. `CREATE TABLE "table_name" AS {select_sql}` 建表
3. `COMMENT ON TABLE` 添加表注释
4. 统计行数和字段信息并返回

### 4.4 操作示例

**Python 调用（Agent 工具方式）：**

```python
from src.warehouse.tools import create_table_from_query

result = create_table_from_query.invoke({
    "table_name": "ads_monthly_product_sales",
    "select_sql": """
        SELECT oi.product_name,
               SUM(oi.quantity) AS total_qty,
               CAST(SUM(oi.subtotal) AS DECIMAL(18,2)) AS total_amount
        FROM orders o
        JOIN order_items oi ON o.id = oi.order_id
        WHERE strftime(o.order_time, '%Y-%m') = '2025-06'
        GROUP BY oi.product_name
        ORDER BY total_amount DESC
    """,
    "table_comment": "月度商品销售统计（购买数量+金额）",
})
print(result)
# 表 'ads_monthly_product_sales' 创建成功！
#   行数: 12
#   字段: 3 个
#   说明: 月度商品销售统计（购买数量+金额）
```

**在 SQLAgent 分析流程中自动触发：**

当 Agent 发现某个查询被反复使用时，会主动建议调用 `read_convention` 读取规范，然后调用 `create_table_from_query` 固化。

---

## 五、功能模块三：ETL 建表（DDLAgent + DDLAutoBuilder）

ETL 建表是平台另一核心能力，提供两种模式：**AI 驱动（DDLAgent）** 和 **规则引擎（DDLAutoBuilder）**。

### 5.1 DDLAgent（AI 驱动）

#### 概述

DDLAgent 是基于 LangChain ReAct 模式的 AI Agent。LLM 通过 tool-calling 自主决定探索顺序，完成从源表到目标 DDL 的全流程。

#### 工作流程

```
LLM 自主决策
    │
    ├─ describe_source_table(table_name)  → 探索源表字段结构
    ├─ get_sample_data(table_name)        → 查看样本数据
    ├─ read_convention                    → 读取建表规范
    ├─ ddl_verify(ddl)                    → DuckDB 沙箱验证
    ├─ list_source_tables / list_target_tables → 查看已有表
    └─ query_target(sql)                  → 查询目标库
    │
    ▼
最终输出 ```sql ... ``` 代码块中的 DDL
```

#### 支持数仓分层

| 层级 | 设计要点 |
|---|---|
| **ODS** | 与源表结构对齐，追加 `etl_time`(TIMESTAMP)、`source_system`(VARCHAR) |
| **DWD** | 清洗去重（ROW_NUMBER）、维度退化（JOIN 维度字段）、追加 etl_time |
| **DWS** | 按业务维度 GROUP BY 聚合（SUM/COUNT/AVG/MAX/MIN）、追加 etl_time |
| **ADS** | 面向业务场景的筛选和宽表 |

#### 操作示例

```python
import duckdb
from langchain_community.chat_models import ChatOllama
from src.warehouse.ddl_agent import DDLAgent

llm = ChatOllama(model="qwen2.5:14b", temperature=0.1)
conn = duckdb.connect("my_database.duckdb")

agent = DDLAgent(
    llm=llm,
    db=conn,
    convention_file="conventions/default_convention.yaml",
)

result = agent.build(
    source_table="order_items",
    target_layer="DWS",
    business_desc="商品销售日汇总表，按日期+商品维度聚合",
)

print(result.ddl)                # 生成的 DDL
print(result.verification)       # 验证结果
print(result.tool_calls_log)     # 工具调用日志
```

#### 返回结果结构

```python
@dataclass
class DDLAgentResult:
    source_table: str                  # 源表名
    target_layer: str                  # 目标层级
    ddl: Optional[str]                 # 生成的 DDL
    verification: Optional[str]        # DuckDB 验证结果
    tool_calls_log: list[dict]         # 工具调用日志
    success: bool                      # 是否成功
    error: Optional[str]               # 错误信息
```

### 5.2 DDLAutoBuilder（规则引擎）

#### 概述

DDLAutoBuilder 是基于规则的批量 DDL 生成引擎，不依赖 LLM，适用于大规模批量建表场景。它从源表元数据出发，结合建表规范自动生成 DDL 和计算 SQL。

#### DDLPipelineConfig 配置项

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `source_connection_id` | str | （必填） | 源数据库连接 ID |
| `source_tables` | List[str] | `[]` | 指定处理的表名列表，空列表表示处理所有表 |
| `target_layer` | str | `"ODS"` | 目标层级：ODS / DWD / DWS / ADS |
| `target_db_type` | str | `"clickhouse"` | 目标引擎：clickhouse / hive / doris / mysql / postgresql / duckdb |
| `convention_path` | Optional[str] | `None` | 建表规范文件路径，None 使用内置默认 |
| `naming_overrides` | Dict[str, str] | `{}` | 手动命名覆盖（源表名 → 目标表名） |
| `include_computation_sql` | bool | `True` | 是否生成 INSERT INTO...SELECT 计算 SQL |
| `local_verify` | bool | `True` | 是否在 DuckDB 沙箱中验证 |
| `sample_rows_for_verify` | int | `100` | 验证用样本行数（1 ~ 10000） |
| `enable_ai` | bool | `False` | 是否启用 AI 增强（LLM 审查和优化建议） |

#### 数据类型映射

DDLAutoBuilder 内置完整的逻辑类型到物理类型映射（支持 6 种引擎）：

| 逻辑类型 | ClickHouse | Hive | Doris | MySQL | PostgreSQL | DuckDB |
|---|---|---|---|---|---|---|
| STRING | String | STRING | VARCHAR(65533) | VARCHAR(255) | TEXT | VARCHAR |
| INTEGER | Int32 | INT | INT | INT | INTEGER | INTEGER |
| BIGINT | Int64 | BIGINT | BIGINT | BIGINT | BIGINT | BIGINT |
| DECIMAL | Decimal(18,2) | DECIMAL(18,2) | DECIMAL(18,2) | DECIMAL(18,2) | NUMERIC(18,2) | DECIMAL(18,2) |
| BOOLEAN | UInt8 | BOOLEAN | BOOLEAN | TINYINT(1) | BOOLEAN | BOOLEAN |
| DATE | Date | DATE | DATE | DATE | DATE | DATE |
| TIMESTAMP | DateTime | TIMESTAMP | DATETIME | DATETIME | TIMESTAMP | TIMESTAMP |
| JSON | String | STRING | JSON | JSON | JSONB | VARCHAR |

#### 各层计算 SQL 生成规则

| 层级 | SQL 模式 | 说明 |
|---|---|---|
| **ODS** | `INSERT INTO ods_xxx SELECT *, NOW() AS etl_time FROM source` | 原样同步 + ETL 时间戳 |
| **DWD** | `INSERT INTO dwd_xxx SELECT ROW_NUMBER() OVER(PARTITION BY pk ORDER BY ...) ...` | 去重 + 清洗 + 分区过滤 |
| **DWS** | `INSERT INTO dws_xxx SELECT dim1, SUM(amount), COUNT(*) FROM dwd_xxx GROUP BY ...` | 维度聚合 |
| **ADS** | `INSERT INTO ads_xxx SELECT ... FROM dws_xxx WHERE ...` | 业务条件过滤 |

#### 操作示例

**通过 API 调用：**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/build \
  -H "Content-Type: application/json" \
  -d '{
    "source_connection_id": "conn_001",
    "source_tables": ["orders", "order_items", "users"],
    "target_layer": "DWD",
    "target_db_type": "clickhouse",
    "convention_path": "conventions/default_convention.yaml",
    "include_computation_sql": true,
    "local_verify": true,
    "sample_rows_for_verify": 100,
    "ai_enhance": false
  }'
```

**直接提供 Schema（无需连接源库）：**

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

**Python 代码调用：**

```python
from src.warehouse.ddl_auto_builder import DDLAutoBuilder, DDLPipelineConfig

config = DDLPipelineConfig(
    source_connection_id="conn_001",
    source_tables=["orders", "order_items"],
    target_layer="DWS",
    target_db_type="clickhouse",
    convention_path="conventions/default_convention.yaml",
    include_computation_sql=True,
    local_verify=True,
    enable_ai=True,  # 启用 AI 增强审查
)

builder = DDLAutoBuilder(config)
result = builder.run()

for table in result.tables:
    print(f"源表: {table.source_table} → 目标表: {table.target_table}")
    print(table.ddl)
    if table.computation_sql:
        print(table.computation_sql)
```

#### 返回结果结构

```json
{
  "config": { "..." },
  "tables": [
    {
      "source_table": "orders",
      "target_table": "ods_trade_orders_di",
      "target_layer": "ODS",
      "ddl": "CREATE TABLE IF NOT EXISTS ods_trade_orders_di (\n  id BIGINT COMMENT '订单ID',\n  ...\n);",
      "computation_sql": "INSERT OVERWRITE TABLE ods_trade_orders_di ...\nSELECT ... FROM trade_db.orders;",
      "convention_violations": [],
      "verify_result": {
        "ddl_success": true,
        "sql_success": true,
        "rows_generated": 100,
        "sample_preview": [{"id": 1, "user_id": 42, "amount": 199.50}]
      },
      "column_mappings": [
        {"source_column": "id", "target_column": "id", "source_type": "BIGINT", "target_type": "BIGINT"}
      ],
      "ai_enhance_result": null
    }
  ],
  "total_tables": 3,
  "succeeded": 3,
  "failed": 0
}
```

---

## 六、功能模块四：建表规范管理

### 6.1 概述

建表规范是 YAML 或 Markdown 格式的配置文件，定义了数仓建表的全部约束和标准。DDLAgent 和 DDLAutoBuilder 在生成 DDL 时都会自动读取并遵循这些规则。

### 6.2 规范内容

| 模块 | 说明 |
|---|---|
| **命名规则** | 表名前缀/后缀、snake_case 风格、保留字检查 |
| **数据类型映射** | 逻辑类型到 6 种引擎物理类型的映射表 |
| **分区策略** | 默认分区列、按层分区、保留天数 |
| **注释要求** | 表注释必填、列注释必填、最小长度 |
| **质量约束** | 主键必填、NOT NULL 列模式、CHECK 约束 |
| **存储格式** | 各引擎默认存储格式和压缩方式 |

### 6.3 ConventionLoader 和 ConventionValidator

- **ConventionLoader**：解析 YAML/Markdown 规范文件，提供统一的访问接口
- **ConventionValidator**：对已有表结构进行合规检查，返回合规分数和违规明细

### 6.4 规范文件示例（conventions/default_convention.yaml）

```yaml
version: "1.0.0"
description: "DataForge AI 默认建表规范"

naming:
  table_pattern: "{prefix}{domain}_{description}{suffix}"
  case_style: "snake_case"
  prefix_rules:
    ODS: "ods_"
    DWD: "dwd_"
    DWS: "dws_"
    ADS: "ads_"
  suffix_rules:
    daily_increment: "_di"
    dimension: "_dim"
  reserved_words: ["order", "group", "select"]

data_types:
  logical_to_physical:
    STRING:
      clickhouse: "String"
      hive: "STRING"
      mysql: "VARCHAR(255)"
      postgresql: "TEXT"
      duckdb: "VARCHAR"
    BIGINT:
      clickhouse: "Int64"
      hive: "BIGINT"
      mysql: "BIGINT"
      postgresql: "BIGINT"
      duckdb: "BIGINT"
    DECIMAL:
      clickhouse: "Decimal(18,2)"
      hive: "DECIMAL(18,2)"
      mysql: "DECIMAL(18,2)"
      postgresql: "NUMERIC(18,2)"
      duckdb: "DECIMAL(18,2)"
  preferred_types:
    id_column: "BIGINT"
    amount: "DECIMAL(18, 2)"
  forbidden_types: ["TEXT", "BLOB"]

partition:
  default_partition_column: "dt"
  partition_by_layer:
    ODS: "dt"
    DWS: "stat_date"
  retention_days_by_layer:
    ODS: 90
    DWD: 365

comments:
  table_comment_required: true
  column_comment_required: true
  column_comment_min_length: 3

quality:
  primary_key_required: true
  not_null_columns: ["*_id", "*_key", "dt"]
  check_constraints:
    - column_pattern: "amount"
      rule: ">= 0"

storage:
  default_format_by_engine:
    clickhouse: "MergeTree"
    hive: "ORC"
  compression_by_engine:
    hive: "SNAPPY"
```

### 6.5 操作示例

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
    {
      "severity": "error",
      "rule": "naming.prefix",
      "message": "表名缺少 ODS 层前缀 'ods_'",
      "location": "ods_trade_orders",
      "suggestion": "重命名为 ods_trade_orders_di"
    },
    {
      "severity": "warning",
      "rule": "data_types.preferred",
      "message": "金额字段建议使用 DECIMAL(18,2) 而非 FLOAT",
      "location": "amt",
      "suggestion": "改为 DECIMAL(18,2)"
    }
  ]
}
```

**下载规范模板：**

```bash
curl -O http://localhost:8000/api/v1/ddl/convention/template
```

---

## 七、功能模块五：DuckDB 本地验证沙箱

### 7.1 概述

DuckDB 是嵌入式 OLAP 数据库（类似 SQLite 但面向分析场景），无需安装服务端。沙箱用于在部署到生产环境之前，在本地验证生成的 DDL 和计算 SQL 的正确性。

### 7.2 验证能力

| 能力 | 说明 |
|---|---|
| **DDL 语法验证** | 执行 CREATE TABLE，检查语法正确性 |
| **方言自动转换** | ClickHouse / Hive / MySQL DDL 自动翻译成 DuckDB 可执行语法 |
| **计算 SQL 验证** | 执行 INSERT INTO ... SELECT，检查逻辑正确性 |
| **模拟数据生成** | 根据列类型自动生成样本数据（整数、小数、日期、字符串等） |
| **端到端流水线验证** | ODS → DWD → DWS → ADS 全链路跑通 |
| **导出 Parquet / CSV** | 将验证结果导出为标准格式 |

### 7.3 操作示例

**验证单条 DDL：**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/verify \
  -H "Content-Type: application/json" \
  -d '{
    "ddl_statements": [
      "CREATE TABLE ods_trade_orders (id BIGINT, amount DECIMAL(10,2), dt VARCHAR)"
    ]
  }'
```

**验证完整流水线（DDL + 计算 SQL + 样本数据）：**

```bash
curl -X POST http://localhost:8000/api/v1/ddl/verify \
  -H "Content-Type: application/json" \
  -d '{
    "ddl_statements": [
      "CREATE TABLE source_orders (id INTEGER, user_id INTEGER, amount DOUBLE, status VARCHAR, created_at TIMESTAMP)",
      "CREATE TABLE ods_orders (id INTEGER, user_id INTEGER, amount DOUBLE, status VARCHAR, created_at TIMESTAMP, etl_time TIMESTAMP, dt VARCHAR)"
    ],
    "computation_sql": [
      "INSERT INTO ods_orders SELECT *, NOW() AS etl_time, '\''2026-06-07'\'' AS dt FROM source_orders"
    ],
    "sample_data": {
      "source_orders": [
        {"id": 1, "user_id": 100, "amount": 299.5, "status": "paid", "created_at": "2026-06-01 10:00:00"},
        {"id": 2, "user_id": 101, "amount": 150.0, "status": "pending", "created_at": "2026-06-02 14:30:00"}
      ]
    }
  }'
```

**Python 代码调用（DDLAgent 中的 ddl_verify 工具）：**

```python
from src.db.duckdb_sandbox import DuckDBSandbox

sandbox = DuckDBSandbox()
result = sandbox.verify_ddl([
    "CREATE TABLE ods_orders (id BIGINT, amount DECIMAL(10,2), dt VARCHAR)"
])
print(result)
```

---

## 八、功能模块六：数据源连接管理

### 8.1 概述

管理远程和本地数据库的连接。采用 **Adapter Pattern** 统一 7 种数据库的访问接口，支持注册连接、测试连通性、浏览库表结构。

### 8.2 适配器架构

```
DatabaseAdapterFactory (src/db/factory.py)
    │
    ├── MySQLAdapter       (src/db/mysql_adapter.py)
    ├── PostgresAdapter    (src/db/postgres_adapter.py)
    ├── ClickHouseAdapter  (src/db/clickhouse_adapter.py)
    ├── DorisAdapter       (src/db/doris_adapter.py)
    ├── HiveAdapter        (src/db/hive_adapter.py)
    ├── SQLServerAdapter   (src/db/sqlserver_adapter.py)
    └── OracleAdapter      (src/db/oracle_adapter.py)
```

每个适配器实现了统一的元数据发现接口：列出数据库、列出表、获取列信息、执行 SQL。

### 8.3 操作示例

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

## 九、功能模块七：AI 数据建模辅助

### 9.1 概述

利用 LLM 辅助数据仓库建模，支持维度模型设计、模型审查和分区建议。

### 9.2 功能列表

| 功能 | 端点 | 说明 |
|---|---|---|
| 建模建议 | `POST /api/v1/modeling/suggest` | 根据业务描述和源表给出建模方案 |
| 维度模型设计 | `POST /api/v1/modeling/dimensional` | 生成星型/雪花模型的事实表和维度表 |
| 模型审查 | `POST /api/v1/modeling/review` | 对已有模型评分并提出改进建议 |
| 分区建议 | `POST /api/v1/modeling/partition` | 根据查询模式推荐分区策略 |

### 9.3 操作示例

**设计维度模型（星型模型）：**

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

返回包含事实表定义（grain、measures、FK）、维度表定义（attributes、hierarchies）、设计理由和注意事项。

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

返回包含 0-100 的评分和按严重程度（error / warning / info）分类的发现。

---

## 十、功能模块八：SQL 生成与优化

### 10.1 概述

AI 驱动的 SQL 工具集，支持自然语言生成、解释、优化、方言翻译和执行。

### 10.2 功能列表

| 功能 | 端点 | 说明 |
|---|---|---|
| 自然语言→SQL | `POST /api/v1/sql/generate` | 用中文描述需求，自动生成 SQL |
| SQL 解释 | `POST /api/v1/sql/explain` | 逐步解释 SQL 的执行逻辑 |
| SQL 优化 | `POST /api/v1/sql/optimize` | 性能/可读性/成本维度的优化建议 |
| 方言翻译 | `POST /api/v1/sql/translate` | 在不同数据库方言间转换 SQL |
| SQL 执行 | `POST /api/v1/sql/execute` | 在指定连接上执行 SQL |

### 10.3 操作示例

**自然语言生成 SQL：**

```bash
curl -X POST http://localhost:8000/api/v1/sql/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "查询2026年每月的总销售额，按月份排序",
    "dialect": "clickhouse",
    "schema_context": "CREATE TABLE orders (id Int64, amount Decimal(10,2), created_at DateTime)",
    "connection_id": "conn_001"
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

**SQL 优化：**

```bash
curl -X POST http://localhost:8000/api/v1/sql/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT * FROM orders WHERE DATE_FORMAT(created_at, '\''%Y-%m'\'') = '\''2026-06'\''",
    "dialect": "mysql",
    "optimization_goal": "performance"
  }'
```

---

## 十一、功能模块九：数据血缘追踪

### 11.1 概述

自动解析 SQL 语句提取数据流向，支持表级和字段级血缘，以及变更影响分析。

### 11.2 功能列表

| 功能 | 端点 | 说明 |
|---|---|---|
| 表级血缘 | `GET /api/v1/lineage/table/{table_id}` | 获取表的上游/下游依赖图 |
| 字段级血缘 | `GET /api/v1/lineage/column/{table_id}/{column}` | 追踪字段的数据流向 |
| SQL 血缘分析 | `POST /api/v1/lineage/analyze` | 从 SQL 语句中提取血缘关系 |
| 影响分析 | `GET /api/v1/lineage/impact/{table_id}` | 评估变更下游影响范围和风险 |

### 11.3 操作示例

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

返回包含直接下游、间接下游数量、受影响的流水线、风险等级（low / medium / high / critical）和建议。

---

## 十二、功能模块十：ETL 管道编排

### 12.1 概述

管理 ETL 任务管道，支持任务依赖图（DAG）、调度配置、Airflow DAG 代码生成和 DolphinScheduler YAML 生成。

### 12.2 支持的任务类型

| 任务类型 | 说明 |
|---|---|
| `extract` | 从源数据库抽取数据 |
| `transform` | 数据清洗/转换 |
| `load` | 加载到目标表 |
| `sql_execute` | 执行 SQL 语句 |
| `data_quality_check` | 数据质量检查 |
| `custom_python` | 自定义 Python 脚本 |

### 12.3 操作示例

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

## 十三、本地快速体验（demo.py）

### 13.1 前置条件

```bash
# 安装依赖
pip install langchain langchain-community langchain-core duckdb pyyaml

# 启动 Ollama 并拉取推荐模型
ollama serve
ollama pull qwen2.5:14b
```

### 13.2 运行

```bash
python demo.py
```

### 13.3 演示场景

demo.py 使用 **电商业务数据**（3 张表）模拟完整的智能问数 → 固化建表 → ETL 建表流程：

| 表名 | 说明 | 行数 |
|---|---|---|
| `orders` | 订单主表（用户、金额、状态、支付方式、时间） | 9 |
| `order_items` | 订单商品明细表（商品名、品类、单价、数量） | 20 |
| `users` | 用户信息表（姓名、城市、省份、VIP 等级） | 5 |

### 13.4 演示流程

**提问 #1**："查2025年6月每个商品的购买数量和总金额，按金额降序排列"

演示过程：
1. Agent 调用 `list_tables` 了解有哪些表
2. Agent 调用 `describe_table` 查看 orders 和 order_items 的字段结构
3. Agent 输出【思考过程】
4. Agent 生成 SQL 并调用 `execute_query` 验证
5. 用户确认数据正确 → 调用 `create_table_from_query` 固化为 `ads_monthly_product_sales` 表

**提问 #2**："统计各省份VIP用户（vip_level>=3）在6月的消费总额，找出高价值区域"

演示过程：
- 多表关联（users + orders + order_items）+ 条件筛选 + 分组聚合

**ETL 建表**：DDLAgent 为 `order_items` 源表生成 DWS 层 DDL

演示过程：
1. DDLAgent 调用 `describe_table` 探索 order_items 结构
2. DDLAgent 调用 `read_convention` 读取建表规范
3. DDLAgent 设计目标表并生成 DDL
4. DDLAgent 调用 `ddl_verify` 在 DuckDB 沙箱中验证

### 13.5 自定义配置

通过环境变量调整模型：

```bash
# 使用其他 Ollama 模型
OLLAMA_MODEL=deepseek-coder-v2:16b python demo.py

# 使用远程 Ollama 服务
OLLAMA_BASE_URL=http://192.168.1.50:11434 python demo.py
```

---

## 十四、LLM 模型配置

### 14.1 支持的模型提供商

| 提供商 | 配置值 | 说明 | 是否需要 API Key |
|---|---|---|---|
| **OpenAI** | `openai` | GPT-4o / GPT-4o-mini | 是 |
| **Azure OpenAI** | `azure_openai` | 企业级 Azure 部署 | 是 |
| **Ollama** | `ollama` | 本地模型推理，推荐 qwen2.5:14b | 否 |
| **通义千问** | `tongyi` | 阿里云大模型 | 是 |
| **DeepSeek** | `deepseek` | OpenAI 兼容接口 | 是 |

### 14.2 settings.py → ProviderConfig 桥接

`AppSettings.get_provider_config()` 方法自动将 `.env` 中的配置项映射为 `ProviderConfig` 对象，供 `LangChainProvider` 使用：

```python
# src/config/settings.py
settings = get_settings()
config = settings.get_provider_config()  # → ProviderConfig
provider = LangChainProvider(config)     # → 可用的 LLM 实例
```

映射规则：

| LLM Provider | 使用的配置字段 |
|---|---|
| `openai` / `azure_openai` | `openai_*` 系列字段 |
| `ollama` / `local` | `ollama_*` 系列字段 |
| `tongyi` / `deepseek` | `openai_*` 系列字段（OpenAI 兼容接口） |

### 14.3 Ollama 本地模型配置（推荐）

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
OLLAMA_TEMPERATURE=0.1
OLLAMA_REQUEST_TIMEOUT=300
```

推荐模型选择：

| 模型 | 参数量 | 适用场景 |
|---|---|---|
| `qwen2.5:14b` | 14B | 推荐，SQL 生成和建模平衡 |
| `deepseek-coder-v2:16b` | 16B | 代码生成能力强 |
| `llama3.1:8b` | 8B | 轻量级，适合快速验证 |
| `codellama:13b` | 13B | Meta 代码专用模型 |

### 14.4 API 模式配置

**OpenAI：**

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o
OPENAI_TEMPERATURE=0.1
OPENAI_MAX_TOKENS=4096
OPENAI_TIMEOUT=120
```

**Azure OpenAI：**

```env
LLM_PROVIDER=azure_openai
OPENAI_API_KEY=your-azure-key
OPENAI_API_BASE=https://your-resource.openai.azure.com/
OPENAI_MODEL=your-deployment-name
```

**通义千问：**

```env
LLM_PROVIDER=tongyi
OPENAI_API_KEY=your-dashscope-api-key
OPENAI_MODEL=qwen-max
```

**DeepSeek：**

```env
LLM_PROVIDER=deepseek
OPENAI_API_KEY=your-deepseek-key
OPENAI_API_BASE=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

---

## 十五、完整配置参考

所有配置项通过 `.env` 文件或环境变量设置。环境变量前缀为 `DATAFORGE_`。

### 15.1 应用基础配置

| 字段名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `app_name` | str | `DataForge AI` | 应用名称 |
| `app_version` | str | `0.1.0` | 版本号 |
| `debug` | bool | `False` | 调试模式（详细日志、错误详情） |
| `log_level` | str | `INFO` | 日志级别：DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `api_prefix` | str | `/api/v1` | API 路由前缀 |
| `cors_origins` | List[str] | `["http://localhost:3000", "http://localhost:8080"]` | CORS 白名单 |
| `cors_allow_credentials` | bool | `True` | 是否允许跨域凭据 |

### 15.2 元数据库配置

| 字段名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `database_url` | str | `postgresql+asyncpg://dataforge:dataforge@localhost:5432/dataforge_meta` | 内部元数据库连接 |
| `database_pool_size` | int | `20` | 连接池大小 |
| `database_max_overflow` | int | `10` | 连接池溢出上限 |
| `database_pool_timeout` | int | `30` | 获取连接超时（秒） |
| `database_pool_recycle` | int | `1800` | 连接回收周期（秒） |
| `database_echo` | bool | `False` | SQLAlchemy SQL 回显 |

### 15.3 Redis / 缓存配置

| 字段名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `redis_url` | str | `redis://localhost:6379/0` | Redis 连接 URL |
| `redis_max_connections` | int | `50` | Redis 最大连接数 |
| `redis_key_prefix` | str | `dataforge:` | Redis 键前缀 |
| `cache_ttl_seconds` | int | `3600` | 缓存 TTL（秒），0 表示不过期 |

### 15.4 LLM 配置

| 字段名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `llm_provider` | str | `openai` | LLM 提供商 |
| `openai_api_key` | Optional[str] | `None` | OpenAI API Key |
| `openai_api_base` | Optional[str] | `None` | OpenAI Base URL（兼容 Azure / 代理服务） |
| `openai_model` | str | `gpt-4o` | 默认模型 |
| `openai_fallback_model` | str | `gpt-4o-mini` | 轻量降级模型 |
| `openai_temperature` | float | `0.1` | 采样温度（0.0 ~ 2.0） |
| `openai_max_tokens` | int | `4096` | 单次最大 Token 数 |
| `openai_request_timeout` | int | `120` | 请求超时（秒） |
| `openai_max_retries` | int | `3` | 失败重试次数 |

### 15.5 Ollama / 本地模型配置

| 字段名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `ollama_base_url` | str | `http://localhost:11434` | Ollama 服务地址 |
| `ollama_model` | str | `qwen2.5:14b` | 默认模型 |
| `ollama_temperature` | float | `0.1` | 采样温度 |
| `ollama_request_timeout` | int | `300` | 请求超时（秒） |

### 15.6 DuckDB 沙箱配置

| 字段名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `duckdb_enabled` | bool | `True` | 是否启用沙箱 |
| `duckdb_database_path` | str | `:memory:` | 数据库路径（`:memory:` 或文件路径） |
| `duckdb_verify_sample_rows` | int | `100` | 验证用样本行数（10 ~ 10000） |
| `duckdb_memory_limit_mb` | int | `512` | 内存上限（MB，最小 64） |

### 15.7 其他配置

| 字段名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `convention_file_path` | Optional[str] | `None` | 默认建表规范文件路径 |
| `query_timeout_seconds` | int | `300` | 查询超时（秒） |
| `query_max_rows` | int | `10000` | 单次查询最大返回行数 |
| `jwt_secret_key` | Optional[str] | `None` | JWT 密钥（预留） |
| `jwt_algorithm` | str | `HS256` | JWT 算法 |
| `jwt_access_token_expire_minutes` | int | `1440` | Token 有效期（分钟） |

### 15.8 .env.example 示例

项目根目录提供 `.env.example` 模板，包含所有配置项的示例值和注释说明。复制并修改即可使用：

```bash
cp .env.example .env
# 编辑 .env，至少配置 LLM 相关项
```

---

## 十六、API 端点速查表

### 16.1 系统端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 根端点（名称、版本、文档链接） |
| GET | `/health` | 健康检查（数据库、Redis、活跃连接） |
| GET | `/docs` | Swagger UI 交互式 API 文档 |
| GET | `/redoc` | ReDoc 文档视图 |
| GET | `/openapi.json` | OpenAPI Schema |

### 16.2 数据源连接 `/api/v1/connections`

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

### 16.3 DDL 生成 `/api/v1/ddl`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/build` | DDL 生成流水线（DDLAutoBuilder） |
| POST | `/verify` | DuckDB 沙箱验证 |
| POST | `/convention/validate` | 验证规范文件 |
| GET | `/convention/template` | 下载规范模板 |
| POST | `/convention/check-table` | 检查表合规性 |

### 16.4 AI 建模 `/api/v1/modeling`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/suggest` | 建模建议 |
| POST | `/dimensional` | 维度模型设计 |
| POST | `/review` | 模型审查 |
| POST | `/partition` | 分区建议 |

### 16.5 SQL 引擎 `/api/v1/sql`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/generate` | 自然语言生成 SQL |
| POST | `/explain` | SQL 解释 |
| POST | `/optimize` | SQL 优化 |
| POST | `/translate` | 方言翻译 |
| POST | `/execute` | 执行 SQL |

### 16.6 数据血缘 `/api/v1/lineage`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/table/{id}` | 表级血缘图 |
| GET | `/column/{id}/{col}` | 字段级血缘 |
| POST | `/analyze` | SQL 血缘分析 |
| GET | `/impact/{id}` | 影响分析 |

### 16.7 ETL 流水线 `/api/v1/etl`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/pipelines` | 创建流水线 |
| GET | `/pipelines` | 列出流水线 |
| GET | `/pipelines/{id}` | 流水线详情 |
| POST | `/pipelines/{id}/generate-dag` | 生成 Airflow DAG |
| POST | `/pipelines/{id}/validate` | 验证流水线 |

### 16.8 数仓分层 `/api/v1/warehouse`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/layers/{layer}/tables` | 在层中创建表 |
| GET | `/layers/{layer}/tables` | 列出层中的表 |
| POST | `/design` | AI 辅助分层设计 |
| GET | `/lineage/{table}` | 表血缘 |
| POST | `/migration` | 生成迁移脚本 |

---

## 十七、项目结构

```
dataforge-ai/
├── demo.py                                  # 本地快速体验脚本
├── .env.example                             # 环境变量模板
├── conventions/                             # 建表规范文件目录
│   └── default_convention.yaml              # 默认建表规范
├── src/
│   ├── __init__.py                          # 版本号、包说明
│   ├── main.py                              # FastAPI 应用入口
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                      # AppSettings 配置（pydantic-settings）
│   ├── core/
│   │   ├── __init__.py
│   │   ├── database.py                      # ConnectionManager（SQLAlchemy async）
│   │   ├── exceptions.py                    # 自定义异常体系
│   │   └── schemas.py                       # 公共数据模型（ColumnInfo, TableSchema 等）
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py                          # DatabaseAdapter 抽象基类
│   │   ├── factory.py                       # DatabaseAdapterFactory（适配器工厂）
│   │   ├── mysql_adapter.py                 # MySQL 适配器
│   │   ├── postgres_adapter.py              # PostgreSQL 适配器
│   │   ├── clickhouse_adapter.py            # ClickHouse 适配器
│   │   ├── doris_adapter.py                 # Apache Doris 适配器
│   │   ├── hive_adapter.py                  # Hive 适配器
│   │   ├── sqlserver_adapter.py             # SQL Server 适配器
│   │   ├── oracle_adapter.py                # Oracle 适配器
│   │   └── duckdb_sandbox.py                # DuckDB 本地验证沙箱
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py                        # 主路由聚合（7 个子路由组）
│   │   ├── deps.py                          # FastAPI 依赖注入
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── connection.py                # 数据源连接管理 API
│   │       ├── ddl_builder.py               # DDL 生成 + 沙箱验证 API
│   │       ├── modeling.py                  # AI 建模辅助 API
│   │       ├── sql.py                       # SQL 生成与优化 API
│   │       ├── lineage.py                   # 数据血缘追踪 API
│   │       ├── etl.py                       # ETL 管道编排 API
│   │       └── warehouse.py                 # 数仓分层管理 API
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── provider.py                      # LLM 提供商工厂 + LangChainProvider
│   │   ├── prompts.py                       # Prompt 模板注册表
│   │   ├── sql_generator.py                 # SQL 生成器
│   │   ├── model_advisor.py                 # AI 建模顾问
│   │   ├── optimizer.py                     # SQL 优化器
│   │   ├── output_parsers.py                # LLM 输出解析器
│   │   └── rag.py                           # RAG 检索增强
│   ├── warehouse/
│   │   ├── __init__.py
│   │   ├── tools.py                         # LangChain 工具定义（7 个工具）
│   │   ├── sql_agent.py                     # SQLAgent（智能问数 Agent）
│   │   ├── ddl_agent.py                     # DDLAgent（ETL 建表 Agent）
│   │   ├── ddl_auto_builder.py              # DDLAutoBuilder（规则引擎批量建表）
│   │   ├── convention_loader.py             # 建表规范加载与校验
│   │   ├── layers.py                        # 数仓分层配置与验证
│   │   ├── schema_manager.py                # Schema 管理器
│   │   └── lineage.py                       # 血缘追踪引擎
│   ├── etl/
│   │   ├── __init__.py
│   │   └── pipeline.py                      # ETL 流水线编排引擎
│   └── metadata/
│       ├── __init__.py
│       └── catalog.py                       # 元数据目录管理
```
