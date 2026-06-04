"""Tests for T11.3 — staleness guard (count_stale_files + serve/MCP integration).

count_stale_files unit tests work against real temp DBs.
serve integration tests mock count_stale_files so they don't need
the embedding model or a real uvicorn server.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from codegraph.cli import app
from codegraph.sync.watcher import count_stale_files
from typer.testing import CliRunner

_RUNNER = CliRunner()


# --------------------------------------------------------------------------- #
# count_stale_files — unit tests
# --------------------------------------------------------------------------- #


def test_count_stale_no_db(tmp_path: Path) -> None:
    """Returns 0 when the DB file does not exist."""
    assert count_stale_files(tmp_path / "repo", tmp_path / "nonexistent.duckdb") == 0


def test_count_stale_empty_db(tmp_path: Path) -> None:
    """Returns 0 when the DB has no indexed files (nothing to compare)."""
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    with GraphStore(db) as store:
        store.init_schema()

    assert count_stale_files(repo, db) == 0


def _index_repo(repo: Path, db: Path, files: dict[str, str]) -> None:
    """Helper: write files + index them via CLI (no embed)."""
    for rel, content in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content, encoding="utf-8")
    result = _RUNNER.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.output


def test_count_stale_fresh_after_index(tmp_path: Path) -> None:
    """Returns 0 immediately after indexing — files are not stale yet."""
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    _index_repo(repo, db, {"app.py": "def run():\n    pass\n"})

    assert count_stale_files(repo, db) == 0


def test_count_stale_after_modification(tmp_path: Path) -> None:
    """Returns > 0 when a source file is modified after the last index."""
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    src = repo / "app.py"
    src.write_text("def run():\n    pass\n", encoding="utf-8")
    _index_repo(repo, db, {})  # app.py is already present in repo

    # Give the OS a moment so the new mtime is strictly after indexed_at.
    time.sleep(0.05)
    src.write_text("def run():\n    pass\n\ndef stop():\n    pass\n", encoding="utf-8")

    assert count_stale_files(repo, db) > 0


def test_count_stale_unchanged_file_not_counted(tmp_path: Path) -> None:
    """A file that has NOT been touched since the index is not counted."""
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    _index_repo(repo, db, {"lib.py": "def helper():\n    pass\n"})

    # Do NOT modify lib.py — stale count should stay 0.
    assert count_stale_files(repo, db) == 0


def test_count_stale_only_counts_source_files(tmp_path: Path) -> None:
    """Non-source files (e.g. .txt) are not counted even if newer than index."""
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    _index_repo(repo, db, {"app.py": "def run():\n    pass\n"})

    # Write a .txt file — walker ignores it, so it should not affect stale count.
    time.sleep(0.05)
    (repo / "README.txt").write_text("some docs\n", encoding="utf-8")

    assert count_stale_files(repo, db) == 0


# --------------------------------------------------------------------------- #
# serve integration — staleness warning in CLI output
# --------------------------------------------------------------------------- #


# The serve command imports count_stale_files lazily (local import inside the
# function), so we patch it at the source module, not at codegraph.cli.
# uvicorn and create_app are local imports inside serve(); patch at their source modules.
# Use --dev to skip the npm frontend build during tests.
_STALE_PATCH = "codegraph.sync.watcher.count_stale_files"
_UVICORN_PATCH = "uvicorn.run"  # actual target of the local `import uvicorn` call
_APP_PATCH = "codegraph.server.api.create_app"
_SERVE_FLAGS = ["--no-open", "--dev"]  # --dev skips npm build; --no-open skips browser


def test_serve_warns_when_stale(tmp_path: Path) -> None:
    """Warning message appears when count_stale_files returns > 0."""
    db = tmp_path / "graph.duckdb"
    db.touch()

    with (
        patch(_STALE_PATCH, return_value=3),
        patch(_APP_PATCH, return_value=MagicMock()),
        patch(_UVICORN_PATCH),
    ):
        result = _RUNNER.invoke(app, ["serve", "--db", str(db), *_SERVE_FLAGS])

    assert "Warning" in result.output or "changed since last index" in result.output


def test_serve_silent_when_fresh(tmp_path: Path) -> None:
    """No staleness warning when count_stale_files returns 0."""
    db = tmp_path / "graph.duckdb"
    db.touch()

    with (
        patch(_STALE_PATCH, return_value=0),
        patch(_APP_PATCH, return_value=MagicMock()),
        patch(_UVICORN_PATCH),
    ):
        result = _RUNNER.invoke(app, ["serve", "--db", str(db), *_SERVE_FLAGS])

    assert "changed since last index" not in result.output


def test_serve_singular_noun_for_one_file(tmp_path: Path) -> None:
    """Uses 'file' (not 'files') when exactly 1 file is stale."""
    db = tmp_path / "graph.duckdb"
    db.touch()

    with (
        patch(_STALE_PATCH, return_value=1),
        patch(_APP_PATCH, return_value=MagicMock()),
        patch(_UVICORN_PATCH),
    ):
        result = _RUNNER.invoke(app, ["serve", "--db", str(db), *_SERVE_FLAGS])

    flat = result.output.replace("\n", "")
    assert "1 file changed" in flat or "1 file" in flat
