"""Tests for src.agents — multi-agent management layer."""

import os
import tempfile
from unittest.mock import MagicMock

import duckdb
import pytest

from src.agents.base import AgentResult, ManagedAgent
from src.agents.registry import AgentRegistry
from src.agents.router import IntentRouter
from src.agents.orchestrator import AgentOrchestrator
from src.agents.sql_wrapper import SQLAgentWrapper
from src.agents.ddl_wrapper import DDLAgentWrapper


# ===================================================================
#  Test helpers
# ===================================================================


class MockAgent(ManagedAgent):
    """Simple test agent that echoes the message."""

    name = "mock_agent"
    description = "测试用 Mock Agent"
    intent_keywords = ["测试", "mock", "echo"]

    def process(self, message: str, context: dict | None = None) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            content=f"Echo: {message}",
            metadata={"context_keys": list((context or {}).keys())},
            success=True,
        )


class FailingAgent(ManagedAgent):
    """Agent that always raises an error."""

    name = "failing_agent"
    description = "总是失败的 Agent"
    intent_keywords = ["失败", "error"]

    def process(self, message: str, context: dict | None = None) -> AgentResult:
        raise RuntimeError("Intentional test error")


class SecondAgent(ManagedAgent):
    """Another test agent for routing tests."""

    name = "second_agent"
    description = "第二个测试 Agent"
    intent_keywords = ["第二", "another", "build"]

    def process(self, message: str, context: dict | None = None) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            content=f"Second: {message}",
            success=True,
        )


# ===================================================================
#  AgentResult tests
# ===================================================================


class TestAgentResult:
    def test_default_success(self):
        r = AgentResult(agent_name="test", content="hello")
        assert r.success is True
        assert r.error is None
        assert r.metadata == {}

    def test_with_error(self):
        r = AgentResult(agent_name="test", content="fail", success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"

    def test_metadata(self):
        r = AgentResult(agent_name="test", content="x", metadata={"key": 42})
        assert r.metadata["key"] == 42


# ===================================================================
#  AgentRegistry tests
# ===================================================================


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = MockAgent()
        reg.register(agent)
        assert reg.get("mock_agent") is agent

    def test_list_agents(self):
        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.register(SecondAgent())
        agents = reg.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"mock_agent", "second_agent"}

    def test_duplicate_name_raises(self):
        reg = AgentRegistry()
        reg.register(MockAgent())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(MockAgent())

    def test_empty_name_raises(self):
        reg = AgentRegistry()
        agent = MockAgent()
        agent.name = ""
        with pytest.raises(ValueError, match="no name"):
            reg.register(agent)

    def test_unregister(self):
        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.unregister("mock_agent")
        assert reg.get("mock_agent") is None

    def test_get_nonexistent(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_agents_property(self):
        reg = AgentRegistry()
        reg.register(MockAgent())
        agents = reg.agents
        assert "mock_agent" in agents
        assert isinstance(agents, dict)


# ===================================================================
#  IntentRouter tests
# ===================================================================


class TestIntentRouter:
    def test_single_agent_skip(self):
        """When only one agent is registered, skip classification."""
        reg = AgentRegistry()
        reg.register(MockAgent())
        llm = MagicMock()
        router = IntentRouter(reg, llm)

        result = router.classify("anything")
        assert result == "mock_agent"
        llm.invoke.assert_not_called()

    def test_empty_registry(self):
        reg = AgentRegistry()
        llm = MagicMock()
        router = IntentRouter(reg, llm)
        assert router.classify("test") == "unknown"

    def test_keyword_fallback(self):
        """When LLM fails, fall back to keyword matching."""
        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.register(SecondAgent())

        llm = MagicMock()
        llm.invoke.side_effect = Exception("LLM down")

        router = IntentRouter(reg, llm)
        # "测试" matches mock_agent's keywords
        result = router.classify("这是一个测试")
        assert result == "mock_agent"

    def test_keyword_match_second(self):
        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.register(SecondAgent())

        llm = MagicMock()
        llm.invoke.side_effect = Exception("LLM down")

        router = IntentRouter(reg, llm)
        result = router.classify("build something")
        assert result == "second_agent"

    def test_keyword_no_match_returns_unknown(self):
        reg = AgentRegistry()
        reg.register(MockAgent())

        llm = MagicMock()
        llm.invoke.side_effect = Exception("LLM down")

        router = IntentRouter(reg, llm)
        # Single agent returns immediately, so we need 2 agents for this test
        reg.register(SecondAgent())
        result = router.classify("xyzabc")
        # No keywords match either, but best_score is 0, so "unknown"
        assert result == "unknown"

    def test_llm_classification(self):
        """LLM returns valid JSON with agent name."""
        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.register(SecondAgent())

        llm = MagicMock()
        response = MagicMock()
        response.content = '{"agent": "second_agent"}'
        llm.invoke.return_value = response

        router = IntentRouter(reg, llm)
        result = router.classify("do something")
        assert result == "second_agent"

    def test_llm_returns_unknown_agent(self):
        """LLM returns a valid JSON but agent doesn't exist — fall back to keywords."""
        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.register(SecondAgent())

        llm = MagicMock()
        response = MagicMock()
        response.content = '{"agent": "nonexistent"}'
        llm.invoke.return_value = response

        router = IntentRouter(reg, llm)
        result = router.classify("测试一下")
        # Falls through to keyword matching since "nonexistent" not in registry
        assert result == "mock_agent"  # "测试" keyword match


# ===================================================================
#  AgentOrchestrator tests
# ===================================================================


class TestAgentOrchestrator:
    def _make_orchestrator(self, agents=None):
        reg = AgentRegistry()
        for a in (agents or [MockAgent()]):
            reg.register(a)
        llm = MagicMock()
        return AgentOrchestrator(reg, llm)

    def test_chat_routes_to_agent(self):
        orch = self._make_orchestrator()
        result = orch.chat("hello", target_agent="mock_agent")
        assert result.success is True
        assert "Echo: hello" in result.content

    def test_chat_explicit_target(self):
        orch = self._make_orchestrator([MockAgent(), SecondAgent()])
        result = orch.chat("test", target_agent="second_agent")
        assert result.agent_name == "second_agent"
        assert "Second:" in result.content

    def test_chat_agent_not_found(self):
        orch = self._make_orchestrator()
        result = orch.chat("test", target_agent="nonexistent")
        assert result.success is False
        assert "not found" in result.error.lower() or "无法识别" in result.content

    def test_chat_with_context(self):
        orch = self._make_orchestrator()
        orch.set_context("db", "test_db")
        result = orch.chat("hello", target_agent="mock_agent", context={"extra": 42})
        assert result.success is True
        assert "db" in result.metadata["context_keys"]
        assert "extra" in result.metadata["context_keys"]

    def test_chat_agent_exception_handled(self):
        orch = self._make_orchestrator([FailingAgent()])
        result = orch.chat("test", target_agent="failing_agent")
        assert result.success is False
        assert "Intentional test error" in result.error

    def test_list_agents(self):
        orch = self._make_orchestrator([MockAgent(), SecondAgent()])
        agents = orch.list_agents()
        assert len(agents) == 2

    def test_set_context(self):
        orch = self._make_orchestrator()
        orch.set_context("key1", "value1")
        result = orch.chat("test", target_agent="mock_agent")
        assert "key1" in result.metadata["context_keys"]


# ===================================================================
#  DDLAgentWrapper._parse_message tests
# ===================================================================


class TestDDLParseMessage:
    def test_extract_layer_dws(self):
        source, layer, desc = DDLAgentWrapper._parse_message(
            "请为 order_items 源表生成 DWS 层的目标表 DDL", None
        )
        assert layer == "DWS"
        assert source == "order_items"

    def test_extract_layer_ods(self):
        source, layer, _ = DDLAgentWrapper._parse_message(
            "为 `users` 表生成 ODS 层 DDL", None
        )
        assert layer == "ODS"
        assert source == "users"

    def test_extract_layer_dwd(self):
        source, layer, _ = DDLAgentWrapper._parse_message(
            "帮我把 orders 表建 DWD 层的目标表", None
        )
        assert layer == "DWD"

    def test_default_layer(self):
        _, layer, _ = DDLAgentWrapper._parse_message("建一个表", None)
        assert layer == "DWS"

    def test_context_override(self):
        ctx = {"source_table": "my_table", "target_layer": "ADS", "business_desc": "test"}
        source, layer, desc = DDLAgentWrapper._parse_message("anything", ctx)
        assert source == "my_table"
        assert layer == "ADS"
        assert desc == "test"

    def test_backtick_table(self):
        source, _, _ = DDLAgentWrapper._parse_message("为 `products` 生成 DWS DDL", None)
        assert source == "products"

    def test_source_table_pattern(self):
        source, _, _ = DDLAgentWrapper._parse_message("源表 events 生成 DWS", None)
        assert source == "events"

    def test_full_message_as_desc(self):
        msg = "为 order_items 生成 DWS 层汇总表"
        _, _, desc = DDLAgentWrapper._parse_message(msg, None)
        assert desc == msg


# ===================================================================
#  Wrapper integration tests (with mocked internal agents)
# ===================================================================


class TestSQLAgentWrapper:
    def test_wrapper_metadata(self):
        w = SQLAgentWrapper(llm=MagicMock(), db=":memory:")
        assert w.name == "sql_query"
        assert "智能问数" in w.description
        assert len(w.intent_keywords) > 5

    def test_process_with_mock(self):
        """Test that the wrapper correctly calls SQLAgent and formats result."""
        from unittest.mock import patch, MagicMock

        mock_llm = MagicMock()
        mock_db = MagicMock()

        wrapper = SQLAgentWrapper(llm=mock_llm, db=mock_db)

        # Mock the internal SQLAgent
        mock_analysis = MagicMock()
        mock_analysis.success = True
        mock_analysis.question = "test question"
        mock_analysis.sql = "SELECT COUNT(*) FROM orders"
        mock_analysis.reasoning = "需要统计订单总数"
        mock_analysis.tool_calls_log = [{"step": 1, "tool": "list_tables", "args": {}}]

        with patch("src.warehouse.sql_agent.SQLAgent") as MockSQLAgent:
            instance = MockSQLAgent.return_value
            instance.analyze.return_value = mock_analysis

            result = wrapper.process("test question")

            assert result.success is True
            assert result.agent_name == "sql_query"
            assert "SELECT COUNT(*)" in result.content
            assert result.metadata["sql"] == "SELECT COUNT(*) FROM orders"
            assert result.metadata["tool_calls"] == 1


class TestDDLAgentWrapper:
    def test_wrapper_metadata(self):
        w = DDLAgentWrapper(llm=MagicMock(), db=":memory:")
        assert w.name == "ddl_build"
        assert "智能建表" in w.description
        assert "DWS" in w.intent_keywords

    def test_process_with_mock(self):
        from unittest.mock import patch, MagicMock

        mock_llm = MagicMock()
        mock_db = MagicMock()

        wrapper = DDLAgentWrapper(llm=mock_llm, db=mock_db)

        mock_ddl_result = MagicMock()
        mock_ddl_result.success = True
        mock_ddl_result.source_table = "orders"
        mock_ddl_result.target_layer = "DWS"
        mock_ddl_result.ddl = "CREATE TABLE dws_orders (id BIGINT)"
        mock_ddl_result.verification = "OK"
        mock_ddl_result.tool_calls_log = []

        with patch("src.warehouse.ddl_agent.DDLAgent") as MockDDLAgent:
            instance = MockDDLAgent.return_value
            instance.build.return_value = mock_ddl_result

            result = wrapper.process(
                "为 orders 源表生成 DWS 层 DDL",
                context={"source_table": "orders", "target_layer": "DWS"},
            )

            assert result.success is True
            assert result.agent_name == "ddl_build"
            assert "CREATE TABLE" in result.content
            assert result.metadata["source_table"] == "orders"


# ===================================================================
#  End-to-end orchestrator test with wrappers
# ===================================================================


class TestOrchestratorEndToEnd:
    def test_full_flow_with_mock_agents(self):
        """Test the complete flow: registry → router → orchestrator."""
        from unittest.mock import patch, MagicMock

        mock_llm = MagicMock()

        # Set up LLM to return classification
        classify_response = MagicMock()
        classify_response.content = '{"agent": "mock_agent"}'
        mock_llm.invoke.return_value = classify_response

        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.register(SecondAgent())

        orch = AgentOrchestrator(reg, mock_llm)

        # Chat should route via LLM classification
        result = orch.chat("测试一下")
        assert result.success is True
        assert result.agent_name == "mock_agent"


# ===================================================================
#  GovernanceAgentWrapper tests
# ===================================================================


class TestGovernanceAgentWrapper:
    def test_wrapper_metadata(self):
        from src.agents.governance_wrapper import GovernanceAgentWrapper
        w = GovernanceAgentWrapper(llm=MagicMock(), db=":memory:")
        assert w.name == "data_governance"
        assert "数据治理" in w.description
        assert "冗余" in w.intent_keywords

    def test_keyword_routing(self):
        """Governance keywords should match in the router."""
        from src.agents.governance_wrapper import GovernanceAgentWrapper

        reg = AgentRegistry()
        reg.register(MockAgent())
        reg.register(GovernanceAgentWrapper(llm=MagicMock(), db=":memory:"))

        llm = MagicMock()
        llm.invoke.side_effect = Exception("LLM down")  # force keyword fallback

        router = IntentRouter(reg, llm)
        result = router.classify("帮我检查一下有没有冗余表")
        assert result == "data_governance"

    def test_process_with_mock(self):
        """Test that governance wrapper runs BaseAgent and returns result."""
        from src.agents.governance_wrapper import GovernanceAgentWrapper
        from unittest.mock import patch

        mock_llm = MagicMock()
        mock_db = MagicMock()

        wrapper = GovernanceAgentWrapper(llm=mock_llm, db=mock_db)

        # Mock the BaseAgent.invoke to return a fake response
        fake_response = MagicMock()
        fake_response.content = "## 治理报告\n发现 2 对冗余表"

        with patch("src.warehouse.tools.init_tool_context"), \
             patch("src.warehouse.tools.ALL_TOOLS", []), \
             patch("src.warehouse.base_agent.BaseAgent.invoke", return_value=fake_response):

            result = wrapper.process("检查冗余表")

            assert result.success is True
            assert result.agent_name == "data_governance"
            assert "冗余" in result.content
