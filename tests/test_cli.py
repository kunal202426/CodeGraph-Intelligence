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


# ---------- --version (sanity) ----------


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "codegraph 0.1.0" in result.stdout
