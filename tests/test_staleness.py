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
from codegraph.sync.watcher import count_stale_files, find_deleted_files, git_head, index_one_file
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
# find_stale_files — per-file comparison (not a single repo-wide max)
# --------------------------------------------------------------------------- #


def test_stale_uses_per_file_timestamp_not_global_max(tmp_path: Path) -> None:
    """A file edited between its own index time and another file's later
    re-index must still be flagged -- a single repo-wide max(indexed_at)
    would hide it once the other file's re-index advances the max."""
    from codegraph.sync.watcher import find_stale_files

    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    a = repo / "a.py"
    b = repo / "b.py"
    a.write_text("def a1():\n    return 1\n", encoding="utf-8")
    b.write_text("def b1():\n    return 1\n", encoding="utf-8")
    _index_repo(repo, db, {})  # initial index: both a.py and b.py indexed together

    # Edit a.py first...
    time.sleep(0.05)
    a.write_text("def a1():\n    return 1\n\ndef a2():\n    return 2\n", encoding="utf-8")

    # ...then re-index only b.py (unrelated edit), which advances b.py's own
    # indexed_at -- and, under the old max()-based check, the repo-wide max too.
    time.sleep(0.05)
    b.write_text("def b1():\n    return 1\n\ndef b2():\n    return 2\n", encoding="utf-8")
    index_one_file(repo, b, db, no_embed=True)

    # a.py was never re-indexed and is still older than the new global max,
    # but it WAS modified after its own indexed_at -- must still be stale.
    stale_names = {p.name for p in find_stale_files(repo, db)}
    assert "a.py" in stale_names


# --------------------------------------------------------------------------- #
# find_deleted_files — files removed on disk but still in the DB
# --------------------------------------------------------------------------- #


def test_find_deleted_no_db(tmp_path: Path) -> None:
    assert find_deleted_files(tmp_path / "repo", tmp_path / "nonexistent.duckdb") == []


def test_find_deleted_none_when_nothing_removed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    _index_repo(repo, db, {"app.py": "def run():\n    pass\n"})

    assert find_deleted_files(repo, db) == []


def test_find_deleted_flags_removed_file(tmp_path: Path) -> None:
    """A file present in the DB but deleted from disk (e.g. by `git checkout`,
    a branch switch, or a plain `rm`) must be reported as deleted."""
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    _index_repo(
        repo, db, {"keep.py": "def keep():\n    pass\n", "gone.py": "def gone():\n    pass\n"}
    )

    (repo / "gone.py").unlink()

    assert find_deleted_files(repo, db) == ["gone.py"]


def test_find_deleted_does_not_flag_untouched_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    _index_repo(repo, db, {"keep.py": "def keep():\n    pass\n"})

    assert find_deleted_files(repo, db) == []


# --------------------------------------------------------------------------- #
# git_head — cheap fingerprint for cache invalidation on branch switch
# --------------------------------------------------------------------------- #


def _make_fake_git_repo(repo: Path, branch: str, commit: str) -> None:
    """Build a minimal .git directory (no real git binary needed) so
    git_head() can be exercised without shelling out."""
    git_dir = repo / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / branch).write_text(f"{commit}\n", encoding="utf-8")


def test_git_head_none_for_non_git_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git_head(repo) is None


def test_git_head_resolves_branch_to_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_fake_git_repo(repo, "main", "abc123")

    assert git_head(repo) == "abc123"


def test_git_head_changes_on_branch_switch(tmp_path: Path) -> None:
    """Checking out a different branch (different commit) must change the fingerprint."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_fake_git_repo(repo, "main", "abc123")
    before = git_head(repo)

    # Simulate `git checkout feature-branch`.
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (repo / ".git" / "refs" / "heads" / "feature").write_text("def456\n", encoding="utf-8")
    after = git_head(repo)

    assert before != after
    assert after == "def456"


def test_git_head_detached_returns_raw_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("9f8e7d6c5b4a\n", encoding="utf-8")

    assert git_head(repo) == "9f8e7d6c5b4a"


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
