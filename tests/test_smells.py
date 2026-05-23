"""Tests for T4.5 — heuristic code-smell detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.analysis.smells import cyclomatic_complexity, detect_smells
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


def _god_class_source(n_methods: int) -> str:
    lines = ["class Kitchen:"]
    for i in range(n_methods):
        lines.append(f"    def task_{i}(self):")
        lines.append(f"        return {i}")
    return "\n".join(lines) + "\n"


# ---------- cyclomatic_complexity (pure) ----------


def test_complexity_of_straight_line_code_is_one() -> None:
    assert cyclomatic_complexity("x = 1\nreturn x\n") == 1


def test_complexity_counts_decision_points() -> None:
    src = "if a and b:\n    for x in y:\n        while z or w:\n            pass\n"
    # if, and, for, while, or → 5 decision points → complexity 6
    assert cyclomatic_complexity(src) == 6


# ---------- detect_smells over a real index ----------


def test_god_class_detected(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"big.py": _god_class_source(20)})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        smells = detect_smells(store.conn)
    finally:
        store.close()
    god = [s for s in smells if s.kind == "god-class"]
    assert len(god) == 1
    assert god[0].name == "Kitchen"
    assert god[0].metric == 20
    assert god[0].threshold == 15


def test_clean_repo_has_no_smells(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"ok.py": "def small():\n    return 1\n"})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        assert detect_smells(store.conn) == []
    finally:
        store.close()


def test_thresholds_are_configurable(runner: CliRunner, tmp_path: Path) -> None:
    # A 4-method class is fine by default but a god-class at threshold 3.
    repo = tmp_path / "repo"
    _make_repo(repo, {"m.py": _god_class_source(4)})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        assert detect_smells(store.conn) == []  # default threshold 15
        flagged = detect_smells(store.conn, god_class_methods=3)
    finally:
        store.close()
    assert any(s.kind == "god-class" and s.metric == 4 for s in flagged)


def test_complex_function_detected(runner: CliRunner, tmp_path: Path) -> None:
    body = "\n".join(f"    if x == {i} or x == {i}:\n        pass" for i in range(10))
    src = f"def tangled(x):\n{body}\n    return x\n"
    repo = tmp_path / "repo"
    _make_repo(repo, {"c.py": src})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        smells = detect_smells(store.conn, complex_function=5)
    finally:
        store.close()
    complex_fns = [s for s in smells if s.kind == "complex-function"]
    assert any(s.name == "tangled" for s in complex_fns)


def test_results_sorted_by_severity(runner: CliRunner, tmp_path: Path) -> None:
    # Two god-classes; the bigger one (more times over threshold) sorts first.
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {"a.py": _god_class_source(10), "b.py": _god_class_source(20)},
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        smells = detect_smells(store.conn, god_class_methods=5)
    finally:
        store.close()
    god = [s for s in smells if s.kind == "god-class"]
    assert [s.metric for s in god] == sorted((s.metric for s in god), reverse=True)
    assert god[0].metric == 20


# ---------- CLI ----------


def test_cli_smells_reports_god_class(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"big.py": _god_class_source(20)})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["smells", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "god-class" in result.stdout
    assert "Kitchen" in result.stdout


def test_cli_smells_clean_message(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"ok.py": "def small():\n    return 1\n"})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["smells", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "No code smells detected" in result.stdout


def test_cli_smells_threshold_flag(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"m.py": _god_class_source(4)})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    # Default: clean. With --god-class 3: flagged.
    assert "No code smells" in runner.invoke(app, ["smells", "--db", str(db)]).stdout
    result = runner.invoke(app, ["smells", "--db", str(db), "--god-class", "3"])
    assert result.exit_code == 0, result.stdout
    assert "god-class" in result.stdout


def test_cli_smells_missing_db_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "nope.duckdb"
    result = runner.invoke(app, ["smells", "--db", str(db)])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout
