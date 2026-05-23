"""Tests for T4.3 — reverse-call impact analysis + CLI `impact`."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.queries import find_callers, find_entity_by_name_or_id
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


# A small call chain: main -> service -> helper, plus a second caller of helper.
#   helper() is called by service() and by other()
#   service() is called by main()
_CHAIN = {
    "a.py": (
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def service():\n"
        "    return helper()\n"
        "\n"
        "def other():\n"
        "    return helper()\n"
        "\n"
        "def main():\n"
        "    return service()\n"
    ),
}


# ---------- query-level: find_callers ----------


def test_direct_callers_found(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CHAIN)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        helper = find_entity_by_name_or_id(store.conn, "helper")[0]
        tree = find_callers(store.conn, helper.entity_id, depth=1)
    finally:
        store.close()
    callers = {c.name for c in tree.callers.get(helper.entity_id, [])}
    assert callers == {"service", "other"}
    assert tree.total == 2
    assert tree.truncated is True  # depth=1 stops before transitive callers


def test_transitive_callers_found(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CHAIN)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        helper = find_entity_by_name_or_id(store.conn, "helper")[0]
        service = find_entity_by_name_or_id(store.conn, "service")[0]
        tree = find_callers(store.conn, helper.entity_id, depth=5)
    finally:
        store.close()
    # main calls service which calls helper → main is in the transitive radius.
    transitive = tree.callers.get(service.entity_id, [])
    assert any(c.name == "main" for c in transitive)
    assert tree.total == 3  # service, other, main
    assert tree.truncated is False


def test_leaf_entity_has_no_callers(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CHAIN)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        main = find_entity_by_name_or_id(store.conn, "main")[0]
        tree = find_callers(store.conn, main.entity_id, depth=5)
    finally:
        store.close()
    assert tree.callers == {}
    assert tree.total == 0


def test_caller_counted_once_despite_multiple_call_sites(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "def target():\n    return 1\n\ndef caller():\n    target()\n    return target()\n"
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        target = find_entity_by_name_or_id(store.conn, "target")[0]
        tree = find_callers(store.conn, target.entity_id, depth=3)
    finally:
        store.close()
    callers = tree.callers.get(target.entity_id, [])
    assert [c.name for c in callers] == ["caller"]
    assert tree.total == 1


def test_recursion_is_cycle_safe(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    # Mutual recursion: ping() calls pong(), pong() calls ping().
    _make_repo(
        repo,
        {"a.py": "def ping():\n    return pong()\n\ndef pong():\n    return ping()\n"},
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        ping = find_entity_by_name_or_id(store.conn, "ping")[0]
        tree = find_callers(store.conn, ping.entity_id, depth=10)
    finally:
        store.close()
    # Root (ping) is excluded; only pong is in its radius. ping reappears as a
    # caller of pong but is marked a cycle and not re-traversed → terminates.
    assert tree.total == 1
    pong_id = "py:a.py:pong"
    assert any(c.name == "ping" for c in tree.callers.get(pong_id, []))


# ---------- CLI ----------


def test_cli_impact_lists_callers(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CHAIN)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["impact", "helper", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "service" in result.stdout
    assert "other" in result.stdout
    assert "called by" in result.stdout
    assert "Blast radius" in result.stdout


def test_cli_impact_no_callers_message(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CHAIN)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["impact", "main", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "no callers" in result.stdout.lower()


def test_cli_impact_unknown_entity_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _CHAIN)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["impact", "does_not_exist", "--db", str(db)])
    assert result.exit_code == 1
    assert "No entity matching" in result.stdout


def test_cli_impact_missing_db_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "nope.duckdb"
    result = runner.invoke(app, ["impact", "helper", "--db", str(db)])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout
