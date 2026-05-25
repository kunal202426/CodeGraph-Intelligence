"""Tests for T7.1 — MCP server skeleton (tool declarations + db-path resolution)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from codegraph.server import mcp_server
from codegraph.server.mcp_server import (
    DEFAULT_DB,
    get_db_path,
    list_tools,
    tool_definitions,
)

_EXPECTED = {"search_code", "get_entity_context", "impact_analysis", "ask_codebase"}


def test_four_tools_declared() -> None:
    tools = tool_definitions()
    assert {t.name for t in tools} == _EXPECTED


def test_each_tool_has_object_schema_with_required() -> None:
    by_name = {t.name: t for t in tool_definitions()}
    assert by_name["search_code"].inputSchema["required"] == ["query"]
    assert by_name["get_entity_context"].inputSchema["required"] == ["entity_id"]
    assert by_name["impact_analysis"].inputSchema["required"] == ["entity_id"]
    assert by_name["ask_codebase"].inputSchema["required"] == ["query"]
    for tool in by_name.values():
        assert tool.inputSchema["type"] == "object"
        assert tool.description  # non-empty description


def test_tools_have_descriptions() -> None:
    assert all(len(t.description or "") > 10 for t in tool_definitions())


def test_list_tools_handler_matches_definitions() -> None:
    tools = asyncio.run(list_tools())
    assert {t.name for t in tools} == _EXPECTED


# ---------- db path resolution ----------


def test_db_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEGRAPH_DB", raising=False)
    monkeypatch.setattr(mcp_server, "_db_path", None)
    assert get_db_path() == DEFAULT_DB


def test_db_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "_db_path", None)
    monkeypatch.setenv("CODEGRAPH_DB", "/tmp/custom.duckdb")
    assert get_db_path() == Path("/tmp/custom.duckdb")


def test_db_path_explicit_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEGRAPH_DB", "/tmp/env.duckdb")
    monkeypatch.setattr(mcp_server, "_db_path", Path("/tmp/explicit.duckdb"))
    assert get_db_path() == Path("/tmp/explicit.duckdb")
