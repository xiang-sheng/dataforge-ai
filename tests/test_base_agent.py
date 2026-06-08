"""Tests for src.warehouse.base_agent — shared ReAct loop logic."""

import pytest
from unittest.mock import MagicMock, patch

from src.warehouse.base_agent import BaseAgent, ToolCallLog


class TestToolCallLog:
    def test_dataclass(self):
        log = ToolCallLog(step=1, tool="list_tables", args={})
        assert log.step == 1
        assert log.tool == "list_tables"
        assert log.args == {}


class TestFmtArgs:
    def test_simple(self):
        assert BaseAgent._fmt_args({"name": "users"}) == "name=users"

    def test_long_value_truncated(self):
        val = "x" * 100
        result = BaseAgent._fmt_args({"data": val})
        assert "..." in result
        assert len(result) < 100

    def test_empty(self):
        assert BaseAgent._fmt_args({}) == ""

    def test_multiple(self):
        result = BaseAgent._fmt_args({"a": "1", "b": "2"})
        assert "a=1" in result
        assert "b=2" in result
