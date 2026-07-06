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


# ---------- regression (Phase 29): non-Python languages must clear stale
# edges on re-index too, not just Python ----------


def test_store_clear_file_removes_non_python_edges(tmp_path: Path, runner: CliRunner) -> None:
    """clear_file's edge DELETE previously matched only the `py:` prefix, so
    every other language's stale edges (removed calls/imports/inherits)
    lingered in the graph forever after the first index. This locks in the
    language-agnostic fix directly against the store."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "app.ts": "export function helper() { return 1; }\nexport function caller() { return helper(); }\n"
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    store = GraphStore(db)
    try:
        assert (
            store.conn.execute("SELECT count(*) FROM edges WHERE type = 'calls'").fetchone()[0] == 1
        )
        store.clear_file("app.ts")
        assert store.conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 0
        assert store.conn.execute("SELECT count(*) FROM entities").fetchone()[0] == 0
    finally:
        store.close()


def test_removed_call_does_not_linger_as_a_stale_edge_after_reindex(
    runner: CliRunner, tmp_path: Path
) -> None:
    """End-to-end regression for the same bug: index a TS file with a call,
    remove the call, re-index, and confirm the stale edge is actually gone
    -- not just that clear_file's SQL pattern looks right in isolation."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "app.ts": "export function helper() { return 1; }\nexport function caller() { return helper(); }\n"
        },
    )
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0

    store = GraphStore(db)
    try:
        assert store.conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 1
    finally:
        store.close()

    _make_repo(
        repo,
        {
            "app.ts": "export function helper() { return 1; }\nexport function caller() { return 99; }\n"
        },
    )
    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0

    store = GraphStore(db)
    try:
        assert store.conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 0
    finally:
        store.close()


# ---------- bulk store helpers (Phase 29 batching) ----------


def test_upsert_files_bulk_matches_single_file_semantics(tmp_path: Path) -> None:
    from codegraph.uir import Language

    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    try:
        store.upsert_files(
            [
                ("a.py", Language.PYTHON, "hash-a", 10),
                ("b.py", Language.PYTHON, "hash-b", 20),
            ]
        )
        assert store.get_file_hash("a.py") == "hash-a"
        assert store.get_file_hash("b.py") == "hash-b"
        # Re-upserting with a new hash replaces in place (same semantics as upsert_file).
        store.upsert_files([("a.py", Language.PYTHON, "hash-a2", 11)])
        assert store.get_file_hash("a.py") == "hash-a2"
    finally:
        store.close()


def test_clear_files_bulk_clears_multiple_paths_language_agnostically(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "def f(): return g()\ndef g(): return 1\n",
            "b.ts": "export function h() { return k(); }\nexport function k() { return 2; }\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    CliRunner().invoke(app, ["index", str(repo), "--db", str(db)])

    store = GraphStore(db)
    try:
        assert store.conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 2
        store.clear_files(["a.py", "b.ts"])
        assert store.conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 0
        assert store.conn.execute("SELECT count(*) FROM entities").fetchone()[0] == 0
    finally:
        store.close()


# ---------- LIKE-wildcard escaping in edge cleanup ----------


def test_clear_file_does_not_delete_a_sibling_files_edges(tmp_path: Path) -> None:
    """A file path routinely contains `_`, which is a SQL LIKE wildcard. The
    edge-cleanup pattern `%:{path}:%` must not let clearing `test_resolver.py`
    also match (and delete) `testXresolver.py`'s edges -- that would silently
    drop real edges from an unrelated file on every incremental re-index."""
    from codegraph.uir import Edge

    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    try:
        store.upsert_edges(
            [
                Edge(src_id="py:tests/test_resolver.py:foo", dst_id="py:x:1", type="calls", line=1),
                Edge(src_id="py:tests/testXresolver.py:bar", dst_id="py:y:1", type="calls", line=1),
            ]
        )
        store.clear_file("tests/test_resolver.py")
        remaining = {r[0] for r in store.conn.execute("SELECT src_id FROM edges").fetchall()}
        assert remaining == {"py:tests/testXresolver.py:bar"}
    finally:
        store.close()


def test_clear_files_does_not_delete_a_sibling_files_edges(tmp_path: Path) -> None:
    """Bulk `clear_files` shares the same LIKE-wildcard hazard as `clear_file`;
    the escaped pattern must isolate `data_v1.py` from `dataXv1.py`."""
    from codegraph.uir import Edge

    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    try:
        store.upsert_edges(
            [
                Edge(src_id="py:a/data_v1.py:foo", dst_id="py:x:1", type="calls", line=1),
                Edge(src_id="py:a/dataXv1.py:bar", dst_id="py:y:1", type="calls", line=1),
            ]
        )
        store.clear_files(["a/data_v1.py"])
        remaining = {r[0] for r in store.conn.execute("SELECT src_id FROM edges").fetchall()}
        assert remaining == {"py:a/dataXv1.py:bar"}
    finally:
        store.close()


def test_escape_like_neutralizes_wildcards() -> None:
    from codegraph.graph.store import escape_like

    # `_`, `%`, and the escape char itself are all neutralized; other chars pass through.
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("50%") == "50\\%"
    assert escape_like("a\\b") == "a\\\\b"
    assert escape_like("plain/path.py") == "plain/path.py"
