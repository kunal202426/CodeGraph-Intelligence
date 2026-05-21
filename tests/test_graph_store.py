"""Tests for GraphStore — schema init, round-trips, idempotency."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from codegraph.graph.store import GraphStore
from codegraph.uir import Edge, EntityType, Language, UIREntity, hash_source, make_entity_id


def _sample_entity(name: str = "authenticate", file: str = "auth/login.py") -> UIREntity:
    raw = f"def {name}(): pass\n"
    return UIREntity(
        entity_id=make_entity_id(Language.PYTHON, file, name),
        type=EntityType.FUNCTION,
        name=name,
        qualified_name=name,
        language=Language.PYTHON,
        file=file,
        start_line=1,
        end_line=1,
        raw_source=raw,
        hash=hash_source(raw),
    )


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    db_path = tmp_path / "graph.duckdb"
    s = GraphStore(db_path)
    s.init_schema()
    yield s
    s.close()


# ---------- schema ----------


def test_init_schema_creates_three_tables(store: GraphStore) -> None:
    tables = {
        row[0]
        for row in store.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert {"files", "entities", "edges"}.issubset(tables)


def test_init_schema_is_idempotent(store: GraphStore) -> None:
    # Calling a second time must not raise (uses CREATE IF NOT EXISTS).
    store.init_schema()
    store.init_schema()


def test_creates_parent_dir_for_db_path(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "deeper" / "graph.duckdb"
    s = GraphStore(nested)
    try:
        assert nested.parent.is_dir()
    finally:
        s.close()


# ---------- files ----------


def test_upsert_file_roundtrip(store: GraphStore) -> None:
    store.upsert_file("auth/login.py", Language.PYTHON, "deadbeef", loc=42)
    row = store.conn.execute("SELECT path, language, hash, loc FROM files").fetchone()
    assert row == ("auth/login.py", "python", "deadbeef", 42)
    assert store.count_files() == 1


def test_upsert_file_replaces_existing(store: GraphStore) -> None:
    store.upsert_file("a.py", Language.PYTHON, "h1", loc=10)
    store.upsert_file("a.py", Language.PYTHON, "h2", loc=20)
    assert store.count_files() == 1
    row = store.conn.execute("SELECT hash, loc FROM files WHERE path='a.py'").fetchone()
    assert row == ("h2", 20)


# ---------- entities ----------


def test_upsert_entities_basic(store: GraphStore) -> None:
    store.upsert_file("auth/login.py", Language.PYTHON, "h", loc=1)
    store.upsert_entities([_sample_entity()])
    assert store.count_entities() == 1
    row = store.conn.execute(
        "SELECT name, qualified_name, type, language, file FROM entities"
    ).fetchone()
    assert row == ("authenticate", "authenticate", "function", "python", "auth/login.py")


def test_upsert_entities_idempotent_on_entity_id(store: GraphStore) -> None:
    store.upsert_file("auth/login.py", Language.PYTHON, "h", loc=1)
    e = _sample_entity()
    store.upsert_entities([e, e])
    assert store.count_entities() == 1


def test_upsert_entities_replaces_raw_source(store: GraphStore) -> None:
    store.upsert_file("auth/login.py", Language.PYTHON, "h", loc=1)
    first = _sample_entity()
    second = first.model_copy(update={"raw_source": "def authenticate(): return 1\n"})
    store.upsert_entities([first])
    store.upsert_entities([second])
    raw = store.conn.execute(
        "SELECT raw_source FROM entities WHERE entity_id = ?", [first.entity_id]
    ).fetchone()[0]
    assert "return 1" in raw


def test_upsert_entities_empty_list_is_noop(store: GraphStore) -> None:
    store.upsert_entities([])  # must not raise
    assert store.count_entities() == 0


def test_entity_requires_file_row(store: GraphStore) -> None:
    """FK constraint: entity.file must reference an existing files.path."""
    with pytest.raises(duckdb.Error):
        store.upsert_entities([_sample_entity()])  # no upsert_file first


# ---------- edges ----------


def test_upsert_edges_basic(store: GraphStore) -> None:
    edges = [
        Edge(src_id="py:a.py:foo", dst_id="py:b.py:bar", type="calls", line=10),
        Edge(src_id="py:a.py:foo", dst_id="py:c.py:baz", type="imports", line=2),
    ]
    store.upsert_edges(edges)
    assert store.count_edges() == 2


def test_upsert_edges_idempotent_on_pk(store: GraphStore) -> None:
    e = Edge(src_id="py:a.py:foo", dst_id="py:b.py:bar", type="calls", line=10)
    store.upsert_edges([e, e, e])
    assert store.count_edges() == 1


def test_upsert_edges_empty_list_is_noop(store: GraphStore) -> None:
    store.upsert_edges([])
    assert store.count_edges() == 0


def test_edges_with_same_dst_diff_line_both_kept(store: GraphStore) -> None:
    edges = [
        Edge(src_id="py:a:f", dst_id="py:b:g", type="calls", line=10),
        Edge(src_id="py:a:f", dst_id="py:b:g", type="calls", line=20),  # diff line
    ]
    store.upsert_edges(edges)
    assert store.count_edges() == 2


# ---------- bulk at scale (T1.5) ----------
#
# NOTE: kept at N=50 entities / N=100 edges deliberately. DuckDB's
# parameterized executemany has ~25 ms/row overhead in this version (1.5.x)
# and the fast paths (Arrow / DataFrame) require pandas or pyarrow as deps,
# which we don't pull at MVP. Larger sizes would just slow CI without adding
# coverage. See STATUS.md "Plan deviations" for the perf note.


def test_bulk_upsert_50_entities_then_reinsert(store: GraphStore) -> None:
    store.upsert_file("bulk/mod.py", Language.PYTHON, "h", loc=50)
    entities = [_sample_entity(name=f"func_{i:03d}", file="bulk/mod.py") for i in range(50)]
    store.upsert_entities(entities)
    assert store.count_entities() == 50
    store.upsert_entities(entities)
    assert store.count_entities() == 50


def test_bulk_upsert_replaces_subset_raw_source(store: GraphStore) -> None:
    store.upsert_file("bulk/mod.py", Language.PYTHON, "h", loc=50)
    originals = [_sample_entity(name=f"func_{i:03d}", file="bulk/mod.py") for i in range(50)]
    store.upsert_entities(originals)

    # Mutate every 5th entity: 10 of 50.
    mutated_indices = set(range(0, 50, 5))
    new_batch = [
        e.model_copy(update={"raw_source": f"def {e.name}(): return 'changed'\n"})
        if i in mutated_indices
        else e
        for i, e in enumerate(originals)
    ]
    store.upsert_entities(new_batch)
    assert store.count_entities() == 50

    rows = store.conn.execute("SELECT name, raw_source FROM entities ORDER BY name").fetchall()
    changed_count = sum(1 for _, raw in rows if "changed" in raw)
    assert changed_count == len(mutated_indices) == 10


def test_bulk_upsert_100_edges_idempotent(store: GraphStore) -> None:
    edges = [
        Edge(
            src_id=f"py:a.py:f_{i:03d}",
            dst_id=f"py:b.py:g_{(i % 25):03d}",
            type="calls",
            line=(i % 30) + 1,
        )
        for i in range(100)
    ]
    store.upsert_edges(edges)
    first = store.count_edges()
    store.upsert_edges(edges)
    assert store.count_edges() == first


# ---------- lifecycle ----------


def test_context_manager_closes(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    with GraphStore(db) as s:
        s.init_schema()
    # After exit, the connection should be closed; reopening must work.
    s2 = GraphStore(db)
    try:
        # Tables should still exist (persisted file).
        tables = {
            row[0]
            for row in s2.conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert "entities" in tables
    finally:
        s2.close()
