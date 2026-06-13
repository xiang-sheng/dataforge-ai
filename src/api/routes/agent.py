"""API routes for AI Agents — unified chat + direct endpoints."""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

# Directories that db_path must never write to
_BLOCKED_DB_DIRS = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev")
# On Windows these translate to system roots like C:\Windows, C:\Program Files
if os.name == "nt":
    _BLOCKED_DB_DIRS = (
        os.environ.get("SYSTEMROOT", r"C:\Windows").lower(),
        os.environ.get("PROGRAMFILES", r"C:\Program Files").lower(),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)").lower(),
    )


def _validate_db_path(raw_path: str) -> str:
    """Validate a DuckDB file path to prevent writing to sensitive directories.

    Allows ``:memory:`` and paths that resolve outside system directories.
    Raises HTTPException(400) for blocked paths.
    """
    if raw_path == ":memory:" or not raw_path:
        return raw_path or ":memory:"

    if ".." in raw_path:
        raise HTTPException(
            status_code=400,
            detail="db_path must not contain '..' components.",
        )

    resolved = Path(raw_path).resolve()
    resolved_lower = str(resolved).lower()

    for blocked in _BLOCKED_DB_DIRS:
        if blocked and resolved_lower.startswith(blocked.lower()):
            raise HTTPException(
                status_code=400,
                detail=f"db_path must not point to a system directory: '{raw_path}'.",
            )

    return str(resolved)


# ---------------------------------------------------------------------------
#  Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., description="自然语言输入（自动路由到对应 Agent）", examples=["查6月各商品购买数量和金额"])
    target_agent: str | None = Field(None, description="指定 Agent 名称（跳过意图分类）")
    db_path: str | None = Field(None, description="DuckDB 文件路径")
    convention_file: str | None = Field(None, description="建表规范文件路径")
    context: dict | None = Field(None, description="额外上下文参数")


class ChatResponse(BaseModel):
    success: bool
    agent_name: str
    content: str
    metadata: dict = Field(default_factory=dict)
    error: str | None = None


class AgentInfo(BaseModel):
    name: str
    description: str
    keywords: str


class AnalyzeRequest(BaseModel):
    question: str = Field(..., description="自然语言数据需求", examples=["查2025年6月各商品购买数量和金额"])
    db_path: str | None = Field(None, description="DuckDB 文件路径，留空使用内存库")
    convention_file: str | None = Field(None, description="建表规范文件路径")


class AnalyzeResponse(BaseModel):
    success: bool
    question: str
    sql: str | None = None
    reasoning: str | None = None
    tool_calls: int = 0
    error: str | None = None


class BuildDDLRequest(BaseModel):
    source_table: str = Field(..., description="源表名")
    target_layer: str = Field(..., description="目标层级: ODS/DWD/DWS/ADS")
    db_path: str | None = Field(None, description="DuckDB 文件路径")
    convention_file: str | None = Field(None, description="建表规范文件路径")
    business_desc: str = Field("", description="业务描述")


class BuildDDLResponse(BaseModel):
    success: bool
    source_table: str
    target_layer: str
    ddl: str | None = None
    tool_calls: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
#  Helper: create LLM from settings
# ---------------------------------------------------------------------------

def _create_ll():
    """Create a ChatModel from app settings."""
    try:
        from src.config.settings import get_settings
        settings = get_settings()
        provider_config = settings.get_provider_config()

        from src.ai.provider import LLMFactory
        return LLMFactory.create_chat_model(provider_config)
    except ImportError:
        from langchain_community.chat_models import ChatOllama
        return ChatOllama(
            model=os.environ.get("OLLAMA_MODEL", "qwen2.5:14b"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.1,
            request_timeout=300,
        )


@lru_cache(maxsize=8)
def _create_orchestrator(db_path: str = ":memory:", convention_file: str | None = None):
    """Create a fully configured AgentOrchestrator with all agents registered."""
    from src.agents import (
        AgentOrchestrator,
        AgentRegistry,
        DDLAgentWrapper,
        GovernanceAgentWrapper,
        SQLAgentWrapper,
    )

    llm = _create_ll()
    registry = AgentRegistry()

    sql_agent = SQLAgentWrapper(llm=llm, db=db_path, convention_file=convention_file)
    ddl_agent = DDLAgentWrapper(llm=llm, db=db_path, convention_file=convention_file)
    gov_agent = GovernanceAgentWrapper(llm=llm, db=db_path, convention_file=convention_file)

    registry.register(sql_agent)
    registry.register(ddl_agent)
    registry.register(gov_agent)

    return AgentOrchestrator(registry, llm)


# ---------------------------------------------------------------------------
#  Unified chat endpoint (routes via intent classification)
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
async def unified_chat(req: ChatRequest):
    """统一入口：自动识别意图并路由到对应 Agent。

    支持智能问数（自然语言查询）和智能建表（DDL 生成），
    也可通过 target_agent 参数直接指定 Agent。
    """
    try:
        db = _validate_db_path(req.db_path or ":memory:")
        orch = _create_orchestrator(db, req.convention_file)

        ctx = req.context or {}
        if req.convention_file:
            ctx["convention_file"] = req.convention_file

        result = await asyncio.to_thread(
            lambda: orch.chat(
                message=req.message,
                target_agent=req.target_agent,
                context=ctx,
            )
        )

        return ChatResponse(
            success=result.success,
            agent_name=result.agent_name,
            content=result.content,
            metadata=result.metadata,
            error=result.error,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents():
    """列出所有已注册的 Agent 及其描述。"""
    try:
        orch = _create_orchestrator()
        agents = await asyncio.to_thread(orch.list_agents)
        return [AgentInfo(**a) for a in agents]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------------------------------------------------------
#  Direct endpoints (bypass intent routing)
# ---------------------------------------------------------------------------

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_question(req: AnalyzeRequest):
    """智能问数：自然语言 → 思考过程 → SQL → 验证。"""
    try:
        from src.warehouse.sql_agent import SQLAgent

        db = _validate_db_path(req.db_path or ":memory:")
        llm = _create_ll()

        agent = SQLAgent(
            llm=llm,
            db=db,
            convention_file=req.convention_file,
        )

        result = await asyncio.to_thread(agent.analyze, req.question)

        return AnalyzeResponse(
            success=result.success,
            question=result.question,
            sql=result.sql,
            reasoning=result.reasoning,
            tool_calls=len(result.tool_calls_log),
            error=result.error,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/build-ddl", response_model=BuildDDLResponse)
async def build_ddl(req: BuildDDLRequest):
    """ETL 建表：从源表自动生成数仓 DDL。"""
    try:
        from src.warehouse.ddl_agent import DDLAgent

        db = _validate_db_path(req.db_path or ":memory:")
        llm = _create_ll()

        agent = DDLAgent(
            llm=llm,
            db=db,
            convention_file=req.convention_file,
        )

        result = await asyncio.to_thread(
            lambda: agent.build(
                source_table=req.source_table,
                target_layer=req.target_layer,
                business_desc=req.business_desc,
            )
        )

        return BuildDDLResponse(
            success=result.success,
            source_table=result.source_table,
            target_layer=result.target_layer,
            ddl=result.ddl,
            tool_calls=len(result.tool_calls_log),
            error=result.error,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
