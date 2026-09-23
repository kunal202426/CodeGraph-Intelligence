"""Tests for the `context_summaries` store -- agent-written file/directory
summaries, cached across sessions the same way `entities.summary` already is."""

from __future__ import annotations

from codegraph.graph.store import GraphStore
from codegraph.uir import Language


def _store(tmp_path) -> GraphStore:
    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    store.upsert_file("a.py", Language.PYTHON, "h1", loc=10)
    store.upsert_file("b.py", Language.PYTHON, "h2", loc=20)
    return store


def test_set_and_get_context_summaries_round_trip(tmp_path) -> None:
    store = _store(tmp_path)
    try:
        store.set_context_summaries(
            [
                ("file", "a.py", "Handles login and session validation.", "h1"),
                ("dir", ".", "Top-level entry point.", "dirhash1"),
            ]
        )
        files = store.get_context_summaries("file", ["a.py", "b.py"])
        dirs = store.get_context_summaries("dir", ["."])
    finally:
        store.close()
    assert files == {"a.py": ("Handles login and session validation.", "h1")}
    assert dirs == {".": ("Top-level entry point.", "dirhash1")}


def test_get_context_summaries_empty_scope_ids_returns_empty(tmp_path) -> None:
    store = _store(tmp_path)
    try:
        result = store.get_context_summaries("file", [])
    finally:
        store.close()
    assert result == {}


def test_get_context_summaries_missing_scope_id_absent_not_none(tmp_path) -> None:
    store = _store(tmp_path)
    try:
        store.set_context_summaries([("file", "a.py", "desc", "h1")])
        result = store.get_context_summaries("file", ["a.py", "does_not_exist.py"])
    finally:
        store.close()
    assert "does_not_exist.py" not in result
    assert "a.py" in result


def test_set_context_summaries_upserts_in_place(tmp_path) -> None:
    """Re-running store_summaries on the same scope updates the row instead of
    duplicating it -- a real risk with a composite-key table if the upsert
    isn't wired correctly (would look fine on first write, break on the
    second)."""
    store = _store(tmp_path)
    try:
        store.set_context_summaries([("file", "a.py", "first pass", "h1")])
        store.set_context_summaries([("file", "a.py", "revised, more accurate", "h1-v2")])
        result = store.get_context_summaries("file", ["a.py"])
        count = store.conn.execute(
            "SELECT COUNT(*) FROM context_summaries WHERE scope_type='file' AND scope_id='a.py'"
        ).fetchone()[0]
    finally:
        store.close()
    assert result["a.py"] == ("revised, more accurate", "h1-v2")
    assert count == 1


def test_set_context_summaries_empty_rows_is_a_noop(tmp_path) -> None:
    store = _store(tmp_path)
    try:
        store.set_context_summaries([])
        count = store.conn.execute("SELECT COUNT(*) FROM context_summaries").fetchone()[0]
    finally:
        store.close()
    assert count == 0


def test_context_summaries_scope_types_are_independent(tmp_path) -> None:
    """A 'file' and a 'dir' summary can share the same scope_id string (e.g.
    a file 'a.py' vs a directory literally named 'a.py' won't happen in
    practice, but '.' as a dir id and '.' never colliding with a file path is
    exactly the kind of edge case a composite key should just handle)."""
    store = _store(tmp_path)
    try:
        store.set_context_summaries([("dir", "x", "a dir called x", "dh")])
        store.set_context_summaries([("file", "x", "a file called x", "fh")])
        files = store.get_context_summaries("file", ["x"])
        dirs = store.get_context_summaries("dir", ["x"])
    finally:
        store.close()
    assert files == {"x": ("a file called x", "fh")}
    assert dirs == {"x": ("a dir called x", "dh")}
