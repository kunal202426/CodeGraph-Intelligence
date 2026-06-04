"""Tests for T7.1/T7.2 — MCP server skeleton + tool wiring."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from codegraph.cli import app as cli_app
from codegraph.server import mcp_server
from codegraph.server.mcp_server import (
    DEFAULT_DB,
    call_tool,
    get_db_path,
    list_tools,
    tool_definitions,
)
from typer.testing import CliRunner

_EXPECTED = {
    "search_code",
    "get_entity_context",
    "impact_analysis",
    "ask_codebase",
    "get_context",
    "trace_path",
}
SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


@pytest.fixture
def indexed_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Index the sample repo (--no-embed) and point the MCP server at it."""
    db = tmp_path / "g.duckdb"
    assert (
        CliRunner()
        .invoke(cli_app, ["index", str(SAMPLE_REPO), "--db", str(db), "--no-embed"])
        .exit_code
        == 0
    )
    monkeypatch.setattr(mcp_server, "_db_path", db)
    return db


def test_five_tools_declared() -> None:
    tools = tool_definitions()
    assert {t.name for t in tools} == _EXPECTED


def test_each_tool_has_object_schema_with_required() -> None:
    by_name = {t.name: t for t in tool_definitions()}
    assert by_name["search_code"].inputSchema["required"] == ["query"]
    assert by_name["get_entity_context"].inputSchema["required"] == ["entity_id"]
    assert by_name["impact_analysis"].inputSchema["required"] == ["entity_id"]
    assert by_name["ask_codebase"].inputSchema["required"] == ["query"]
    assert by_name["get_context"].inputSchema["required"] == ["query"]
    assert by_name["trace_path"].inputSchema["required"] == ["from_id", "to_id"]
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


# ---------- T7.2: wired tools ----------


def _call(name: str, args: dict) -> dict | list:
    out = asyncio.run(call_tool(name, args))
    assert len(out) == 1 and out[0].type == "text"
    return json.loads(out[0].text)


def test_search_code_tool(indexed_db: Path) -> None:
    results = _call("search_code", {"query": "authenticate"})
    assert any(r["name"] == "authenticate" for r in results)


def test_get_entity_context_tool(indexed_db: Path) -> None:
    eid = next(r["entity_id"] for r in _call("search_code", {"query": "authenticate"}))
    ctx = _call("get_entity_context", {"entity_id": eid})
    assert ctx["entity"]["entity_id"] == eid
    assert "depends_on" in ctx and "called_by" in ctx
    assert ctx["called_by"]  # authenticate is called by submit/login_handler/boot


def test_get_entity_context_unknown(indexed_db: Path) -> None:
    ctx = _call("get_entity_context", {"entity_id": "py:nope.py:ghost"})
    assert "error" in ctx


def test_impact_analysis_tool(indexed_db: Path) -> None:
    eid = next(r["entity_id"] for r in _call("search_code", {"query": "authenticate"}))
    data = _call("impact_analysis", {"entity_id": eid})
    assert data["root"] == eid
    assert data["total"] >= 1


def test_ask_codebase_without_embeddings(indexed_db: Path) -> None:
    # Indexed with --no-embed → ask should report the missing embeddings, no API call.
    data = _call("ask_codebase", {"query": "how does login work?"})
    assert "error" in data
    assert "embeddings" in data["error"].lower()


def test_unknown_tool_raises(indexed_db: Path) -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        asyncio.run(call_tool("nope", {}))


def test_missing_db_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    data = _call("search_code", {"query": "x"})
    assert "error" in data


# ---------- T12.1: get_context ----------


# ---------- T12.2: trace_path ----------


def test_trace_path_tool_definition() -> None:
    by_name = {t.name: t for t in tool_definitions()}
    tool = by_name["trace_path"]
    assert "from_id" in tool.inputSchema["properties"]
    assert "to_id" in tool.inputSchema["properties"]
    assert tool.inputSchema["required"] == ["from_id", "to_id"]


def test_trace_path_direct_call(indexed_db: Path) -> None:
    """A caller of authenticate should reach authenticate in 1 hop."""
    # Find the authenticate entity and one of its callers via search.
    hits = _call("search_code", {"query": "authenticate"})
    auth_id = next(h["entity_id"] for h in hits if h["name"] == "authenticate")

    # Retrieve direct callers from get_entity_context.
    ctx = _call("get_entity_context", {"entity_id": auth_id})
    callers = ctx["called_by"]
    assert callers, "need at least one caller to test trace_path"

    caller_id = callers[0]
    data = _call("trace_path", {"from_id": caller_id, "to_id": auth_id})
    assert data["found"] is True
    assert data["hops"] == 1
    assert data["path"] == [caller_id, auth_id]


def test_trace_path_same_entity_zero_hops(indexed_db: Path) -> None:
    """from_id == to_id should return a single-element path with 0 hops."""
    hits = _call("search_code", {"query": "authenticate"})
    auth_id = next(h["entity_id"] for h in hits if h["name"] == "authenticate")

    data = _call("trace_path", {"from_id": auth_id, "to_id": auth_id})
    assert data["found"] is True
    assert data["hops"] == 0
    assert data["path"] == [auth_id]


def test_trace_path_not_found(indexed_db: Path) -> None:
    """Unrelated entities should return found=False."""
    hits = _call("search_code", {"query": "authenticate"})
    auth_id = next(h["entity_id"] for h in hits if h["name"] == "authenticate")

    # Try to reach authenticate *from* itself via a non-existent path in the
    # reverse direction (authenticate → caller) — BFS is directed so this
    # should not be reachable.
    ctx = _call("get_entity_context", {"entity_id": auth_id})
    callers = ctx["called_by"]
    if not callers:
        pytest.skip("no callers in fixture")

    caller_id = callers[0]
    # Reversed direction: authenticate → caller is not a call edge.
    data = _call("trace_path", {"from_id": auth_id, "to_id": caller_id})
    assert data["found"] is False
    assert data["path"] == []


def test_get_context_tool_definition() -> None:
    by_name = {t.name: t for t in tool_definitions()}
    tool = by_name["get_context"]
    assert tool.inputSchema["required"] == ["query"]
    props = tool.inputSchema["properties"]
    assert "query" in props and "limit" in props
    assert "default" in props["limit"]


def test_get_context_returns_packed_result(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "authenticate"})
    assert data["total"] >= 1
    assert len(data["entities"]) >= 1

    top = data["entities"][0]
    # Full entity fields present
    assert "entity_id" in top
    assert "raw_source" in top
    assert "signature" in top or "docstring" in top
    # Graph neighbourhood present
    assert "depends_on" in top
    assert "called_by" in top
    assert isinstance(top["depends_on"], list)
    assert isinstance(top["called_by"], list)
    # Retriever tags present
    assert "via" in top and isinstance(top["via"], list)


def test_get_context_authenticate_has_callers(indexed_db: Path) -> None:
    """The authenticate function is called by other entities in the fixture."""
    data = _call("get_context", {"query": "authenticate"})
    # Find the authenticate entity specifically
    auth_ents = [e for e in data["entities"] if e.get("name") == "authenticate"]
    assert auth_ents, "authenticate should appear in get_context results"
    assert auth_ents[0]["called_by"], "authenticate must have at least one caller"


def test_get_context_no_match_returns_empty(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "zzz_does_not_exist_9999"})
    assert data["total"] == 0
    assert data["entities"] == []


def test_get_context_limit_respected(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "def", "limit": 2})
    assert len(data["entities"]) <= 2
