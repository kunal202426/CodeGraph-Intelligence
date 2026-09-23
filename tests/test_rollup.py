"""Tests for the free, structural file/directory rollups (analysis/rollup.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
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


_REPO = {
    "auth/login.py": (
        "def authenticate(user):\n    return True\n\n"
        "def logout(user):\n    return None\n\n"
        "class Session:\n    pass\n"
    ),
    "auth/util.py": "def hash_password(pw):\n    return pw\n",
    "billing/invoice.py": (
        "from auth.login import authenticate\n\n"
        "def create_invoice(order):\n    return authenticate(order)\n"
    ),
    "README.md": "# Not indexed as code entities\n",
}
# auth/ carries: authenticate, logout, Session, hash_password + 2 auto module
# entities (one per file) = 6. Verified against the parser's real output
# rather than assumed -- module-level entities are real rows, not noise.
_AUTH_ENTITY_COUNT = 6


def test_build_file_rollup_lists_entity_counts_and_names(runner: CliRunner, tmp_path: Path) -> None:
    from codegraph.analysis.rollup import build_file_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        summary = build_file_rollup(store.conn, "auth/login.py")
    finally:
        store.close()
    assert "authenticate" in summary
    assert "logout" in summary
    assert "Session" in summary
    assert "function" in summary
    assert "class" in summary


def test_build_file_rollup_empty_for_a_file_with_no_entities(
    runner: CliRunner, tmp_path: Path
) -> None:
    from codegraph.analysis.rollup import build_file_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        summary = build_file_rollup(store.conn, "does_not_exist.py")
    finally:
        store.close()
    assert summary == ""


def test_build_file_rollup_caps_shown_names_and_reports_the_rest(
    runner: CliRunner, tmp_path: Path
) -> None:
    from codegraph.analysis.rollup import _FILE_ROLLUP_NAME_CAP, build_file_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    src = "\n".join(f"def fn_{i}():\n    return {i}" for i in range(_FILE_ROLLUP_NAME_CAP + 5))
    _make_repo(repo, {"big.py": src})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        total = store.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE file = 'big.py'"
        ).fetchone()[0]
        summary = build_file_rollup(store.conn, "big.py")
    finally:
        store.close()
    assert f"+{total - _FILE_ROLLUP_NAME_CAP} more" in summary


def test_build_dir_rollup_counts_files_and_entities_recursively(
    runner: CliRunner, tmp_path: Path
) -> None:
    from codegraph.analysis.rollup import build_dir_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        summary = build_dir_rollup(store.conn, "auth")
    finally:
        store.close()
    assert "2 files" in summary
    assert f"{_AUTH_ENTITY_COUNT} entities" in summary


def test_build_dir_rollup_surfaces_highest_fan_in_entity(runner: CliRunner, tmp_path: Path) -> None:
    from codegraph.analysis.rollup import build_dir_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        summary = build_dir_rollup(store.conn, "auth")
    finally:
        store.close()
    # authenticate() is called from billing/invoice.py -- a cross-dir caller,
    # still counted since fan-in is repo-wide, only the *subject* is dir-scoped.
    assert "authenticate" in summary


def test_build_dir_rollup_root_covers_everything(runner: CliRunner, tmp_path: Path) -> None:
    from codegraph.analysis.rollup import build_dir_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        root_summary = build_dir_rollup(store.conn, ".")
        empty_summary = build_dir_rollup(store.conn, "")
    finally:
        store.close()
    assert "3 files" in root_summary
    assert root_summary == empty_summary


def test_build_dir_rollup_empty_for_a_directory_with_no_files(
    runner: CliRunner, tmp_path: Path
) -> None:
    from codegraph.analysis.rollup import build_dir_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        summary = build_dir_rollup(store.conn, "nonexistent")
    finally:
        store.close()
    assert summary == ""


def test_build_dir_rollup_does_not_cross_a_sibling_directory_with_a_shared_prefix(
    runner: CliRunner, tmp_path: Path
) -> None:
    """auth/ and auth_backup/ share a string prefix but are different
    directories -- a naive LIKE 'auth%' would wrongly merge them."""
    from codegraph.analysis.rollup import build_dir_rollup
    from codegraph.graph.store import GraphStore

    repo = tmp_path / "repo"
    files = dict(_REPO)
    files["auth_backup/old.py"] = "def old_fn():\n    return 1\n"
    _make_repo(repo, files)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        summary = build_dir_rollup(store.conn, "auth")
    finally:
        store.close()
    assert "old_fn" not in summary
    assert "2 files" in summary
