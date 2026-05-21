"""End-to-end CLI tests via typer.testing.CliRunner."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


def _make_pyrepo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


# ---------- index ----------


def test_index_writes_entities_to_db(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Indexed" in result.stdout
    assert db.exists()

    store = GraphStore(db)
    try:
        assert store.count_files() >= 1
        assert store.count_entities() >= 5  # module + several functions / classes / methods
    finally:
        store.close()


def test_index_is_idempotent(runner: CliRunner, tmp_path: Path) -> None:
    """Re-indexing the same repo must not double the row counts."""
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    store = GraphStore(db)
    try:
        first_entities = store.count_entities()
        first_files = store.count_files()
    finally:
        store.close()

    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0
    # T2.3: every file should be reported as unchanged on second run.
    assert "unchanged" in result.stdout
    store = GraphStore(db)
    try:
        assert store.count_entities() == first_entities
        assert store.count_files() == first_files
    finally:
        store.close()


def test_index_skips_unsupported_languages(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_pyrepo(
        repo,
        {
            "main.py": "def foo(): return 1\n",
            "front/index.ts": "export const x = 1;\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0
    assert "Skipped 1 files" in result.stdout
    store = GraphStore(db)
    try:
        # Only the Python file landed.
        files = [row[0] for row in store.conn.execute("SELECT path FROM files").fetchall()]
        assert "main.py" in files
        assert "front/index.ts" not in files
    finally:
        store.close()


def test_index_on_empty_dir_prints_nothing_found(runner: CliRunner, tmp_path: Path) -> None:
    empty = tmp_path / "empty_repo"
    empty.mkdir()
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(empty), "--db", str(db)])
    assert result.exit_code == 0
    assert "No indexable files found" in result.stdout


def test_index_missing_repo_errors(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["index", str(tmp_path / "nope"), "--db", str(tmp_path / "g.duckdb")]
    )
    assert result.exit_code != 0  # typer rejects missing path via exists=True


# ---------- search ----------


@pytest.fixture
def indexed_db(runner: CliRunner, tmp_path: Path) -> Path:
    """Index the sample fixture into a fresh DB and return the path."""
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    return db


def test_search_finds_entity_by_name(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "authenticate", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout
    assert "auth/login.py" in result.stdout
    assert "Results for" in result.stdout


def test_search_case_insensitive(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "AUTHENTICATE", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout


def test_search_finds_by_docstring(runner: CliRunner, indexed_db: Path) -> None:
    # The fixture's authenticate() docstring contains "Validate user credentials"
    result = runner.invoke(app, ["search", "credentials", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout


def test_search_partial_match(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "Login", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "LoginForm" in result.stdout


def test_search_no_results_yellow_message(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(
        app, ["search", "definitely_no_such_symbol_xyzzy", "--db", str(indexed_db)]
    )
    assert result.exit_code == 0
    assert "No results" in result.stdout


def test_search_limit_flag_caps_output(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "form", "--db", str(indexed_db), "--limit", "1"])
    assert result.exit_code == 0
    # "form" matches both LoginForm and _PrivateForm; with limit 1 only the better-ranked one appears.
    assert result.stdout.count("Form") <= 2  # may show in row + title


def test_search_missing_db_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["search", "x", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout


def test_search_semantic_flag_falls_back_with_notice(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "authenticate", "--semantic", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "T3.4" in result.stdout  # the deferral notice
    assert "authenticate" in result.stdout  # but it still returned results


# ---------- --version (sanity) ----------


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "codegraph 0.1.0" in result.stdout
