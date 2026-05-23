"""Tests for T4.4 — import-cycle detection via Tarjan SCC."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.analysis.cycles import build_import_graph, find_cycles, tarjan_scc
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _index(runner: CliRunner, repo: Path, db: Path) -> None:
    result = runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.stdout


# A 3-file circular import: a -> b -> c -> a.
_CYCLE_REPO = {
    "a.py": "from b import b_func\n\n\ndef a_func():\n    return b_func()\n",
    "b.py": "from c import c_func\n\n\ndef b_func():\n    return c_func()\n",
    "c.py": "from a import a_func\n\n\ndef c_func():\n    return a_func()\n",
}

# A clean dependency chain with no cycle: main -> lib -> util.
_ACYCLIC_REPO = {
    "util.py": "def util():\n    return 1\n",
    "lib.py": "from util import util\n\n\ndef lib():\n    return util()\n",
    "main.py": "from lib import lib\n\n\ndef main():\n    return lib()\n",
}


# ---------- pure algorithm: tarjan_scc ----------


def test_tarjan_finds_three_node_cycle() -> None:
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    sccs = tarjan_scc(graph)
    assert len(sccs) == 1
    assert sorted(sccs[0]) == ["a", "b", "c"]


def test_tarjan_dag_yields_all_singletons() -> None:
    graph = {"a": {"b"}, "b": {"c"}, "c": set()}
    sccs = tarjan_scc(graph)
    assert sorted(sorted(s) for s in sccs) == [["a"], ["b"], ["c"]]


def test_tarjan_separates_two_independent_cycles() -> None:
    graph = {
        "a": {"b"},
        "b": {"a"},  # cycle 1
        "c": {"d"},
        "d": {"c"},  # cycle 2
        "e": {"a"},  # not in any cycle
    }
    sccs = [sorted(s) for s in tarjan_scc(graph)]
    assert ["a", "b"] in sccs
    assert ["c", "d"] in sccs
    assert ["e"] in sccs


def test_tarjan_handles_deep_chain_without_recursion_error() -> None:
    # 5000-node chain would overflow a recursive Tarjan; iterative must cope.
    n = 5000
    graph: dict[str, set[str]] = {str(i): {str(i + 1)} for i in range(n)}
    graph[str(n)] = set()
    sccs = tarjan_scc(graph)
    assert len(sccs) == n + 1  # all singletons, no crash


# ---------- graph build + find_cycles over a real index ----------


def test_build_import_graph_excludes_externals(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _ACYCLIC_REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        graph = build_import_graph(store.conn)
    finally:
        store.close()
    # Only in-repo files are nodes; edges follow imports.
    assert graph["main.py"] == {"lib.py"}
    assert graph["lib.py"] == {"util.py"}
    assert graph["util.py"] == set()


def test_find_cycles_detects_circular_import(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CYCLE_REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        found = find_cycles(store.conn)
    finally:
        store.close()
    assert found == [["a.py", "b.py", "c.py"]]


def test_find_cycles_empty_on_acyclic_repo(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _ACYCLIC_REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        assert find_cycles(store.conn) == []
    finally:
        store.close()


# ---------- CLI ----------


def test_cli_cycles_reports_cycle(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CYCLE_REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["cycles", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Found 1 import cycle" in result.stdout
    assert "a.py" in result.stdout
    assert "b.py" in result.stdout
    assert "c.py" in result.stdout


def test_cli_cycles_clean_repo_message(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _ACYCLIC_REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["cycles", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "No import cycles found" in result.stdout


def test_cli_cycles_missing_db_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "nope.duckdb"
    result = runner.invoke(app, ["cycles", "--db", str(db)])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout
