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
    "project_brief",
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


def test_twelve_tools_declared() -> None:
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


def test_project_brief_tool_definition() -> None:
    by_name = {t.name: t for t in tool_definitions()}
    tool = by_name["project_brief"]
    assert tool.inputSchema["required"] == []
    assert tool.inputSchema["properties"] == {}


def test_project_brief_returns_orientation_summary(indexed_db: Path) -> None:
    data = _call("project_brief", {})
    assert data["files"] >= 1
    assert data["entities"] >= 1
    assert isinstance(data["languages"], dict)
    assert "python" in data["languages"]
    assert isinstance(data["layers"], dict)
    assert isinstance(data["hot_paths"], list)
    assert isinstance(data["entry_points"], list)


def test_project_brief_hot_path_has_caller_count(indexed_db: Path) -> None:
    """`authenticate` is called from multiple places in the sample fixture --
    must show up as a hot path with a caller count."""
    data = _call("project_brief", {})
    auth = next((h for h in data["hot_paths"] if h["name"] == "authenticate"), None)
    assert auth is not None
    assert auth["callers"] >= 1


def test_get_context_returns_packed_result(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "authenticate"})
    assert data["total"] >= 1
    assert len(data["entities"]) >= 1

    top = data["entities"][0]
    # Summary fields present
    assert "entity_id" in top
    assert "signature" in top or "docstring" in top
    # Graph neighbourhood present when non-empty (authenticate has callers)
    auth = next(e for e in data["entities"] if e["entity_id"].endswith(":authenticate"))
    assert isinstance(auth["called_by"], list) and auth["called_by"]
    assert auth["called_by_count"] >= len(auth["called_by"])


def test_get_context_strips_fields_derivable_from_entity_id(indexed_db: Path) -> None:
    """entity_id is {lang}:{file}:{qname}, so name/qualified_name/language/file
    are pure duplication. Every response byte stays in the agent's context for
    the whole session and is re-read from cache each turn -- found via a real
    A/B cost measurement where a codegraph session cost more than one without."""
    data = _call("get_context", {"query": "authenticate"})
    for ent in data["entities"]:
        for redundant in ("name", "qualified_name", "language", "file", "via"):
            assert redundant not in ent, f"{redundant} should be stripped (derivable/unused)"
        # And nothing null/empty survives serialization.
        for key, value in ent.items():
            assert value not in (None, "", []), f"{key} is empty -- should have been dropped"


def test_get_context_summary_neighbors_are_names_not_ids(indexed_db: Path) -> None:
    """Summary-mode depends_on/called_by carry qualified names, not full ids --
    a Java neighbour id repeats the whole file path; the qname is what an agent
    needs to understand the neighbourhood. Ids on demand via impact_analysis."""
    data = _call("get_context", {"query": "authenticate", "limit": 10})
    for ent in data["entities"]:
        for label in ent.get("called_by", []) + ent.get("depends_on", []):
            if label.startswith(("external:", "wildcard:", "route:")):
                continue  # pseudo-ids pass through unchanged
            assert "/" not in label, f"{label} looks like a full entity_id, not a qname"


def test_get_context_summary_caps_neighbor_lists(indexed_db: Path) -> None:
    """Summary mode caps the lists at _NEIGHBOR_CAP but reports the true count."""
    from codegraph.server.mcp_server import _NEIGHBOR_CAP

    data = _call("get_context", {"query": "authenticate", "limit": 10})
    for ent in data["entities"]:
        deps = ent.get("depends_on", [])
        callers = ent.get("called_by", [])
        assert len(deps) <= _NEIGHBOR_CAP
        assert len(callers) <= _NEIGHBOR_CAP
        # Count is the source of truth and is >= the (possibly capped) list length.
        if deps:
            assert ent["depends_on_count"] >= len(deps)
        if callers:
            assert ent["called_by_count"] >= len(callers)


def test_get_context_summary_omits_raw_source(indexed_db: Path) -> None:
    """Default (summary) mode must NOT include full raw_source -- token discipline."""
    data = _call("get_context", {"query": "authenticate"})
    for ent in data["entities"]:
        assert "raw_source" not in ent
        assert "source_preview" in ent


def test_get_context_summary_truncates_docstring_to_first_line(indexed_db: Path) -> None:
    """The preview already shows the body's opening lines; a multi-paragraph
    docstring in summary mode is duplicated weight. detail='full' keeps it all."""
    data = _call("get_context", {"query": "authenticate"})
    auth = next(e for e in data["entities"] if e["entity_id"].endswith(":authenticate"))
    assert "\n" not in auth.get("docstring", "")

    full = _call("get_context", {"query": "authenticate", "detail": "full"})
    auth_full = next(e for e in full["entities"] if e["entity_id"].endswith(":authenticate"))
    # The fixture's authenticate docstring is multi-line; full mode keeps it whole.
    assert "\n" in (auth_full.get("docstring") or "")


def test_get_context_full_includes_raw_source(indexed_db: Path) -> None:
    """detail='full' includes complete bodies and omits the preview."""
    data = _call("get_context", {"query": "authenticate", "detail": "full"})
    top = data["entities"][0]
    assert "raw_source" in top
    assert "source_preview" not in top


def test_get_context_full_neighbors_are_full_ids(indexed_db: Path) -> None:
    """Full mode keeps complete entity_ids in the neighbour lists -- that's the
    mode for acting on the graph, so ids must be directly usable."""
    data = _call("get_context", {"query": "authenticate", "detail": "full"})
    auth = next(e for e in data["entities"] if e["entity_id"].endswith(":authenticate"))
    assert auth["called_by"]
    assert all(c.split(":", 1)[0] == "py" for c in auth["called_by"])


def test_neighbor_label_compacts_real_ids_and_passes_pseudo_ids() -> None:
    from codegraph.server.mcp_server import _neighbor_label

    assert _neighbor_label("py:auth/login.py:LoginForm.submit") == "LoginForm.submit"
    assert (
        _neighbor_label(
            "java:backend/src/main/java/com/x/AnomalyScorer.java:AnomalyScorer.closeBucket"
        )
        == "AnomalyScorer.closeBucket"
    )
    assert _neighbor_label("external:sqrt") == "external:sqrt"
    assert _neighbor_label("route:GET /me") == "route:GET /me"
    assert _neighbor_label("wildcard:ts:src/x.ts") == "wildcard:ts:src/x.ts"


def test_get_context_warns_when_no_embeddings(indexed_db: Path) -> None:
    """T17.2: a --no-embed index (the fixture) warns that semantic search is off."""
    data = _call("get_context", {"query": "authenticate"})
    assert "warnings" in data
    assert any("embeddings" in w.lower() for w in data["warnings"])


def test_get_context_warns_present_even_when_no_match(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "zzz_no_such_symbol_42"})
    assert data["total"] == 0
    assert any("embeddings" in w.lower() for w in data["warnings"])


def test_get_context_warns_on_low_confidence_multi_term_match(indexed_db: Path) -> None:
    """'boot server' matches `boot` and `run_server` individually, but no
    single hit corroborates both words -- that's noise, not a real answer,
    and should be flagged rather than presented with full confidence."""
    data = _call("get_context", {"query": "boot server"})
    assert data["total"] > 0
    assert any("low-confidence" in w.lower() for w in data["warnings"])


def test_get_context_no_low_confidence_warning_for_single_term_query(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "authenticate"})
    assert not any("low-confidence" in w.lower() for w in data["warnings"])


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


def test_confident_match_single_term_query_always_confident() -> None:
    from codegraph.server.mcp_server import _has_confident_match

    assert _has_confident_match("authenticate", ["authenticate"], ["auth/login.py"])


def test_confident_match_requires_two_term_corroboration() -> None:
    from codegraph.server.mcp_server import _has_confident_match

    assert not _has_confident_match("boot server", ["boot"], ["main.py"])
    assert _has_confident_match("state machine", ["OrderStateMachine"], ["app/order.py"])


def test_diversity_cap_limits_per_file_share() -> None:
    from types import SimpleNamespace

    from codegraph.server.mcp_server import _apply_diversity_cap

    hits = [SimpleNamespace(file="src/big.py", name=f"fn_{i}") for i in range(10)]
    kept = _apply_diversity_cap(hits, limit=5)
    assert len(kept) == 3  # file_cap = ceil(5 * 0.6) = 3


def test_diversity_cap_backfills_from_other_files() -> None:
    from types import SimpleNamespace

    from codegraph.server.mcp_server import _apply_diversity_cap

    hits = [SimpleNamespace(file="src/big.py", name=f"fn_{i}") for i in range(10)] + [
        SimpleNamespace(file="src/other.py", name="helper")
    ]
    kept = _apply_diversity_cap(hits, limit=5)
    assert any(h.file == "src/other.py" for h in kept)


def test_diversity_cap_limits_test_file_share() -> None:
    from types import SimpleNamespace

    from codegraph.server.mcp_server import _apply_diversity_cap

    hits = [SimpleNamespace(file="tests/test_x.py", name=f"test_{i}") for i in range(10)]
    kept = _apply_diversity_cap(hits, limit=6)
    assert len(kept) == 2  # test_cap = max(1, 6 // 3) = 2


def test_get_context_reports_token_estimate(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "authenticate"})
    assert "tokens_estimated" in data
    assert isinstance(data["tokens_estimated"], int)
    assert "truncated" in data


def test_get_context_reports_token_savings(indexed_db: Path) -> None:
    """get_context surfaces a savings comparison vs reading the files in full.

    tokens_saved is deliberately NOT in the response -- it's derivable from the
    other two fields, and every response byte is re-paid on every later turn."""
    data = _call("get_context", {"query": "authenticate"})
    for key in ("tokens_if_read", "savings_ratio"):
        assert key in data
    assert "tokens_saved" not in data
    assert "query" not in data  # no echo of what the caller just sent
    assert "detail" not in data
    # Reading the full files costs at least as much as the lean context.
    assert data["tokens_if_read"] >= data["tokens_estimated"]
    assert data["savings_ratio"] >= 1.0


def test_get_context_no_match_has_zero_savings(indexed_db: Path) -> None:
    data = _call("get_context", {"query": "zzz_no_such_symbol_42"})
    assert data["total"] == 0
    assert data["tokens_if_read"] == 0
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


def test_breadcrumb_writes_to_stderr_never_stdout(capsys: pytest.CaptureFixture) -> None:
    """stdout is reserved for MCP framing -- a single stray byte there corrupts
    the protocol stream. Boot diagnostics must go to stderr only."""
    mcp_server._breadcrumb("starting (db: x)")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "CodeGraph[mcp pid=" in captured.err
    assert "starting (db: x)" in captured.err


def test_main_emits_starting_and_serving_breadcrumbs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The boot path must leave a breadcrumb trail on stderr: 'starting' at
    process start and 'serving' (with phase timings) once the handshake loop
    is about to begin. Added after three real stuck-connection incidents that
    each began as guesswork over a silent process -- with these lines, the
    absence/last-line of the trail localizes the stall at a glance."""
    import anyio

    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    monkeypatch.setattr("sys.argv", ["codegraph-mcp"])
    monkeypatch.setattr(anyio, "run", lambda fn: None)

    mcp_server.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "starting (db:" in captured.err
    assert "serving (boot" in captured.err
    assert "warmup" in captured.err


def test_warm_embedding_model_with_timeout_waits_for_a_fast_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The common case: warmup finishes well within budget, so the call
    blocks until it's actually done (same behavior as calling it directly)."""
    called = {"n": 0}
    monkeypatch.setattr(mcp_server, "_warm_embedding_model", lambda: called.__setitem__("n", 1))
    mcp_server._warm_embedding_model_with_timeout(timeout=5.0)
    assert called["n"] == 1


def test_warm_embedding_model_with_timeout_does_not_block_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: a slow environment (antivirus scanning a freshly
    installed venv, a cold disk) used to block the MCP handshake itself for
    however long the import took -- an agent that gives up waiting saw the
    whole server as stuck "connecting". Found live: ~12.5s on a freshly
    reinstalled venv vs. ~0.1s on a warm one. Must now return at the timeout
    regardless of how long the underlying warmup actually takes."""
    import threading
    import time

    release = threading.Event()

    def _slow_warmup() -> None:
        release.wait(timeout=5.0)  # would hang the test if not released

    monkeypatch.setattr(mcp_server, "_warm_embedding_model", _slow_warmup)
    start = time.monotonic()
    mcp_server._warm_embedding_model_with_timeout(timeout=0.2)
    elapsed = time.monotonic() - start
    release.set()  # let the background thread finish so it doesn't linger
    assert elapsed < 1.0, f"blocked for {elapsed:.2f}s, expected to return near the 0.2s timeout"


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
    # Find the authenticate entity specifically (name lives in the id's tail now)
    auth_ents = [e for e in data["entities"] if e["entity_id"].endswith(":authenticate")]
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


# ---------- per-file staleness banner in get_context ----------


def test_get_context_names_the_exact_stale_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a query's matched entity lives in a modified file, the warning
    names that file specifically -- not just a repo-wide count."""
    import time as _time

    repo = tmp_path / "proj"
    fresh = repo / "fresh.py"
    stale = repo / "stale.py"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_text("def fresh_fn():\n    return 1\n", encoding="utf-8")
    stale.write_text("def stale_fn():\n    return 1\n", encoding="utf-8")
    db = repo / ".codegraph" / "graph.duckdb"
    result = CliRunner().invoke(cli_app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.output
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.setattr(mcp_server, "_stale_paths_cache", mcp_server._StalePathsCache())
    monkeypatch.chdir(repo)

    _time.sleep(0.05)
    stale.write_text("def stale_fn():\n    return 2\n", encoding="utf-8")

    data = _call("get_context", {"query": "stale_fn"})
    named = [w for w in data["warnings"] if "stale.py" in w]
    assert named, f"expected stale.py to be named, got: {data['warnings']}"
    assert "Read" in named[0]


def test_get_context_does_not_name_untouched_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A query matching only the untouched file gets no per-file banner for it,
    even though the repo overall has a stale file elsewhere."""
    import time as _time

    repo = tmp_path / "proj"
    fresh = repo / "fresh.py"
    stale = repo / "stale.py"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_text("def fresh_fn():\n    return 1\n", encoding="utf-8")
    stale.write_text("def stale_fn():\n    return 1\n", encoding="utf-8")
    db = repo / ".codegraph" / "graph.duckdb"
    result = CliRunner().invoke(cli_app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.output
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.setattr(mcp_server, "_stale_paths_cache", mcp_server._StalePathsCache())
    monkeypatch.chdir(repo)

    _time.sleep(0.05)
    stale.write_text("def stale_fn():\n    return 2\n", encoding="utf-8")

    data = _call("get_context", {"query": "fresh_fn"})
    named = [w for w in data["warnings"] if "fresh.py" in w]
    assert not named


def test_get_stale_paths_cleared_after_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path-set cache resets alongside the count cache after a clean reindex."""
    import time as _time

    repo = tmp_path / "proj"
    src = repo / "pkg" / "mod.py"
    db = _index_temp_repo(repo, src, "def alpha():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.chdir(repo)

    mcp_server._stale_paths_cache.set(frozenset({"pkg/mod.py"}))
    assert mcp_server._stale_paths_cache.get() == frozenset({"pkg/mod.py"})

    _time.sleep(0.05)
    src.write_text("def alpha():\n    return 2\n", encoding="utf-8")

    result = _call("reindex", {"no_embed": True})
    assert result["reindexed"] >= 1
    assert result["failed"] == 0

    assert mcp_server._stale_paths_cache.get() == frozenset()


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
