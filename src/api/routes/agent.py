"""API routes for AI Agents (SQLAgent + DDLAgent)."""

from __future__ import annotations

import os
import tempfile
from typing import Optional

import duckdb
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


# ---------------------------------------------------------------------------
#  Request / Response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    question: str = Field(..., description="自然语言数据需求", examples=["查2025年6月各商品购买数量和金额"])
    db_path: Optional[str] = Field(None, description="DuckDB 文件路径，留空使用内存库")
    convention_file: Optional[str] = Field(None, description="建表规范文件路径")


class AnalyzeResponse(BaseModel):
    success: bool
    question: str
    sql: Optional[str] = None
    reasoning: Optional[str] = None
    tool_calls: int = 0
    error: Optional[str] = None


class BuildDDLRequest(BaseModel):
    source_table: str = Field(..., description="源表名")
    target_layer: str = Field(..., description="目标层级: ODS/DWD/DWS/ADS")
    db_path: Optional[str] = Field(None, description="DuckDB 文件路径")
    convention_file: Optional[str] = Field(None, description="建表规范文件路径")
    business_desc: str = Field("", description="业务描述")


class BuildDDLResponse(BaseModel):
    success: bool
    source_table: str
    target_layer: str
    ddl: Optional[str] = None
    tool_calls: int = 0
    error: Optional[str] = None


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
        # Fallback: try Ollama directly
        from langchain_community.chat_models import ChatOllama
        return ChatOllama(
            model=os.environ.get("OLLAMA_MODEL", "qwen2.5:14b"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.1,
            request_timeout=300,
        )


# ---------------------------------------------------------------------------
#  Endpoints
# ---------------------------------------------------------------------------

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_question(req: AnalyzeRequest):
    """智能问数：自然语言 → 思考过程 → SQL → 验证。"""
    try:
        from src.warehouse.sql_agent import SQLAgent

        db = req.db_path or ":memory:"
        llm = _create_ll()

        agent = SQLAgent(
            llm=llm,
            db=db,
            convention_file=req.convention_file,
        )

        result = agent.analyze(req.question)

        return AnalyzeResponse(
            success=result.success,
            question=result.question,
            sql=result.sql,
            reasoning=result.reasoning,
            tool_calls=len(result.tool_calls_log),
            error=result.error,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/build-ddl", response_model=BuildDDLResponse)
async def build_ddl(req: BuildDDLRequest):
    """ETL 建表：从源表自动生成数仓 DDL。"""
    try:
        from src.warehouse.ddl_agent import DDLAgent

        db = req.db_path or ":memory:"
        llm = _create_ll()

        agent = DDLAgent(
            llm=llm,
            db=db,
            convention_file=req.convention_file,
        )

        result = agent.build(
            source_table=req.source_table,
            target_layer=req.target_layer,
            business_desc=req.business_desc,
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
        raise HTTPException(status_code=500, detail=str(e))
