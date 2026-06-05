# DataForge-AI

> AI 驱动的数据仓库构建平台 —— 多数据库连接、智能建模、SQL 生成与优化、数据血缘追踪、ETL 流水线设计

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-green)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-Apache%202.0-orange)](./LICENSE)

---

## 目录

- [项目简介](#项目简介)
- [系统架构](#系统架构)
- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [API 概览](#api-概览)
- [开发指南](#开发指南)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 项目简介

**DataForge-AI** 是一款面向数据工程师和数据架构师的 AI 驱动型数据仓库构建平台。它将大语言模型（LLM）的能力与企业数据仓库开发全流程深度融合，提供从数据源接入、元数据采集、维度建模、SQL 自动生成与优化、数据血缘追踪到 ETL 流水线编排的一站式解决方案。

### 核心价值

| 痛点 | DataForge-AI 的解决方式 |
|---|---|
| 多异构数据库手工对接耗时耗力 | 统一连接管理层，支持 7+ 主流数据库即插即用 |
| 数仓建模依赖专家经验 | AI 辅助维度建模，自动推荐星型/雪花模型 |
| SQL 编写与调优门槛高 | 自然语言转 SQL + 智能优化建议 |
| 数据血缘关系难以追踪 | 自动解析 SQL 生成字段级血缘图谱 |
| ETL 流程设计与维护复杂 | 可视化 DAG 编排，支持增量/全量策略自动生成 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          客户端 (Web UI / API)                          │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │  HTTP / WebSocket
┌───────────────────────────────────▼─────────────────────────────────────┐
│                         API Gateway (FastAPI)                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ 数据源   │ │ 元数据   │ │ 智能建模 │ │ SQL 引擎 │ │  血缘 & ETL  │  │
│  │ 管理模块 │ │ 采集模块 │ │   模块   │ │   模块   │ │    模块      │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│       │            │            │            │              │           │
├───────┼────────────┼────────────┼────────────┼──────────────┼───────────┤
│       │         Core Services   │            │              │           │
│  ┌────▼────┐  ┌────▼────┐  ┌───▼────┐  ┌───▼─────┐  ┌────▼────────┐  │
│  │ DB Pool │  │Metadata │  │  LLM   │  │  SQL    │  │  Lineage    │  │
│  │ Manager │  │ Store   │  │Gateway │  │ Parser  │  │  Analyzer   │  │
│  └────┬────┘  └────┬────┘  └───┬────┘  └───┬─────┘  └────┬────────┘  │
├───────┼────────────┼────────────┼────────────┼──────────────┼───────────┤
│                     Infrastructure Layer                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │PostgreSQL│  │  Redis   │  │  OpenAI API  │  │  Target Databases  │  │
│  │(元数据)  │  │  (缓存)  │  │  (LLM 服务)  │  │  (MySQL/PG/CH/...) │  │
│  └──────────┘  └──────────┘  └──────────────┘  └────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 核心功能

### 1. 多数据库连接管理

支持以下数据库类型的统一接入与管理：

| 数据库 | 协议/驱动 | 典型场景 |
|---|---|---|
| **MySQL** | aiomysql | OLTP 业务数据源 |
| **PostgreSQL** | asyncpg | OLTP / OLAP 双场景 |
| **SQL Server** | pymssql | 企业级 ERP/CRM 数据源 |
| **Oracle** | oracledb | 传统企业核心系统 |
| **ClickHouse** | clickhouse-driver | 高性能 OLAP 分析引擎 |
| **Apache Doris** | MySQL 协议兼容 | 实时分析型数仓 |
| **Hive** | PyHive | 大数据离线数仓 |

- 连接池自动管理与健康检查
- 连接测试、元数据浏览、表结构预览
- 凭据加密存储，支持连接分组与标签

### 2. 智能数据建模

- **AI 辅助维度建模**：基于业务描述自动生成维度表 / 事实表设计方案
- **模型模板**：内置星型模型、雪花模型、Data Vault 2.0 等常用范式
- **字段映射**：自动推荐源表到目标模型的字段映射关系
- **DDL 生成**：一键生成目标数据库方言的建表语句

### 3. SQL 生成与优化

- **自然语言转 SQL**：用自然语言描述需求，AI 自动生成标准 SQL
- **SQL 优化建议**：基于执行计划和统计信息给出索引、分区、改写建议
- **多方言支持**：自动适配不同数据库的 SQL 方言差异
- **SQL 模板库**：常用 ETL 模式（增量抽取、拉链表、SCD 等）预置模板

### 4. 数据血缘追踪

- **字段级血缘**：精确到字段的数据流向追踪
- **SQL 解析引擎**：自动解析 INSERT/SELECT/CTE 语句提取血缘关系
- **影响分析**：变更上游表结构时自动评估下游影响范围
- **可视化图谱**：支持导出为 Mermaid / JSON 格式的血缘关系图

### 5. ETL 流水线设计

- **DAG 编排**：基于有向无环图的任务编排与依赖管理
- **增量 / 全量策略**：AI 根据数据特征推荐最佳加载策略
- **代码生成**：自动生成 Airflow / Dagster 可执行的任务脚本
- **监控告警**：运行状态追踪，异常自动告警

---

## 技术栈

| 层面 | 技术选型 |
|---|---|
| **Web 框架** | FastAPI + Uvicorn |
| **ORM** | SQLAlchemy 2.0 (async) |
| **数据库迁移** | Alembic |
| **缓存** | Redis |
| **LLM 集成** | OpenAI SDK (可替换为其他兼容接口) |
| **数据校验** | Pydantic v2 + pydantic-settings |
| **HTTP 客户端** | httpx |
| **代码质量** | Ruff (lint + format) + mypy |
| **测试** | pytest + pytest-asyncio |
| **容器化** | Docker (multi-stage build) |
| **Python 版本** | >= 3.11 |

---

## 快速开始

### 前置条件

- Python >= 3.11
- PostgreSQL >= 14（用作元数据存储）
- Redis >= 7（用作缓存）
- （可选）Docker & Docker Compose

### 方式一：本地开发

```bash
# 1. 克隆仓库
git clone https://github.com/dataforge/dataforge-ai.git
cd dataforge-ai

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入实际的数据库连接信息和 OpenAI API Key

# 5. 运行数据库迁移
alembic upgrade head

# 6. 启动开发服务器
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

启动后访问：
- API 文档：http://localhost:8000/docs
- ReDoc：http://localhost:8000/redoc
- 健康检查：http://localhost:8000/health

### 方式二：Docker 部署

```bash
# 构建镜像
docker build -t dataforge-ai:latest .

# 运行容器
docker run -d \
  --name dataforge-ai \
  --env-file .env \
  -p 8000:8000 \
  dataforge-ai:latest
```

### 方式三：Docker Compose（推荐）

```bash
docker compose up -d
```

---

## 项目结构

```
dataforge-ai/
├── src/
│   ├── main.py                     # FastAPI 应用入口
│   ├── config.py                   # 配置管理 (pydantic-settings)
│   ├── api/                        # API 路由层
│   │   ├── __init__.py
│   │   ├── deps.py                 # 依赖注入 (DB session, current user, etc.)
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── datasources.py      # 数据源管理接口
│   │       ├── metadata.py         # 元数据采集接口
│   │       ├── modeling.py         # 数据建模接口
│   │       ├── sql_engine.py       # SQL 生成/优化接口
│   │       ├── lineage.py          # 数据血缘接口
│   │       └── etl.py              # ETL 流水线接口
│   ├── core/                       # 核心业务逻辑
│   │   ├── __init__.py
│   │   ├── database/
│   │   │   ├── __init__.py
│   │   │   ├── pool.py             # 连接池管理
│   │   │   ├── connectors/         # 各数据库连接器
│   │   │   │   ├── base.py
│   │   │   │   ├── mysql.py
│   │   │   │   ├── postgresql.py
│   │   │   │   ├── mssql.py
│   │   │   │   ├── oracle.py
│   │   │   │   ├── clickhouse.py
│   │   │   │   ├── doris.py
│   │   │   │   └── hive.py
│   │   │   └── dialect.py          # SQL 方言适配
│   │   ├── modeling/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py           # 建模引擎
│   │   │   ├── templates.py        # 模型模板
│   │   │   └── ddl_generator.py    # DDL 生成器
│   │   ├── sql/
│   │   │   ├── __init__.py
│   │   │   ├── generator.py        # SQL 生成
│   │   │   ├── optimizer.py        # SQL 优化
│   │   │   └── parser.py           # SQL 解析
│   │   ├── lineage/
│   │   │   ├── __init__.py
│   │   │   ├── analyzer.py         # 血缘分析
│   │   │   └── graph.py            # 血缘图谱
│   │   └── etl/
│   │       ├── __init__.py
│   │       ├── dag.py              # DAG 编排
│   │       ├── strategies.py       # 增量/全量策略
│   │       └── codegen.py          # 任务代码生成
│   ├── llm/                        # LLM 集成层
│   │   ├── __init__.py
│   │   ├── client.py               # OpenAI 客户端封装
│   │   ├── prompts.py              # Prompt 模板管理
│   │   └── cache.py                # LLM 响应缓存
│   ├── models/                     # SQLAlchemy ORM 模型
│   │   ├── __init__.py
│   │   ├── base.py                 # 声明基类 & Mixins
│   │   ├── datasource.py           # 数据源模型
│   │   ├── metadata.py             # 元数据模型
│   │   ├── modeling.py             # 建模模型
│   │   └── lineage.py              # 血缘模型
│   ├── schemas/                    # Pydantic 请求/响应模型
│   │   ├── __init__.py
│   │   ├── common.py               # 通用响应结构
│   │   ├── datasource.py
│   │   ├── metadata.py
│   │   ├── modeling.py
│   │   ├── sql_engine.py
│   │   ├── lineage.py
│   │   └── etl.py
│   ├── services/                   # 业务服务层
│   │   ├── __init__.py
│   │   ├── datasource_service.py
│   │   ├── metadata_service.py
│   │   ├── modeling_service.py
│   │   ├── sql_service.py
│   │   ├── lineage_service.py
│   │   └── etl_service.py
│   └── utils/                      # 工具函数
│       ├── __init__.py
│       ├── exceptions.py           # 自定义异常
│       ├── logging.py              # 日志配置
│       └── security.py             # 加密 & 认证
├── alembic/                        # 数据库迁移
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── alembic.ini
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # pytest fixtures
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_sql_generator.py
│   │   ├── test_lineage_analyzer.py
│   │   └── test_modeling_engine.py
│   └── integration/
│       ├── __init__.py
│       ├── test_datasource_api.py
│       └── test_database_connectors.py
├── scripts/                        # 辅助脚本
│   └── init_db.py
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── LICENSE
└── README.md
```

---

## API 概览

| 模块 | 路径 | 说明 |
|---|---|---|
| 健康检查 | `GET /health` | 服务健康状态 |
| 数据源 | `/api/v1/datasources` | 数据源 CRUD、连接测试、元数据浏览 |
| 元数据 | `/api/v1/metadata` | 元数据采集任务、表/列信息查询 |
| 建模 | `/api/v1/modeling` | 数据模型设计、DDL 生成、AI 建模建议 |
| SQL 引擎 | `/api/v1/sql` | SQL 生成、优化、方言转换 |
| 血缘 | `/api/v1/lineage` | 血缘分析、影响分析、图谱导出 |
| ETL | `/api/v1/etl` | DAG 编排、策略推荐、代码生成 |

完整 API 文档启动服务后访问 `/docs`（Swagger UI）查看。

---

## 开发指南

### 环境搭建

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 安装 pre-commit hooks
pre-commit install
```

### 代码规范

```bash
# Lint 检查
ruff check src/ tests/

# 自动修复
ruff check --fix src/ tests/

# 格式化
ruff format src/ tests/

# 类型检查
mypy src/
```

### 运行测试

```bash
# 全量测试
pytest

# 仅运行单元测试（跳过需要外部服务的集成测试）
pytest -m "not integration"

# 生成覆盖率报告
pytest --cov=src --cov-report=html

# 运行特定测试文件
pytest tests/unit/test_sql_generator.py -v
```

### 数据库迁移

```bash
# 创建新迁移
alembic revision --autogenerate -m "description of change"

# 执行迁移
alembic upgrade head

# 回滚一步
alembic downgrade -1
```

---

## 贡献指南

我们欢迎各种形式的贡献！

### 贡献流程

1. **Fork** 本仓库
2. 创建你的特性分支 (`git checkout -b feature/amazing-feature`)
3. 进行开发并提交 commit（请遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范）
4. 推送到你的 Fork (`git push origin feature/amazing-feature`)
5. 创建 **Pull Request**

### Commit 规范

```
feat:     新功能
fix:      修复 Bug
docs:     文档变更
style:    代码风格（不影响逻辑）
refactor: 重构
perf:     性能优化
test:     测试相关
chore:    构建/工具变更
```

### 提交 PR 前请确保

- [ ] 代码已通过 `ruff check` 和 `mypy` 检查
- [ ] 新增功能已编写对应测试
- [ ] 所有测试通过 (`pytest`)
- [ ] 文档已同步更新（如有必要）

---

## 许可证

本项目基于 [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) 开源。

```
Copyright 2024-2026 DataForge Team

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
