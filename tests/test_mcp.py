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


def test_twelve_tools_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tools = tool_definitions()
    assert {t.name for t in tools} == _EXPECTED


def test_ask_codebase_omitted_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """ask_codebase always errors without ANTHROPIC_API_KEY (see ai/llm.py) --
    advertising it anyway would cost real schema tokens every session for a
    tool that can't work, and risk a wasted round-trip if tried. Must not be
    listed when the key is absent (the common case for a Claude-Code-only
    user with no separate Anthropic API key)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tools = tool_definitions()
    assert "ask_codebase" not in {t.name for t in tools}
    assert {t.name for t in tools} == _EXPECTED - {"ask_codebase"}


def test_each_tool_has_object_schema_with_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    by_name = {t.name: t for t in tool_definitions()}
    assert by_name["search_code"].inputSchema["required"] == ["query"]
    assert by_name["get_entity_context"].inputSchema["required"] == ["entity_id"]
    # entity_id/query are alternatives (either resolves the target) -- neither
    # is unconditionally required at the schema level; _impact_analysis
    # validates at runtime that at least one was given.
    assert by_name["impact_analysis"].inputSchema["required"] == []
    assert by_name["ask_codebase"].inputSchema["required"] == ["query"]
    assert by_name["get_context"].inputSchema["required"] == ["query"]
    # Same story: from_id/from_query and to_id/to_query are each alternatives.
    assert by_name["trace_path"].inputSchema["required"] == []
    assert by_name["store_summaries"].inputSchema["required"] == ["items"]
    assert by_name["get_unsummarized_entities"].inputSchema["required"] == []
    for tool in by_name.values():
        assert tool.inputSchema["type"] == "object"
        assert tool.description  # non-empty description


def test_tools_have_descriptions() -> None:
    assert all(len(t.description or "") > 10 for t in tool_definitions())


def test_impact_analysis_description_clarifies_call_edges_only() -> None:
    """Found via real manual testing on Grafana: impact_analysis only tracks
    call edges, so a struct/type (which has no callers, only field
    references) always resolves to total=0 -- correct, but easy to
    misread as "safe to change" rather than "not visible to this tool"."""
    tool = {t.name: t for t in tool_definitions()}["impact_analysis"]
    assert "no callers" in tool.description or "call edges" in tool.description


def test_impact_analysis_description_says_prefer_it_over_grep() -> None:
    """Real finding, live on Grafana (round 8 of the cost A/B): a task whose entire
    job was "find every call site of this function" called impact_analysis ZERO
    times, running ~30 native greps instead alongside a handful of get_context
    calls. search_code's description already says "Prefer this over grep" for
    finding entities in the first place; impact_analysis needs the same explicit
    framing for the follow-up question ("who else calls/uses this"), since that's
    exactly the shape of question it exists to answer in one call instead of a
    chain of greps."""
    tool = {t.name: t for t in tool_definitions()}["impact_analysis"]
    assert "grep" in tool.description.lower()


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


def test_list_tools_handler_matches_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
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
    assert data["mode"] == "callers"


def test_impact_analysis_tool_definition_has_query_property() -> None:
    tool = {t.name: t for t in tool_definitions()}["impact_analysis"]
    assert "query" in tool.inputSchema["properties"]
    assert "entity_id" in tool.inputSchema["properties"]


def test_impact_analysis_by_query_resolves_and_analyzes(indexed_db: Path) -> None:
    """query is an alternative to entity_id -- collapses search_code + impact_analysis
    (2 round-trips) into one call."""
    eid = next(r["entity_id"] for r in _call("search_code", {"query": "authenticate"}))
    data = _call("impact_analysis", {"query": "authenticate"})
    assert data["root"] == eid
    assert data["total"] >= 1


def test_impact_analysis_entity_id_wins_when_both_given(indexed_db: Path) -> None:
    eid = next(r["entity_id"] for r in _call("search_code", {"query": "authenticate"}))
    data = _call("impact_analysis", {"entity_id": eid, "query": "totally unrelated nonsense"})
    assert data["root"] == eid


def test_impact_analysis_missing_entity_id_and_query_errors(indexed_db: Path) -> None:
    data = _call("impact_analysis", {})
    assert "error" in data


def test_impact_analysis_query_no_match_errors(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "hybrid_search", lambda *a, **k: [])
    data = _call("impact_analysis", {"query": "anything"})
    assert "error" in data


def test_impact_analysis_on_a_type_returns_usages_not_callers(indexed_db: Path) -> None:
    """A struct/class/interface has no call-graph callers -- find_callers only
    walks call edges, so it's structurally blind to field/param/return-type
    references. Regression for the real gap found via manual testing on
    Grafana: a 0-callers result on a type read as "safe to change" when it
    just meant "not visible to this tool". validators.py's validate_form
    takes a LoginForm param, so LoginForm now resolves to real usages."""
    login_form_id = next(
        h["entity_id"]
        for h in _call("search_code", {"query": "LoginForm"})
        if h["name"] == "LoginForm"
    )
    data = _call("impact_analysis", {"entity_id": login_form_id})
    assert data["mode"] == "type_usages"
    assert data["total"] >= 1
    assert any(u["name"] == "validate_form" for u in data["usages"])


def test_impact_analysis_query_low_confidence_gets_warning(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "_has_confident_match", lambda *a, **k: False)
    data = _call("impact_analysis", {"query": "authenticate"})
    assert data.get("warnings")
    assert any("low-confidence" in w.lower() for w in data["warnings"])


def test_impact_analysis_tool_definition_has_field_property() -> None:
    tool = {t.name: t for t in tool_definitions()}["impact_analysis"]
    assert "field" in tool.inputSchema["properties"]


def _index_temp_repo_multi(repo: Path, files: dict[str, str]) -> Path:
    """Index a repo with several source files into <repo>/.codegraph/graph.duckdb."""
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    db = repo / ".codegraph" / "graph.duckdb"
    result = CliRunner().invoke(cli_app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.output
    return db


def test_impact_analysis_field_finds_construction_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real gap found via a live A/B on Grafana (cost/efficiency findings, round 9): an
    agent tightened a numeric-field validation (interval must be >= 10s) without
    checking that a test-fixture generator elsewhere constructed that same field with
    random values as low as 1 -- turning 15+ store tests newly flaky. Neither
    search_code (name/docstring only) nor the existing type_usages mode (signature
    text only) can see a field being SET inside a function body. A field usage search
    -- regex over raw_source, same mechanism as type_usages but a different column --
    is exactly "who constructs/sets this field", the question that gap needed
    answered before the edit, not after."""
    repo = tmp_path / "proj"
    db = _index_temp_repo_multi(
        repo,
        {
            "models.py": (
                "class AlertRule:\n"
                "    def __init__(self, interval_seconds: int):\n"
                "        self.interval_seconds = interval_seconds\n"
            ),
            "testing.py": (
                "import random\n"
                "from models import AlertRule\n\n"
                "def generate_alert_rule() -> AlertRule:\n"
                "    return AlertRule(interval_seconds=random.randint(1, 60))\n"
            ),
            "unrelated.py": "def totally_unrelated():\n    return 42\n",
        },
    )
    monkeypatch.setattr(mcp_server, "_db_path", db)

    alert_rule_id = next(
        h["entity_id"]
        for h in _call("search_code", {"query": "AlertRule"})
        if h["name"] == "AlertRule"
    )
    data = _call("impact_analysis", {"entity_id": alert_rule_id, "field": "interval_seconds"})

    assert data["mode"] == "field_usages"
    assert data["field"] == "interval_seconds"
    assert data["total"] >= 1
    names = [u["name"] for u in data["usages"]]
    assert "generate_alert_rule" in names
    assert "totally_unrelated" not in names


def test_impact_analysis_field_excludes_same_named_field_on_an_unrelated_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real precision problem found against a live ~97k-entity index (Grafana): a
    bare field-name search for 'IntervalSeconds' returned 217 hits dominated by an
    unrelated struct (SyncOptions) that happens to share the field name -- a common
    field name is not a rare token at real scale. Requiring the containing type's
    own name to also appear in the body (true of any real construction site) is
    what makes this usable signal instead of noise."""
    repo = tmp_path / "proj"
    db = _index_temp_repo_multi(
        repo,
        {
            "models.py": (
                "class AlertRule:\n"
                "    def __init__(self, interval_seconds: int):\n"
                "        self.interval_seconds = interval_seconds\n\n\n"
                "class SyncOptions:\n"
                "    def __init__(self, interval_seconds: int):\n"
                "        self.interval_seconds = interval_seconds\n"
            ),
            "testing.py": (
                "from models import AlertRule, SyncOptions\n\n"
                "def make_alert_rule() -> AlertRule:\n"
                "    return AlertRule(interval_seconds=5)\n\n\n"
                "def make_sync_options() -> SyncOptions:\n"
                "    return SyncOptions(interval_seconds=99)\n"
            ),
        },
    )
    monkeypatch.setattr(mcp_server, "_db_path", db)

    alert_rule_id = next(
        h["entity_id"]
        for h in _call("search_code", {"query": "AlertRule"})
        if h["name"] == "AlertRule"
    )
    data = _call("impact_analysis", {"entity_id": alert_rule_id, "field": "interval_seconds"})

    names = [u["name"] for u in data["usages"]]
    assert "make_alert_rule" in names
    assert "make_sync_options" not in names


def test_impact_analysis_field_via_query_not_just_entity_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """field combines with query the same way plain impact_analysis does -- resolve
    the containing type by name/phrase, no separate search_code round-trip needed."""
    repo = tmp_path / "proj"
    db = _index_temp_repo_multi(
        repo,
        {
            "models.py": (
                "class AlertRule:\n"
                "    def __init__(self, interval_seconds: int):\n"
                "        self.interval_seconds = interval_seconds\n"
            ),
            "testing.py": (
                "from models import AlertRule\n\n"
                "def make_rule() -> AlertRule:\n"
                "    return AlertRule(interval_seconds=1)\n"
            ),
        },
    )
    monkeypatch.setattr(mcp_server, "_db_path", db)

    data = _call("impact_analysis", {"query": "AlertRule", "field": "interval_seconds"})
    assert data["mode"] == "field_usages"
    assert any(u["name"] == "make_rule" for u in data["usages"])


def test_impact_analysis_field_on_a_function_entity_errors(indexed_db: Path) -> None:
    """A field only makes sense on a struct/class/interface -- a function has no
    fields, so passing one against a function entity is a caller mistake, not a
    valid "zero usages" result. Fail loudly instead of silently returning nothing."""
    eid = next(r["entity_id"] for r in _call("search_code", {"query": "authenticate"}))
    data = _call("impact_analysis", {"entity_id": eid, "field": "whatever"})
    assert "error" in data


def test_impact_analysis_without_field_is_unaffected(indexed_db: Path) -> None:
    """Regression guard: the new optional `field` parameter must not change the
    default callers/type_usages behavior when omitted."""
    eid = next(r["entity_id"] for r in _call("search_code", {"query": "authenticate"}))
    data = _call("impact_analysis", {"entity_id": eid})
    assert data["mode"] == "callers"
    assert "field" not in data


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
    assert "from_query" in tool.inputSchema["properties"]
    assert "to_query" in tool.inputSchema["properties"]
    assert tool.inputSchema["required"] == []


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


def test_trace_path_by_query_resolves_and_traces(indexed_db: Path) -> None:
    """from_query/to_query are alternatives to from_id/to_id -- collapses
    search_code x2 + trace_path (3 round-trips) into one call. main.py's
    boot() calls authenticate() directly (see tests/fixtures/sample_repo_py)."""
    auth_id = next(
        h["entity_id"]
        for h in _call("search_code", {"query": "authenticate"})
        if h["name"] == "authenticate"
    )
    boot_id = next(
        h["entity_id"] for h in _call("search_code", {"query": "boot"}) if h["name"] == "boot"
    )

    data = _call("trace_path", {"from_query": "boot", "to_query": "authenticate"})
    assert data["found"] is True
    assert data["from_id"] == boot_id
    assert data["to_id"] == auth_id
    assert data["hops"] == 1


def test_trace_path_mixes_id_and_query(indexed_db: Path) -> None:
    """One side pre-resolved, the other given as a query."""
    auth_id = next(
        h["entity_id"]
        for h in _call("search_code", {"query": "authenticate"})
        if h["name"] == "authenticate"
    )
    data = _call("trace_path", {"from_query": "boot", "to_id": auth_id})
    assert data["found"] is True
    assert data["to_id"] == auth_id


def test_trace_path_missing_from_errors(indexed_db: Path) -> None:
    auth_id = next(
        h["entity_id"]
        for h in _call("search_code", {"query": "authenticate"})
        if h["name"] == "authenticate"
    )
    data = _call("trace_path", {"to_id": auth_id})
    assert "error" in data


def test_trace_path_missing_to_errors(indexed_db: Path) -> None:
    auth_id = next(
        h["entity_id"]
        for h in _call("search_code", {"query": "authenticate"})
        if h["name"] == "authenticate"
    )
    data = _call("trace_path", {"from_id": auth_id})
    assert "error" in data


def test_trace_path_query_no_match_errors(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "hybrid_search", lambda *a, **k: [])
    data = _call("trace_path", {"from_query": "anything", "to_query": "anything"})
    assert "error" in data


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
    the TTL window, ask a question -- must not silently report 0 forever.

    The recheck is asynchronous (see the non-blocking tests below), so the
    call that observes the new HEAD schedules the walk and the *next* call
    sees the refreshed number."""
    import codegraph.sync.watcher as watcher_mod

    repo = tmp_path / "proj"
    db = _index_temp_repo(repo, repo / "a.py", "def f():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.setattr(mcp_server, "_stale_cache", mcp_server._StalenessCache())
    monkeypatch.setattr(mcp_server, "_stale_paths_cache", mcp_server._StalePathsCache())

    head = {"value": "head-main"}
    monkeypatch.setattr(watcher_mod, "git_head", lambda _repo: head["value"])
    monkeypatch.setattr(watcher_mod, "find_deleted_files", lambda _repo, _db: [])
    monkeypatch.setattr(watcher_mod, "find_stale_files", lambda _repo, _db: [])

    mcp_server._refresh_staleness_now()  # primes cache for head-main
    assert mcp_server._get_stale_count() == 0
    assert mcp_server._get_stale_count() == 0  # still head-main -> cache hit

    # HEAD moves to a different branch; even though TTL hasn't expired, the
    # cache must be treated as invalid and the count re-derived.
    head["value"] = "head-feature"
    monkeypatch.setattr(
        watcher_mod, "find_stale_files", lambda _repo, _db: [repo / f"s{i}.py" for i in range(4)]
    )
    mcp_server._refresh_staleness_now()
    assert mcp_server._get_stale_count() == 4


# ---------- staleness must never block a tool call ----------


def test_get_stale_count_does_not_block_on_a_cold_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug that made get_context look frozen on a large repo.

    Staleness is derived from a full directory walk. On a 16k-file repo that
    measured 225 SECONDS -- and `_get_context` called it synchronously, twice
    (count + paths), on a cold cache, while the boot thread ran the same walk
    concurrently. The cache docstring's "10-50ms per call" estimate only ever
    held for small repos.

    A staleness warning is best-effort metadata; it must never gate an answer.
    A cold cache reports "not stale yet" immediately and schedules the walk in
    the background, so the *next* call carries the warning."""
    import time

    import codegraph.sync.watcher as watcher_mod

    repo = tmp_path / "proj"
    db = _index_temp_repo(repo, repo / "a.py", "def f():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.setattr(mcp_server, "_stale_cache", mcp_server._StalenessCache())
    monkeypatch.setattr(mcp_server, "_stale_paths_cache", mcp_server._StalePathsCache())

    import threading

    release = threading.Event()

    def glacial_walk(_repo: Path, _db: Path) -> list[Path]:
        release.wait(timeout=30)
        return []

    monkeypatch.setattr(mcp_server, "_staleness_refresh_running", False)
    monkeypatch.setattr(watcher_mod, "git_head", lambda _repo: "head")
    monkeypatch.setattr(watcher_mod, "find_stale_files", glacial_walk)
    monkeypatch.setattr(watcher_mod, "find_deleted_files", lambda _repo, _db: [])

    try:
        t0 = time.monotonic()
        count = mcp_server._get_stale_count()
        paths = mcp_server._get_stale_paths()
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, f"staleness blocked the caller for {elapsed:.1f}s"
        assert count == 0
        assert paths == frozenset()
    finally:
        # Don't leave a walk in flight -- single-flight would then suppress the
        # next test's refresh and it would fail for the wrong reason.
        release.set()
        time.sleep(0.2)


def test_concurrent_callers_trigger_only_one_staleness_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three concurrent full-repo walks (boot thread + get_context's count +
    get_context's paths) hammered the filesystem hard enough to take down the
    interpreter outright on a real 16k-file repo:

        Fatal Python error: PyEval_SaveThread: the function must be called
        with the GIL held, but the GIL is released

    -- crashing the server mid-session, which the client reports only as a
    dropped connection. One walk at a time, shared by every caller."""
    import threading
    import time

    import codegraph.sync.watcher as watcher_mod

    repo = tmp_path / "proj"
    db = _index_temp_repo(repo, repo / "a.py", "def f():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.setattr(mcp_server, "_stale_cache", mcp_server._StalenessCache())
    monkeypatch.setattr(mcp_server, "_stale_paths_cache", mcp_server._StalePathsCache())

    walks = {"n": 0}
    walk_lock = threading.Lock()

    def counting_walk(_repo: Path, _db: Path) -> list[Path]:
        with walk_lock:
            walks["n"] += 1
        time.sleep(1.0)
        return []

    monkeypatch.setattr(mcp_server, "_staleness_refresh_running", False)
    monkeypatch.setattr(watcher_mod, "git_head", lambda _repo: "head")
    monkeypatch.setattr(watcher_mod, "find_stale_files", counting_walk)
    monkeypatch.setattr(watcher_mod, "find_deleted_files", lambda _repo, _db: [])

    threads = [threading.Thread(target=mcp_server._get_stale_count) for _ in range(4)] + [
        threading.Thread(target=mcp_server._get_stale_paths) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    # Let the single in-flight refresh finish.
    time.sleep(2.5)
    assert walks["n"] == 1, f"{walks['n']} concurrent walks -- must be single-flight"


def test_one_walk_fills_both_the_count_and_paths_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """count_stale_files is literally len(find_stale_files), so deriving the
    count and the path set from separate walks doubled the cost for nothing."""
    import codegraph.sync.watcher as watcher_mod

    repo = tmp_path / "proj"
    db = _index_temp_repo(repo, repo / "a.py", "def f():\n    return 1\n")
    monkeypatch.setattr(mcp_server, "_db_path", db)
    monkeypatch.setattr(mcp_server, "_stale_cache", mcp_server._StalenessCache())
    monkeypatch.setattr(mcp_server, "_stale_paths_cache", mcp_server._StalePathsCache())

    walks = {"n": 0}

    def counting_walk(_repo: Path, _db: Path) -> list[Path]:
        walks["n"] += 1
        return [repo / "a.py", repo / "b.py"]

    monkeypatch.setattr(watcher_mod, "git_head", lambda _repo: "head")
    monkeypatch.setattr(watcher_mod, "find_stale_files", counting_walk)
    monkeypatch.setattr(watcher_mod, "find_deleted_files", lambda _repo, _db: ["gone.py"])

    mcp_server._refresh_staleness_now()

    assert walks["n"] == 1
    assert mcp_server._get_stale_count() == 3  # 2 changed + 1 deleted
    assert mcp_server._get_stale_paths() == frozenset({"a.py", "b.py", "gone.py"})


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


def test_get_context_points_to_impact_analysis_when_callers_are_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The response-level fix for the round-8 finding: a numeric mismatch between
    called_by_count and the (capped) called_by list was already present in every
    response, but nothing said what to do about it -- the agent had to notice the
    gap and remember a static guide bullet from many turns earlier. This is a
    just-in-time nudge, live in the response next to the truncated data itself,
    naming the tool and the exact entity_id to pass it."""
    from codegraph.server.mcp_server import _NEIGHBOR_CAP

    repo = tmp_path / "proj"
    src = repo / "hub.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    callers_src = "\n".join(
        f"def caller_{i}():\n    return target()" for i in range(_NEIGHBOR_CAP + 3)
    )
    src.write_text(f"def target():\n    return 1\n\n\n{callers_src}\n", encoding="utf-8")
    db = repo / ".codegraph" / "graph.duckdb"
    result = CliRunner().invoke(cli_app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.output
    monkeypatch.setattr(mcp_server, "_db_path", db)

    data = _call("get_context", {"query": "target"})
    target = next(e for e in data["entities"] if e["entity_id"].endswith(":target"))
    assert target["called_by_count"] > _NEIGHBOR_CAP

    pointer = [w for w in data["warnings"] if "impact_analysis" in w]
    assert pointer, f"expected an impact_analysis pointer in warnings, got: {data['warnings']}"
    assert target["entity_id"] in pointer[0]


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


# ---------- startup embedding worker ----------


def test_embedding_worker_not_started_without_embeddings(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A --no-embed index has no vectors, so startup must NOT spawn the worker
    (whose only job is to host the heavy torch stack)."""
    import codegraph.embeddings.remote as remote

    started = {"n": 0}
    monkeypatch.setattr(
        remote.EmbeddingWorkerClient, "start", lambda self: started.__setitem__("n", 1)
    )
    mcp_server._start_embedding_worker()
    assert started["n"] == 0


def test_embedding_worker_start_is_noop_when_db_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup must never raise, even if the DB doesn't exist yet."""
    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    mcp_server._start_embedding_worker()  # should return quietly


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
    process start and 'serving' once the handshake loop is about to begin.
    Added after three real stuck-connection incidents that each began as
    guesswork over a silent process -- with these lines, the absence/last-line
    of the trail localizes the stall at a glance. Staleness/warmup timings are
    no longer part of this immediate trail -- see
    test_boot_diagnostics_run_in_background_and_report_when_done -- because
    waiting for them here is exactly the bug in
    test_main_does_not_block_on_slow_staleness_check."""
    import anyio

    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    monkeypatch.setattr("sys.argv", ["codegraph-mcp"])
    monkeypatch.setattr(anyio, "run", lambda fn: None)

    mcp_server.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "starting (db:" in captured.err
    assert "serving (boot" in captured.err


def test_main_does_not_block_on_slow_staleness_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: staleness check + embedding warm-up used to run BEFORE
    stdio_server() started, blocking the MCP handshake itself. On a large
    repo the staleness scan alone can take several seconds -- confirmed on a
    real 16k-file repo taking 13+s total boot, past what Claude Code's own
    MCP connect timeout allows, surfacing as "Failed to connect" in `claude
    mcp list` even though the server was working fine. Both now run on a
    background thread instead of gating startup."""
    import time

    import anyio

    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    monkeypatch.setattr("sys.argv", ["codegraph-mcp"])
    monkeypatch.setattr(anyio, "run", lambda fn: None)

    def _slow_count_stale_files(*_a, **_k):
        time.sleep(0.3)
        return 0

    monkeypatch.setattr("codegraph.sync.watcher.count_stale_files", _slow_count_stale_files)

    start = time.monotonic()
    mcp_server.main()
    elapsed = time.monotonic() - start

    assert elapsed < 0.1, f"main() blocked for {elapsed:.2f}s on the staleness check"


def test_staleness_check_runs_in_background_and_reports_when_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Staleness (plain DB queries, no native-threading-sensitive imports) is
    safe to background and still leaves its own breadcrumb once it finishes.
    Unlike embedding warmup (see test_main_warms_embedding_model_
    synchronously_before_serving), it never touches torch/sentence-
    transformers, so it doesn't carry the same off-main-thread deadlock risk."""
    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")

    thread = mcp_server._start_staleness_check_in_background()
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    assert "staleness" in capsys.readouterr().err


def test_process_alive_true_for_current_process() -> None:
    import os

    assert mcp_server._process_alive(os.getpid())


def test_process_alive_false_for_an_exited_process() -> None:
    """Deterministic, not a guessed-PID heuristic: spawn a trivial child,
    wait for it to fully exit, then confirm the liveness check correctly
    reports it as dead -- this is the exact check the PPID watchdog relies
    on to detect a killed parent, so it must be correct in both directions."""
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=5.0)
    assert not mcp_server._process_alive(proc.pid)


def test_ppid_watchdog_tick_true_when_parent_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the root cause behind repeatedly-diagnosed
    "orphaned MCP server" incidents: the watchdog's per-check logic must
    signal exit once the parent is gone. Tests the pure tick function
    directly rather than the thread-spawning wrapper -- a real background
    thread would keep looping (and eventually call the real os._exit) well
    past this test's own teardown, which would kill the whole test run."""
    monkeypatch.setattr(mcp_server, "_process_alive", lambda pid: False)
    assert mcp_server._ppid_watchdog_tick(12345) is True


def test_ppid_watchdog_tick_false_while_parent_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "_process_alive", lambda pid: True)
    assert mcp_server._ppid_watchdog_tick(12345) is False


def test_start_ppid_watchdog_spawns_a_named_daemon_thread() -> None:
    """Shallow wiring check: confirms the thread actually gets created,
    named, and marked daemon (so it can't block process exit) -- without
    waiting through a real sleep-interval cycle."""
    import os
    import threading

    mcp_server._start_ppid_watchdog(parent_pid=os.getpid(), interval=999.0)
    thread = next((t for t in threading.enumerate() if t.name == "codegraph-ppid-watchdog"), None)
    assert thread is not None
    assert thread.daemon is True


def test_main_starts_the_embedding_worker_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker is spawned during boot so the model is already loading by the
    time the first semantic query lands -- but boot never *waits* for it (see
    the timing test below)."""
    import anyio

    order: list[str] = []
    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    monkeypatch.setattr("sys.argv", ["codegraph-mcp"])
    monkeypatch.setattr(mcp_server, "_start_embedding_worker", lambda: order.append("worker"))
    monkeypatch.setattr(anyio, "run", lambda fn: order.append("serve"))

    mcp_server.main()

    assert order == ["worker", "serve"]


def test_main_does_not_block_boot_on_the_embedding_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this whole subprocess design exists to fix.

    Boot used to load sentence-transformers/torch synchronously before it
    could serve: measured 25.6s cold on a real 97k-entity index, against
    Claude Code's 30s MCP connect timeout. Warm filesystem cache -> connected;
    cold -> the client gave up and dropped the server, silently, with no error
    shown to the user. The agent then just used grep instead, which is what
    made several A/B runs look like a model-behaviour problem when the tools
    were simply never there.

    It also could not be moved to a background thread: importing torch off the
    main thread while the event loop runs deadlocks the process outright. A
    subprocess solves both -- so boot must stay fast even when the model is
    genuinely slow to load."""
    import time

    import anyio

    monkeypatch.setattr(mcp_server, "_db_path", tmp_path / "nope.duckdb")
    monkeypatch.setattr("sys.argv", ["codegraph-mcp"])
    monkeypatch.setattr(anyio, "run", lambda fn: None)

    # Stand in for a model that takes far longer than the connect timeout.
    def slow_model_load() -> None:
        time.sleep(45)

    monkeypatch.setattr(
        "codegraph.embeddings.remote.EmbeddingWorkerClient.start", lambda self: None
    )
    monkeypatch.setattr(
        "codegraph.embeddings.pipeline.embed_batch", lambda *_a, **_k: slow_model_load()
    )

    t0 = time.monotonic()
    mcp_server.main()
    boot = time.monotonic() - t0

    assert boot < 10.0, f"boot took {boot:.1f}s -- must stay well under the 30s connect timeout"


def test_server_embeds_through_the_worker_not_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """torch must never be imported in the server process -- that is what makes
    the deadlock structurally impossible, not just unlikely."""
    import codegraph.embeddings.pipeline as pipeline
    import codegraph.embeddings.remote as remote
    import numpy as np

    def fail(*_a: object, **_k: object) -> None:
        raise AssertionError("server embedded in-process instead of via the worker")

    monkeypatch.setattr(pipeline, "embed_batch", fail)
    monkeypatch.setattr(pipeline, "embed_one", fail)
    monkeypatch.setattr(
        remote.EmbeddingWorkerClient,
        "embed_batch_or_none",
        lambda self, texts: np.zeros((len(texts), 384), dtype=np.float32),
    )

    assert mcp_server._maybe_embed("hello") is not None
    assert mcp_server._maybe_embed_batch(["a", "b"]) == [
        pytest.approx([0.0] * 384),
        pytest.approx([0.0] * 384),
    ]


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


# ---------- get_context: batched (list) queries ----------


def test_get_context_batch_merges_results_from_each_query(indexed_db: Path) -> None:
    """A list query must return results for EACH named target, not just the
    first -- the whole point is collapsing N round-trips into one, not
    picking a winner."""
    data = _call("get_context", {"query": ["authenticate", "run_server"], "limit": 10})
    names_or_ids = " ".join(e["entity_id"] for e in data["entities"])
    assert "authenticate" in names_or_ids
    assert "run_server" in names_or_ids


def test_get_context_batch_dedupes_repeated_target(indexed_db: Path) -> None:
    data = _call("get_context", {"query": ["authenticate", "authenticate"], "limit": 10})
    auth_hits = [e for e in data["entities"] if e["entity_id"].endswith(":authenticate")]
    assert len(auth_hits) == 1


def test_get_context_batch_caps_at_five_queries(indexed_db: Path) -> None:
    """More than 5 queries must not error -- extras are silently dropped."""
    many = ["authenticate", "run_server", "boot", "User", "Session", "UserController", "LoginForm"]
    data = _call("get_context", {"query": many, "limit": 10})
    assert data["total"] >= 1


def test_get_context_batch_respects_limit_across_all_queries(indexed_db: Path) -> None:
    data = _call("get_context", {"query": ["authenticate", "run_server", "User"], "limit": 2})
    assert len(data["entities"]) <= 2


def test_get_context_batch_skips_low_confidence_warning(indexed_db: Path) -> None:
    """The low-confidence heuristic is tuned for one prose query; a batch of
    already-known symbol names shouldn't trigger it even if, individually,
    a name wouldn't corroborate 2+ words of some other query."""
    data = _call("get_context", {"query": ["authenticate", "run_server"], "limit": 10})
    assert not any("low-confidence" in w.lower() for w in data["warnings"])


def test_get_context_single_string_query_unchanged(indexed_db: Path) -> None:
    """Backward compatibility: a plain string query behaves identically to
    before batching was added."""
    data = _call("get_context", {"query": "authenticate"})
    assert data["total"] >= 1
    assert any(e["entity_id"].endswith(":authenticate") for e in data["entities"])


def test_maybe_embed_batch_returns_one_vector_per_query() -> None:
    from codegraph.server.mcp_server import _maybe_embed_batch

    vectors = _maybe_embed_batch(["authenticate", "run server"])
    assert len(vectors) == 2
    # Either the model is available (both real vectors) or unavailable (both
    # None) -- either way, per-query count and fallback shape must match.
    assert all(v is None for v in vectors) or all(isinstance(v, list) for v in vectors)


def test_maybe_embed_batch_empty_list_returns_empty() -> None:
    from codegraph.server.mcp_server import _maybe_embed_batch

    assert _maybe_embed_batch([]) == []


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
