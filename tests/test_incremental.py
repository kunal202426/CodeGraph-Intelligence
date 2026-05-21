"""Tests for T2.3 — hash-based incremental indexing."""

from __future__ import annotations

from pathlib import Path

import pytest
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


# ---------- hash skip ----------


def test_unchanged_file_is_skipped_on_re_index(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "def f(): return 1\n",
            "b.py": "def g(): return 2\n",
        },
    )
    db = tmp_path / "graph.duckdb"

    first = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert first.exit_code == 0
    assert "Parsed 2 files" in first.stdout  # first run never has unchanged

    second = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert second.exit_code == 0
    assert "Re-parsed 0 of 2 files" in second.stdout
    assert "2 unchanged" in second.stdout


def test_modifying_one_file_re_parses_only_it(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "def f(): return 1\n",
            "b.py": "def g(): return 2\n",
            "c.py": "def h(): return 3\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    # Modify just one file.
    (repo / "b.py").write_text("def g_modified(): return 99\n", encoding="utf-8")

    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0
    assert "Re-parsed 1 of 3 files" in result.stdout
    assert "2 unchanged" in result.stdout

    store = GraphStore(db)
    try:
        # b.py's new function is in the DB; the old one is gone.
        names_in_b = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM entities WHERE file = 'b.py'"
            ).fetchall()
        }
    finally:
        store.close()
    assert "g_modified" in names_in_b
    assert "g" not in names_in_b


def test_new_file_picked_up_on_re_index(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"a.py": "def f(): return 1\n"})
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    # Add a new file.
    (repo / "new.py").write_text("def brand_new(): return 42\n", encoding="utf-8")

    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0
    assert "Re-parsed 1 of 2 files" in result.stdout

    store = GraphStore(db)
    try:
        files = {row[0] for row in store.conn.execute("SELECT path FROM files").fetchall()}
        names = {row[0] for row in store.conn.execute("SELECT name FROM entities").fetchall()}
    finally:
        store.close()
    assert "new.py" in files
    assert "brand_new" in names


def test_deleted_entities_purged_when_source_changes(runner: CliRunner, tmp_path: Path) -> None:
    """Removing a function from source removes its entity on re-index."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "def keep(): return 1\ndef remove_me(): return 2\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    store = GraphStore(db)
    try:
        before = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM entities WHERE file = 'a.py'"
            ).fetchall()
        }
    finally:
        store.close()
    assert "keep" in before
    assert "remove_me" in before

    # Rewrite the file without remove_me.
    (repo / "a.py").write_text("def keep(): return 1\n", encoding="utf-8")
    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0

    store = GraphStore(db)
    try:
        after = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM entities WHERE file = 'a.py'"
            ).fetchall()
        }
    finally:
        store.close()
    assert "keep" in after
    assert "remove_me" not in after  # stale entity purged


def test_imports_purged_when_source_changes(runner: CliRunner, tmp_path: Path) -> None:
    """Removing an import statement removes its edge on re-index."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "import os\nimport sys\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    store = GraphStore(db)
    try:
        before = {
            row[0]
            for row in store.conn.execute(
                "SELECT dst_id FROM edges WHERE src_id = 'py:a.py:a'"
            ).fetchall()
        }
    finally:
        store.close()
    assert "external:os" in before
    assert "external:sys" in before

    # Drop the sys import.
    (repo / "a.py").write_text("import os\n", encoding="utf-8")
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    store = GraphStore(db)
    try:
        after = {
            row[0]
            for row in store.conn.execute(
                "SELECT dst_id FROM edges WHERE src_id = 'py:a.py:a'"
            ).fetchall()
        }
    finally:
        store.close()
    assert "external:os" in after
    assert "external:sys" not in after  # stale edge purged


# ---------- store helpers (unit) ----------


def test_store_get_file_hash_returns_none_when_unknown(tmp_path: Path) -> None:
    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    try:
        assert store.get_file_hash("never_indexed.py") is None
    finally:
        store.close()


def test_store_clear_file_removes_entities_and_edges(tmp_path: Path, runner: CliRunner) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"a.py": "import os\ndef f(): return 1\n"})
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    store = GraphStore(db)
    try:
        assert store.count_entities() > 0
        assert store.count_edges() > 0
        store.clear_file("a.py")
        assert (
            store.conn.execute("SELECT count(*) FROM entities WHERE file = 'a.py'").fetchone()[0]
            == 0
        )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM edges WHERE src_id LIKE 'py:a.py:%'"
            ).fetchone()[0]
            == 0
        )
        # File row itself is kept so upsert_file can update its hash next pass.
        assert store.get_file_hash("a.py") is not None
    finally:
        store.close()
