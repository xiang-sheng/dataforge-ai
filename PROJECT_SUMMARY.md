## DataForge AI - 项目总结

**版本:** v0.3.0 | **仓库:** github.com/xiang-sheng/dataforge-ai | **状态:** 多 Agent + 数据治理完成

---

### 项目定位

DataForge AI 是一个 AI 驱动的数据仓库构建平台，提供从自然语言数据分析到自动化数仓建表的完整能力链。项目基于 Python 3.11+ / FastAPI 构建，支持 7 种数据库连接，通过 LangChain + Ollama 实现本地化 AI 推理。

核心思路是：用户用自然语言描述数据需求，AI Agent 自主探索数据库表结构，打印思考过程，生成并验证 SQL，最终可选将查询结果固化为持久表或自动生成数仓分层 DDL。

---

### 核心能力

**智能问数 (SQLAgent)** — 自然语言数据分析。用户提问后，Agent 自动探索表结构，输出结构化思考过程（【思考过程】），生成 SQL 并执行验证，建议是否需要固化为持久表。典型场景："查 2025 年 6 月每个商品的购买数量和总金额"。

**智能建表 (DDLAgent)** — 自动化数仓 DDL 生成。给定源表和目标层级（ODS/DWD/DWS/ADS），Agent 探索源表结构、读取建表规范（YAML），设计目标表字段并生成 DDL，最后执行验证。典型场景："为 order_items 源表生成 DWS 层的目标表 DDL"。

**数据治理 (GovernanceAgent)** — 冗余表识别与治理建议。Agent 自主扫描所有表，通过列名 Jaccard 相似度、字段类型匹配率、行数对比等维度，识别冗余/重叠表对并输出合并、归档或清理建议。还支持表名前缀/后缀模式（_bak, _copy, _old, _tmp, _v2）自动标记高度可疑项。典型场景："扫描数据库，检查有没有冗余表或重叠的表结构"。

**多 Agent 编排 (AgentOrchestrator)** — 统一入口 + LLM 意图分类。用户的自然语言输入自动路由到正确的 Agent，也支持通过 `target_agent` 参数显式指定。新增 Agent 只需 3 步：继承 ManagedAgent、实现 process() 方法、注册到 Registry。

**多数据库连接** — 支持 PostgreSQL、MySQL、ClickHouse、Hive、Oracle、SQL Server、Doris 七种数据库的元数据发现和数据查询。

**DuckDB 沙箱验证** — 嵌入式 OLAP 引擎用于本地 SQL 验证和数据分析，无需外部数据库即可测试 Agent 能力。

**建表规范驱动** — YAML 格式的 Convention 文件定义命名规则、数据类型映射和分区策略，DDL 生成自动遵循。

---

### 技术架构

```
用户输入（自然语言）
       │
       ▼
┌─────────────────────────────────────┐
│       AgentOrchestrator             │
│  ┌───────────┐  ┌───────────────┐   │
│  │IntentRouter│  │ AgentRegistry │   │
│  │ (LLM分类) │  │ (注册表管理)  │   │
│  └─────┬─────┘  └───────────────┘   │
└────────┼────────────────────────────┘
         │ 意图路由
    ┌────┴────┐
    ▼         ▼         ▼
┌────────┐ ┌─────────┐ ┌──────────┐
│SQLAgent│ │DDLAgent │ │Governance│  ← 可扩展新 Agent
│智能问数│ │智能建表 │ │数据治理  │
└───┬────┘ └────┬────┘ └────┬─────┘
    │           │
    ▼           ▼
┌──────────────────────┐
│   BaseAgent (ReAct)  │  ← 共享 ReAct 循环
│   + LangChain Tools  │
│   + 速率限制/日志    │
└──────────┬───────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
┌────────┐  ┌────────────┐
│DuckDB  │  │Convention  │
│(沙箱)  │  │Loader(YAML)│
└────────┘  └────────────┘
```

---

### 技术栈

| 层级 | 技术选型 |
|------|---------|
| Web 框架 | FastAPI + Uvicorn |
| 异步 ORM | SQLAlchemy 2.0 (asyncio) + Alembic |
| LLM 框架 | LangChain (core + community + openai) |
| 本地模型 | Ollama (qwen2.5:14b) |
| 嵌入式数据库 | DuckDB |
| 配置管理 | Pydantic v2 + pydantic-settings |
| 缓存 | Redis 7 |
| 容器化 | Docker Compose (API + PostgreSQL 16 + Redis 7) |
| 测试 | pytest + pytest-asyncio (140 tests) |
| 代码质量 | Ruff (linter + formatter) + mypy |
| 数据库驱动 | asyncpg, aiomysql, clickhouse-driver, pyhive, oracledb, pymssql |

---

### 项目结构

```
dataforge-ai/
├── src/
│   ├── agents/              # 多 Agent 管理层（新增）
│   │   ├── base.py          #   ManagedAgent 抽象基类 + AgentResult
│   │   ├── registry.py      #   AgentRegistry 注册表
│   │   ├── router.py        #   IntentRouter LLM 意图分类
│   │   ├── orchestrator.py  #   AgentOrchestrator 统一入口
│   │   ├── sql_wrapper.py   #   SQLAgent 适配器
│   │   ├── ddl_wrapper.py   #   DDLAgent 适配器
│   │   └── governance_wrapper.py # GovernanceAgent 适配器（数据治理）
│   ├── warehouse/           # 数仓 Agent 核心
│   │   ├── base_agent.py    #   BaseAgent ReAct 循环（共享）
│   │   ├── sql_agent.py     #   SQLAgent 智能问数
│   │   ├── ddl_agent.py     #   DDLAgent 智能建表
│   │   ├── tools.py         #   8 个 LangChain 工具（contextvars 安全）
│   │   ├── ddl_auto_builder.py  # DDL 自动化流水线
│   │   ├── convention_loader.py # YAML 规范加载
│   │   └── layers.py        #   数仓分层定义
│   ├── ai/                  # AI 能力层
│   │   ├── provider.py      #   LLMFactory + LangChainProvider
│   │   ├── sql_generator.py #   SQL 生成/解释/优化/翻译
│   │   └── model_advisor.py #   数据建模建议
│   ├── api/                 # FastAPI 路由
│   │   ├── routes/agent.py  #   /agent/chat + /agent/agents
│   │   ├── routes/sql.py    #   SQL 生成/执行端点
│   │   └── routes/...       #   connection, warehouse, ddl, modeling, lineage, etl
│   ├── db/                  # 数据库适配器（7 种）
│   │   ├── postgres_adapter.py
│   │   ├── mysql_adapter.py
│   │   ├── clickhouse_adapter.py
│   │   └── ...              #   hive, oracle, sqlserver, doris
│   ├── config/settings.py   # Pydantic 配置（支持 6 种 LLM Provider）
│   └── main.py              # FastAPI 入口
├── tests/                   # 140 个测试
├── conventions/             # 建表规范 YAML
├── docker-compose.yml       # 一键部署
├── demo.py                  # 多 Agent 编排演示脚本
└── pyproject.toml           # 项目配置
```

---

### 多 Agent 扩展机制

新增一个 Agent 只需 3 步：

```python
# 1. 创建 wrapper（继承 ManagedAgent）
class DataAnalysisWrapper(ManagedAgent):
    name = "data_analysis"
    description = "数据分析：深度分析数据趋势和异常"
    intent_keywords = ["趋势", "异常", "分析", "报告"]

    def process(self, message, context=None) -> AgentResult:
        # 实现具体逻辑
        return AgentResult(agent_name=self.name, content="...")

# 2. 注册到 Registry
registry.register(DataAnalysisWrapper(llm=llm, db=db))

# 3. 自动生效 — IntentRouter 会自动将匹配的意图路由到新 Agent
```

---

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/agent/chat` | 统一入口（自动意图路由） |
| GET  | `/api/v1/agent/agents` | 列出所有已注册 Agent |
| POST | `/api/v1/agent/analyze` | 智能问数（直接调用） |
| POST | `/api/v1/agent/build-ddl` | 智能建表（直接调用） |
| POST | `/api/v1/sql/generate` | SQL 生成 |
| POST | `/api/v1/sql/explain` | SQL 解释 |
| POST | `/api/v1/sql/optimize` | SQL 优化 |
| POST | `/api/v1/sql/translate` | SQL 翻译（跨方言） |
| POST | `/api/v1/sql/execute` | SQL 执行 |
| POST | `/api/v1/modeling/suggest` | 建模建议 |
| POST | `/api/v1/modeling/design` | 维度模型设计 |
| POST | `/api/v1/modeling/review` | 模型审查 |
| GET  | `/health` | 健康检查 |

---

### 测试状态

**140 / 140 全部通过**

| 测试文件 | 数量 | 覆盖范围 |
|---------|------|---------|
| test_agents.py | 40 | Agent 管理层（registry/router/orchestrator/wrapper + governance） |
| test_tools.py | 31 | 8 个 LangChain 工具 + SQL 注入防护 + compare_tables |
| test_connection.py | 22 | 连接管理 CRUD + 元数据发现 |
| test_modeling.py | 16 | 建模建议/维度设计/模型审查/分区建议 |
| test_sql.py | 21 | SQL 生成/解释/优化/翻译/执行 |
| test_base_agent.py | 5 | BaseAgent ReAct 循环 |

---

### 安全与质量

项目中已实施的关键安全措施包括：SQL 注入防护（正则白名单 `_validate_identifier`）、`execute_query` 仅允许 SELECT/WITH 语句、标识符长度限制（128 字符）、convention 文件大小限制（15000 字符）、DDL 执行前自动 DROP IF EXISTS 防止冲突。

代码质量方面：contextvars 替代全局变量确保并发安全、logging 替代 print 确保日志可控、Ruff 统一代码风格、类型注解覆盖完整。

---

### 快速启动

```bash
# 安装依赖
pip install -e ".[dev]"

# 启动 Ollama（本地模型）
ollama serve && ollama pull qwen2.5:14b

# 运行多 Agent 演示
python demo.py

# 启动 API 服务
uvicorn src.main:app --reload --port 8000

# 运行测试
pytest tests/ -v

# Docker 部署
docker-compose up -d
```

---

### 开发历程

| 提交 | 阶段 |
|------|------|
| f70ae4c | 项目脚手架 — FastAPI + 多数据库 + AI 层 |
| 3879e65 | DDL 自动构建 + DuckDB 沙箱 + Convention 加载 + LangChain |
| 45eb068 | Agent 化 DDL 生成（LangChain 工具调用） |
| aa7719a | SQLAgent 重写 — 自然语言分析 + 显式思考过程 |
| 5cdea30 | 合并双 Agent — 智能问数 + ETL 建表完整工作流 |
| 2b6d7bc | 全面质量提升 — 14 项审计问题修复（SQL 注入/测试/Docker/日志） |
| 45595b2 | 多 Agent 编排 — AgentOrchestrator + LLM 意图路由 |
| 466f23c | 服务端优化 — 路由挂载/并发安全/日志规范/代码去重 |
| (最新)  | 数据治理 — GovernanceAgent + compare_tables 冗余表识别 |
