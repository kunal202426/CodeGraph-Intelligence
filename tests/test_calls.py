"""Tests for T4.1 — Python call-edge extraction + call resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from codegraph.parsers.python import PythonParser
from codegraph.parsers.typescript import TypeScriptParser
from typer.testing import CliRunner


@pytest.fixture
def parser() -> PythonParser:
    return PythonParser()


@pytest.fixture
def ts_parser() -> TypeScriptParser:
    return TypeScriptParser()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _call_edges(result):
    return [e for e in result.edges if e.type == "calls"]


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


# ---------- parser-level call extraction (provisional dst) ----------


def test_simple_call_emits_provisional_edge(parser: PythonParser) -> None:
    src = "def login():\n    authenticate('a', 'b')\n"
    edges = _call_edges(parser.parse(Path("a.py"), src))
    assert any(e.dst_id == "py:?call:authenticate" for e in edges)
    edge = next(e for e in edges if e.dst_id == "py:?call:authenticate")
    assert edge.src_id == "py:a.py:login"
    assert edge.type == "calls"
    assert edge.line == 2
    assert edge.confidence == 0.7


def test_attribute_call_uses_last_identifier(parser: PythonParser) -> None:
    src = "def run(self):\n    self.validate()\n    obj.helper.process()\n"
    edges = _call_edges(parser.parse(Path("a.py"), src))
    dsts = {e.dst_id for e in edges}
    assert "py:?call:validate" in dsts
    assert "py:?call:process" in dsts


def test_nested_call_in_arguments_captured(parser: PythonParser) -> None:
    src = "def f():\n    outer(inner(x))\n"
    edges = _call_edges(parser.parse(Path("a.py"), src))
    dsts = {e.dst_id for e in edges}
    assert "py:?call:outer" in dsts
    assert "py:?call:inner" in dsts


def test_method_calls_attributed_to_method_entity(parser: PythonParser) -> None:
    src = "class C:\n    def m(self):\n        helper()\n"
    edges = _call_edges(parser.parse(Path("a.py"), src))
    edge = next(e for e in edges if e.dst_id == "py:?call:helper")
    assert edge.src_id == "py:a.py:C.m"


def test_no_calls_in_body_yields_no_call_edges(parser: PythonParser) -> None:
    src = "def f():\n    return 1\n"
    assert _call_edges(parser.parse(Path("a.py"), src)) == []


# ---------- resolver: same-file / imported / external ----------


def _edges(store: GraphStore) -> set[tuple[str, str, str]]:
    rows = store.conn.execute(
        "SELECT src_id, dst_id, type FROM edges WHERE type = 'calls'"
    ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


def test_call_resolves_to_same_file_function(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"a.py": "def helper():\n    return 1\n\ndef main():\n    return helper()\n"})
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0
    store = GraphStore(db)
    try:
        calls = _edges(store)
    finally:
        store.close()
    assert ("py:a.py:main", "py:a.py:helper", "calls") in calls
    assert not any(dst.startswith("py:?call:") for _s, dst, _t in calls)


def test_call_resolves_to_imported_symbol(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "lib.py": "def compute():\n    return 1\n",
            "main.py": "from lib import compute\n\ndef run():\n    return compute()\n",
        },
    )
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0
    store = GraphStore(db)
    try:
        calls = _edges(store)
    finally:
        store.close()
    # run() calls compute(), which main.py imports from lib → resolves cross-file.
    assert ("py:main.py:run", "py:lib.py:compute", "calls") in calls


def test_unresolved_call_becomes_external(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"a.py": "def f():\n    print('hi')\n"})  # print is a builtin
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0
    store = GraphStore(db)
    try:
        calls = _edges(store)
    finally:
        store.close()
    assert ("py:a.py:f", "external:print", "calls") in calls


def test_call_edges_show_in_deps(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"a.py": "def helper():\n    return 1\n\ndef main():\n    return helper()\n"})
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    result = runner.invoke(app, ["deps", "main", "--db", str(db)])
    assert result.exit_code == 0
    assert "calls" in result.stdout
    assert "helper" in result.stdout


def test_no_py_call_placeholders_remain(runner: CliRunner, tmp_path: Path) -> None:
    """After indexing, the resolver must close every py:?call: edge."""
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["index", "tests/fixtures/sample_repo_py", "--db", str(db), "--no-embed"])
    store = GraphStore(db)
    try:
        leftover = store.conn.execute(
            "SELECT count(*) FROM edges WHERE dst_id LIKE '%:?call:%'"
        ).fetchone()[0]
    finally:
        store.close()
    assert leftover == 0


# ---------- T4.2: TypeScript parser-level call extraction ----------


def test_ts_simple_call_emits_provisional_edge(ts_parser: TypeScriptParser) -> None:
    src = "function login() {\n  authenticate('a', 'b');\n}\n"
    edges = _call_edges(ts_parser.parse(Path("a.ts"), src))
    assert any(e.dst_id == "ts:?call:authenticate" for e in edges)
    edge = next(e for e in edges if e.dst_id == "ts:?call:authenticate")
    assert edge.src_id == "ts:a.ts:login"
    assert edge.type == "calls"
    assert edge.line == 2
    assert edge.confidence == 0.7


def test_ts_member_call_uses_last_property(ts_parser: TypeScriptParser) -> None:
    src = "function run() {\n  this.validate();\n  obj.helper.process();\n}\n"
    edges = _call_edges(ts_parser.parse(Path("a.ts"), src))
    dsts = {e.dst_id for e in edges}
    assert "ts:?call:validate" in dsts
    assert "ts:?call:process" in dsts


def test_ts_nested_call_in_arguments_captured(ts_parser: TypeScriptParser) -> None:
    src = "function f() {\n  outer(inner(x));\n}\n"
    edges = _call_edges(ts_parser.parse(Path("a.ts"), src))
    dsts = {e.dst_id for e in edges}
    assert "ts:?call:outer" in dsts
    assert "ts:?call:inner" in dsts


def test_ts_method_calls_attributed_to_method_entity(ts_parser: TypeScriptParser) -> None:
    src = "class C {\n  m() {\n    helper();\n  }\n}\n"
    edges = _call_edges(ts_parser.parse(Path("a.ts"), src))
    edge = next(e for e in edges if e.dst_id == "ts:?call:helper")
    assert edge.src_id == "ts:a.ts:C.m"


def test_ts_arrow_block_body_calls_captured(ts_parser: TypeScriptParser) -> None:
    src = "const handler = () => {\n  process();\n};\n"
    edges = _call_edges(ts_parser.parse(Path("a.ts"), src))
    edge = next(e for e in edges if e.dst_id == "ts:?call:process")
    assert edge.src_id == "ts:a.ts:handler"


def test_ts_arrow_expression_body_call_captured(ts_parser: TypeScriptParser) -> None:
    src = "const f = (x) => foo(x);\n"
    edges = _call_edges(ts_parser.parse(Path("a.ts"), src))
    assert any(e.dst_id == "ts:?call:foo" for e in edges)


def test_ts_no_calls_in_body_yields_no_call_edges(ts_parser: TypeScriptParser) -> None:
    src = "function f() {\n  return 1;\n}\n"
    assert _call_edges(ts_parser.parse(Path("a.ts"), src)) == []


# ---------- T4.2: TypeScript call resolution (same-file / imported) ----------


def test_ts_call_resolves_to_same_file_function(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {"a.ts": "function helper() {\n  return 1;\n}\nfunction main() {\n  return helper();\n}\n"},
    )
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0
    store = GraphStore(db)
    try:
        calls = _edges(store)
    finally:
        store.close()
    assert ("ts:a.ts:main", "ts:a.ts:helper", "calls") in calls
    assert not any(dst.startswith("ts:?call:") for _s, dst, _t in calls)


def test_ts_call_resolves_to_imported_symbol(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "lib.ts": "export function compute() {\n  return 1;\n}\n",
            "main.ts": "import { compute } from './lib';\nfunction run() {\n  return compute();\n}\n",
        },
    )
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0
    store = GraphStore(db)
    try:
        calls = _edges(store)
    finally:
        store.close()
    assert ("ts:main.ts:run", "ts:lib.ts:compute", "calls") in calls
