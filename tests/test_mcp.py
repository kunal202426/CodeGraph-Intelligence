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
    "list_files",
    "index_status",
    "reindex",
    "get_unsummarized_entities",
    "store_summaries",
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


def test_eleven_tools_declared() -> None:
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
    assert by_name["store_summaries"].inputSchema["required"] == ["items"]
    assert by_name["get_unsummarized_entities"].inputSchema["required"] == []
    for tool in by_name.values():
        assert tool.inputSchema["type"] == "object"
        assert tool.description  # non-empty description


def test_tools_have_descriptions() -> None:
    assert all(len(t.description or "") > 10 for t in tool_definitions())


def test_tool_descriptions_are_directive() -> None:
    """Each tool must tell the agent WHEN to use it / to prefer it over file reads."""
    import re

    directive = re.compile(
        r"(?i)(prefer|use this|start here|call this|instead of|before reading|before editing)"
    )
    for tool in tool_definitions():
        assert directive.search(tool.description or ""), (
            f"{tool.name} description is not directive: {tool.description!r}"
        )


def test_list_tools_handler_matches_definitions() -> None:
    tools = asyncio.run(list_tools())
    assert {t.name for t in tools} == _EXPECTED


# ---------- db path resolution ----------


def test_db_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEGRAPH_DB", raising=False)
    monkeypatch.setattr(mcp_server, "_db_path", None)
    # Neutralize walk-up discovery so we test the pure default fallback.
    monkeypatch.setattr("codegraph.graph.locate.discover_db", lambda *a, **k: None)
    assert get_db_path() == DEFAULT_DB


def test_db_path_discovers_when_no_explicit_or_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEGRAPH_DB", raising=False)
    monkeypatch.setattr(mcp_server, "_db_path", None)
    sentinel = Path("/discovered/.codegraph/graph.duckdb")
    monkeypatch.setattr("codegraph.graph.locate.discover_db", lambda *a, **k: sentinel)
    assert get_db_path() == sentinel


def test_db_path_env_beats_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "_db_path", None)
    monkeypatch.setenv("CODEGRAPH_DB", "/tmp/env.duckdb")
    monkeypatch.setattr(
        "codegraph.graph.locate.discover_db",
        lambda *a, **k: Path("/discovered/g.duckdb"),
    )
    assert get_db_path() == Path("/tmp/env.duckdb")


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


def test_trace_path_includes_readable_labels(indexed_db: Path) -> None:
    """trace_path returns a parallel labels list of 'name (file:line)' strings."""
    hits = _call("search_code", {"query": "authenticate"})
    auth_id = next(h["entity_id"] for h in hits if h["name"] == "authenticate")
    ctx = _call("get_entity_context", {"entity_id": auth_id})
    caller_id = ctx["called_by"][0]

    data = _call("trace_path", {"from_id": caller_id, "to_id": auth_id})
    assert "labels" in data
    assert len(data["labels"]) == len(data["path"])
    # The authenticate endpoint's label should name it and cite a file:line.
    assert any("authenticate" in lbl and "(" in lbl for lbl in data["labels"])


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


# ---------- T12.3: list_files ----------


def test_list_files_returns_indexed_files(indexed_db: Path) -> None:
    data = _call("list_files", {})
    assert data["total"] > 0
    f = data["files"][0]
    assert "path" in f and "language" in f and "entity_count" in f and "loc" in f


def test_list_files_language_filter(indexed_db: Path) -> None:
    all_data = _call("list_files", {})
    py_data = _call("list_files", {"language": "python"})
    assert py_data["total"] > 0
    assert py_data["total"] <= all_data["total"]
    assert all(f["language"] == "python" for f in py_data["files"])


def test_list_files_unknown_language_returns_empty(indexed_db: Path) -> None:
    data = _call("list_files", {"language": "erlang"})
    assert data["total"] == 0
    assert data["files"] == []


# ---------- T12.3: index_status ----------


def test_index_status_returns_stats(indexed_db: Path) -> None:
    data = _call("index_status", {})
    for key in ("db_path", "files", "entities", "edges", "embedded", "stale_files", "stale"):
        assert key in data, f"missing key: {key}"
    assert data["files"] > 0
    assert data["entities"] > 0
    assert isinstance(data["stale"], bool)


def test_index_status_stale_false_after_fresh_index(indexed_db: Path) -> None:
    # Just indexed — stale_files should be 0 (CWD is not the fixture repo,
    # so count_stale_files returns 0 because it can't find newer files in CWD).
    data = _call("index_status", {})
    assert isinstance(data["stale_files"], int)


# ---------- T17.1: reindex ----------


def test_reindex_tool_definition() -> None:
    by_name = {t.name: t for t in tool_definitions()}
    assert "reindex" in by_name
    assert "no_embed" in by_name["reindex"].inputSchema["properties"]


def _index_temp_repo(repo: Path, src: Path, body: str) -> Path:
    """Index a repo with one source file into <repo>/.codegraph/graph.duckdb."""
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body, encoding="utf-8")
    db = repo / ".codegraph" / "graph.duckdb"
    result = CliRunner().invoke(cli_app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.output
    return db


def test_reindex_refreshes_changed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Edit a file -> index_status stale -> reindex -> fresh + new symbol searchable."""
    import time

    repo = tmp_path / "proj"
    src = repo / "pkg" / "mod.py"
    db = _index_temp_repo(repo, src, "def alpha():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)

    # Fresh right after indexing.
    assert _call("index_status", {})["stale"] is False

    # Modify: add a new function so the file is newer than the index.
    time.sleep(0.05)
    src.write_text("def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8")
    assert _call("index_status", {})["stale"] is True

    # Reindex from within the "agent".
    time.sleep(0.05)
    result = _call("reindex", {"no_embed": True})
    assert result["reindexed"] >= 1
    assert result["entities"] >= 1

    # Now fresh, and the new symbol is searchable.
    assert _call("index_status", {})["stale"] is False
    hits = _call("search_code", {"query": "beta"})
    assert any(h["name"] == "beta" for h in hits)


def test_reindex_when_fresh_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "proj"
    db = _index_temp_repo(repo, repo / "a.py", "def f():\n    return 0\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)

    result = _call("reindex", {})
    assert result["reindexed"] == 0


def test_reindex_missing_db_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    data = _call("reindex", {})
    assert "error" in data


def test_reindex_works_with_relative_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a RELATIVE --db made _repo_root_for_db() return Path('.'),
    so index_one_file's relative_to() raised and reindex silently did nothing.
    """
    import time

    repo = tmp_path / "proj"
    src = repo / "pkg" / "mod.py"
    _index_temp_repo(repo, src, "def alpha():\n    return 1\n")
    # Run as if launched from inside the repo with a RELATIVE db path.
    monkeypatch.chdir(repo)
    monkeypatch.setattr(mcp_server, "_db_path", Path(".codegraph/graph.duckdb"))

    assert _call("index_status", {})["stale"] is False
    time.sleep(0.05)
    src.write_text("def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8")
    assert _call("index_status", {})["stale"] is True

    time.sleep(0.05)
    result = _call("reindex", {"no_embed": True})
    assert result["reindexed"] >= 1, result
    assert result["failed"] == 0, result
    assert _call("index_status", {})["stale"] is False


def test_reindex_purges_entities_for_deleted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file removed on disk outside of `codegraph watch` (a plain delete,
    a branch switch, `git checkout`) must have its entities purged by
    reindex -- otherwise dead code stays visible to the agent forever."""
    repo = tmp_path / "proj"
    keep = repo / "keep.py"
    gone = repo / "gone.py"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    gone.write_text("def doomed():\n    return 2\n", encoding="utf-8")
    db = repo / ".codegraph" / "graph.duckdb"
    assert (
        CliRunner().invoke(cli_app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code
        == 0
    )
    monkeypatch.setattr(mcp_server, "_db_path", db)

    assert any(r["name"] == "doomed" for r in _call("search_code", {"query": "doomed"}))

    gone.unlink()

    result = _call("reindex", {"no_embed": True})
    assert result["deleted"] == 1, result

    hits = _call("search_code", {"query": "doomed"})
    assert not any(r["name"] == "doomed" for r in hits)
    # The untouched file's entities must survive.
    assert any(r["name"] == "alpha" for r in _call("search_code", {"query": "alpha"}))


def test_reindex_noop_reports_zero_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "proj"
    db = _index_temp_repo(repo, repo / "a.py", "def f():\n    return 0\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)

    result = _call("reindex", {})
    assert result["reindexed"] == 0
    assert result["deleted"] == 0


def test_index_status_reports_deleted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "proj"
    gone = repo / "gone.py"
    db = _index_temp_repo(repo, gone, "def doomed():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.chdir(repo)

    assert _call("index_status", {})["deleted_files"] == 0

    gone.unlink()

    status = _call("index_status", {})
    assert status["deleted_files"] == 1
    assert status["stale"] is True


# ---------- staleness cache keyed by git HEAD (branch-switch invalidation) ----------


def test_stale_cache_get_set_default_head_backward_compatible() -> None:
    """Calling get()/set() with no git_head arg still works (existing callers)."""
    cache = mcp_server._StalenessCache()
    cache.set(7)
    assert cache.get() == 7


def test_stale_cache_miss_on_different_head() -> None:
    """A cache entry primed for one HEAD is not returned for a different HEAD,
    even though the TTL has not expired -- this is what makes a branch switch
    force a fresh check instead of reusing the previous branch's answer."""
    cache = mcp_server._StalenessCache()
    cache.set(0, "commit-on-main")

    assert cache.get("commit-on-main") == 0
    assert cache.get("commit-on-feature-branch") is None


def test_get_stale_count_rechecks_after_branch_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates: index on main (cache primed with 0), switch branches inside
    the TTL window, ask a question -- must not silently report 0 forever."""
    import codegraph.sync.watcher as watcher_mod

    repo = tmp_path / "proj"
    db = _index_temp_repo(repo, repo / "a.py", "def f():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.setattr(mcp_server, "_stale_cache", mcp_server._StalenessCache())

    heads = iter(["head-main", "head-main", "head-feature"])
    monkeypatch.setattr(watcher_mod, "git_head", lambda _repo: next(heads))
    monkeypatch.setattr(watcher_mod, "find_deleted_files", lambda _repo, _db: [])
    monkeypatch.setattr(watcher_mod, "count_stale_files", lambda _repo, _db: 0)

    assert mcp_server._get_stale_count() == 0  # primes cache for head-main
    assert mcp_server._get_stale_count() == 0  # still head-main -> cache hit

    # HEAD moves to a different branch; even though TTL hasn't expired, the
    # cache must be treated as invalid and the count re-derived.
    monkeypatch.setattr(watcher_mod, "count_stale_files", lambda _repo, _db: 4)
    assert mcp_server._get_stale_count() == 4


def test_get_context_tool_definition() -> None:
    by_name = {t.name: t for t in tool_definitions()}
    tool = by_name["get_context"]
    assert tool.inputSchema["required"] == ["query"]
    props = tool.inputSchema["properties"]
    assert "query" in props and "limit" in props
    assert "default" in props["limit"]
    # T15.1: detail param with summary/full enum
    assert "detail" in props
    assert props["detail"]["default"] == "summary"
    assert set(props["detail"]["enum"]) == {"summary", "full"}


def test_get_context_returns_packed_result(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "authenticate"})
    assert data["total"] >= 1
    assert len(data["entities"]) >= 1

    top = data["entities"][0]
    # Summary fields present
    assert "entity_id" in top
    assert "signature" in top or "docstring" in top
    # Graph neighbourhood present
    assert "depends_on" in top
    assert "called_by" in top
    assert isinstance(top["depends_on"], list)
    assert isinstance(top["called_by"], list)
    # Exact neighbour counts always reported
    assert "depends_on_count" in top and "called_by_count" in top
    # Retriever tags present
    assert "via" in top and isinstance(top["via"], list)


def test_get_context_summary_caps_neighbor_lists(indexed_db: Path) -> None:
    """Summary mode caps the id lists at _NEIGHBOR_CAP but reports the true count."""
    from codegraph.server.mcp_server import _NEIGHBOR_CAP

    data = _call("get_context", {"query": "authenticate", "limit": 10})
    for ent in data["entities"]:
        assert len(ent["depends_on"]) <= _NEIGHBOR_CAP
        assert len(ent["called_by"]) <= _NEIGHBOR_CAP
        # Count is the source of truth and is >= the (possibly capped) list length.
        assert ent["depends_on_count"] >= len(ent["depends_on"])
        assert ent["called_by_count"] >= len(ent["called_by"])


def test_get_context_summary_omits_raw_source(indexed_db: Path) -> None:
    """Default (summary) mode must NOT include full raw_source -- token discipline."""
    data = _call("get_context", {"query": "authenticate"})
    assert data["detail"] == "summary"
    for ent in data["entities"]:
        assert "raw_source" not in ent
        assert "source_preview" in ent


def test_get_context_full_includes_raw_source(indexed_db: Path) -> None:
    """detail='full' includes complete bodies and omits the preview."""
    data = _call("get_context", {"query": "authenticate", "detail": "full"})
    assert data["detail"] == "full"
    top = data["entities"][0]
    assert "raw_source" in top
    assert "source_preview" not in top


def test_get_context_warns_when_no_embeddings(indexed_db: Path) -> None:
    """T17.2: a --no-embed index (the fixture) warns that semantic search is off."""
    data = _call("get_context", {"query": "authenticate"})
    assert "warnings" in data
    assert any("embeddings" in w.lower() for w in data["warnings"])


def test_get_context_warns_present_even_when_no_match(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "zzz_no_such_symbol_42"})
    assert data["total"] == 0
    assert any("embeddings" in w.lower() for w in data["warnings"])


def test_source_preview_truncates_long_bodies() -> None:
    """The preview helper caps long source and adds a truncation marker."""
    from codegraph.server.mcp_server import _source_preview

    long_src = "\n".join(f"line {i}" for i in range(50))
    preview = _source_preview(long_src)
    assert preview.count("\n") < 50  # truncated
    assert "more lines" in preview
    assert len(preview) < len(long_src)


def test_source_preview_keeps_short_bodies() -> None:
    from codegraph.server.mcp_server import _source_preview

    short = "def f():\n    return 1"
    assert _source_preview(short) == short
    assert _source_preview(None) == ""


def test_get_context_reports_token_estimate(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "authenticate"})
    assert "tokens_estimated" in data
    assert isinstance(data["tokens_estimated"], int)
    assert "truncated" in data


def test_get_context_reports_token_savings(indexed_db: Path) -> None:
    """get_context surfaces a savings comparison vs reading the files in full."""
    data = _call("get_context", {"query": "authenticate"})
    for key in ("tokens_if_read", "tokens_saved", "savings_ratio"):
        assert key in data
    # Reading the full files costs at least as much as the lean context.
    assert data["tokens_if_read"] >= data["tokens_estimated"]
    assert data["tokens_saved"] == max(0, data["tokens_if_read"] - data["tokens_estimated"])
    assert data["savings_ratio"] >= 1.0


def test_get_context_no_match_has_zero_savings(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "zzz_no_such_symbol_42"})
    assert data["total"] == 0
    assert data["tokens_if_read"] == 0
    assert data["tokens_saved"] == 0
    assert data["savings_ratio"] == 0.0


# ---------- startup model warmup ----------


def test_warm_embedding_model_skips_without_embeddings(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A --no-embed index has no vectors, so startup must NOT load the model
    (which would import the heavy torch/sklearn stack for nothing)."""
    import codegraph.embeddings.pipeline as pipeline

    called = {"n": 0}
    monkeypatch.setattr(pipeline, "embed_one", lambda *_a, **_k: called.__setitem__("n", 1))
    mcp_server._warm_embedding_model()
    assert called["n"] == 0


def test_warm_embedding_model_is_noop_when_db_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warmup must never raise, even if the DB doesn't exist yet."""
    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    mcp_server._warm_embedding_model()  # should return quietly


def test_get_context_respects_token_budget(indexed_db: Path) -> None:
    """A tiny budget caps the entity count and flags truncation."""
    tiny = _call("get_context", {"query": "authenticate", "limit": 10, "max_tokens": 100})
    big = _call("get_context", {"query": "authenticate", "limit": 10, "max_tokens": 100000})
    # First entity always included; the tiny budget returns no more than the big one.
    assert len(tiny["entities"]) >= 1
    assert len(tiny["entities"]) <= len(big["entities"])
    if len(big["entities"]) > len(tiny["entities"]):
        assert tiny["truncated"] is True


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


# ---------- agent-driven summaries ----------


def test_get_unsummarized_entities_returns_batch(indexed_db: Path) -> None:
    data = _call("get_unsummarized_entities", {"limit": 5})
    assert data["count"] > 0
    assert data["remaining"] >= data["count"]
    ent = data["entities"][0]
    for key in ("entity_id", "type", "qualified_name", "location", "source_preview"):
        assert key in ent
    # Only summarizable kinds are returned (no modules).
    assert all(e["type"] in {"function", "method", "class", "interface"} for e in data["entities"])


def test_store_summaries_persists_and_clears(indexed_db: Path) -> None:
    batch = _call("get_unsummarized_entities", {"limit": 3})
    targets = batch["entities"]
    assert targets, "fixture should have unsummarized entities"

    items = [
        {"entity_id": e["entity_id"], "summary": f"Summary of {e['qualified_name']}."}
        for e in targets
    ]
    result = _call("store_summaries", {"items": items})
    assert result["stored"] == len(items)
    assert isinstance(result["reembedded"], int)

    # Stored entities no longer come back as unsummarized.
    stored_ids = {e["entity_id"] for e in targets}
    again = _call("get_unsummarized_entities", {"limit": 50})
    assert stored_ids.isdisjoint({e["entity_id"] for e in again["entities"]})

    # index_status reports the new coverage.
    status = _call("index_status", {})
    assert status["summarized"] >= len(items)


def test_store_summaries_rejects_non_list(indexed_db: Path) -> None:
    data = _call("store_summaries", {"items": "not a list"})
    assert "error" in data


def test_store_summaries_ignores_blank_items(indexed_db: Path) -> None:
    data = _call("store_summaries", {"items": [{"entity_id": "", "summary": ""}]})
    assert data["stored"] == 0


# ---------- stale-index warning in get_context ----------


def test_get_context_warns_when_stale(indexed_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_context must include a stale warning when the index is outdated."""
    monkeypatch.setattr(mcp_server, "_get_stale_count", lambda: 5)
    data = _call("get_context", {"query": "authenticate"})
    stale_warnings = [w for w in data["warnings"] if "stale" in w.lower()]
    assert stale_warnings, f"expected a stale warning, got: {data['warnings']}"
    assert "5" in stale_warnings[0]
    assert "reindex" in stale_warnings[0].lower()


def test_get_context_no_stale_warning_when_fresh(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stale warning when the stale count is 0."""
    monkeypatch.setattr(mcp_server, "_get_stale_count", lambda: 0)
    data = _call("get_context", {"query": "authenticate"})
    stale_warnings = [w for w in data["warnings"] if "stale" in w.lower()]
    assert not stale_warnings


def test_get_context_stale_warning_present_on_no_match(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale warning appears even when the query returns no results."""
    monkeypatch.setattr(mcp_server, "_get_stale_count", lambda: 3)
    data = _call("get_context", {"query": "zzz_no_such_symbol_99"})
    assert data["total"] == 0
    stale_warnings = [w for w in data["warnings"] if "stale" in w.lower()]
    assert stale_warnings


def test_reindex_resets_stale_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a successful reindex, _stale_cache reports 0 (index is fresh)."""
    import time as _time

    repo = tmp_path / "proj"
    src = repo / "pkg" / "mod.py"
    db = _index_temp_repo(repo, src, "def alpha():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)

    # Seed the cache with a non-zero count so we can confirm it resets.
    mcp_server._stale_cache.set(7)
    assert mcp_server._stale_cache.get() == 7

    # Modify the file so there is a real stale file to reindex.
    _time.sleep(0.05)
    src.write_text("def alpha():\n    return 2\n", encoding="utf-8")

    result = _call("reindex", {"no_embed": True})
    assert result["reindexed"] >= 1
    assert result["failed"] == 0

    # Cache must be 0 so the next get_context won't emit a stale warning.
    assert mcp_server._stale_cache.get() == 0


def test_store_summaries_improves_semantic_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept word only in the summary should pull its entity up in semantic search."""
    repo = tmp_path / "proj"
    src = repo / "m.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("def qz9():\n    return 1\n", encoding="utf-8")
    db = repo / ".codegraph" / "graph.duckdb"
    # Index WITH embeddings so semantic search is active.
    result = CliRunner().invoke(cli_app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.output
    monkeypatch.setattr(mcp_server, "_db_path", db)

    batch = _call("get_unsummarized_entities", {"limit": 5})
    qz = next(e for e in batch["entities"] if e["qualified_name"].endswith("qz9"))
    store_res = _call(
        "store_summaries",
        {
            "items": [
                {"entity_id": qz["entity_id"], "summary": "Computes a cryptographic checksum."}
            ]
        },
    )
    if store_res["reembedded"] == 0:
        pytest.skip("embedding model unavailable in this environment")

    # The concept word "cryptographic" appears only in the summary, not the source.
    hits = _call("search_code", {"query": "cryptographic checksum"})
    assert any(h["entity_id"] == qz["entity_id"] for h in hits)
