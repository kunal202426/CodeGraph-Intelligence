"""Tests for T3.2 — embedding storage in DuckDB + cosine vector search."""

from __future__ import annotations

import numpy as np
import pytest
from codegraph.graph.queries import vector_search
from codegraph.graph.store import GraphStore
from codegraph.uir import EntityType, Language, UIREntity, hash_source

DIM = 384


def _entity(name: str, file: str = "a.py") -> UIREntity:
    return UIREntity(
        entity_id=f"py:{file}:{name}",
        type=EntityType.FUNCTION,
        name=name,
        qualified_name=name,
        language=Language.PYTHON,
        file=file,
        start_line=1,
        end_line=2,
        raw_source=f"def {name}(): ...\n",
        hash=hash_source(name),
    )


def _store_with_entities(tmp_path, names: list[str]) -> GraphStore:
    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    store.upsert_file("a.py", Language.PYTHON, "h", loc=1)
    store.upsert_entities([_entity(n) for n in names])
    return store


def _unit(vec: np.ndarray) -> list[float]:
    return (vec / np.linalg.norm(vec)).astype("float32").tolist()


# ---------- storage ----------


def test_update_embeddings_sets_count(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a", "b", "c"])
    try:
        assert store.count_embedded() == 0
        rng = np.random.default_rng(0)
        rows = [(f"py:a.py:{n}", _unit(rng.random(DIM)), f"h_{n}") for n in ("a", "b", "c")]
        store.update_embeddings(rows)
        assert store.count_embedded() == 3
    finally:
        store.close()


def test_update_embeddings_empty_is_noop(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a"])
    try:
        store.update_embeddings([])
        assert store.count_embedded() == 0
    finally:
        store.close()


def test_update_embeddings_stores_hash(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a"])
    try:
        rng = np.random.default_rng(1)
        store.update_embeddings([("py:a.py:a", _unit(rng.random(DIM)), "the_hash")])
        row = store.conn.execute(
            "SELECT embedding_hash FROM entities WHERE entity_id = 'py:a.py:a'"
        ).fetchone()
        assert row[0] == "the_hash"
    finally:
        store.close()


def test_update_embeddings_only_touches_named_rows(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a", "b"])
    try:
        rng = np.random.default_rng(2)
        store.update_embeddings([("py:a.py:a", _unit(rng.random(DIM)), "h")])
        assert store.count_embedded() == 1  # only `a`
    finally:
        store.close()


# ---------- search ----------


def test_vector_search_self_match_ranks_first(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a", "b", "c"])
    try:
        rng = np.random.default_rng(3)
        vecs = {n: _unit(rng.random(DIM)) for n in ("a", "b", "c")}
        store.update_embeddings([(f"py:a.py:{n}", vecs[n], f"h_{n}") for n in vecs])
        hits = vector_search(store.conn, vecs["b"], limit=3)
        assert hits[0].entity_id == "py:a.py:b"
        assert hits[0].similarity == pytest.approx(1.0, abs=1e-4)
        assert len(hits) == 3
    finally:
        store.close()


def test_vector_search_respects_limit(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a", "b", "c", "d", "e"])
    try:
        rng = np.random.default_rng(4)
        store.update_embeddings(
            [(f"py:a.py:{n}", _unit(rng.random(DIM)), "h") for n in ("a", "b", "c", "d", "e")]
        )
        hits = vector_search(store.conn, _unit(rng.random(DIM)), limit=2)
        assert len(hits) == 2
    finally:
        store.close()


def test_vector_search_empty_query_returns_empty(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a"])
    try:
        assert vector_search(store.conn, [], limit=5) == []
    finally:
        store.close()


def test_vector_search_ignores_unembedded_entities(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["a", "b"])
    try:
        rng = np.random.default_rng(5)
        store.update_embeddings([("py:a.py:a", _unit(rng.random(DIM)), "h")])
        hits = vector_search(store.conn, _unit(rng.random(DIM)), limit=10)
        assert {h.entity_id for h in hits} == {"py:a.py:a"}
    finally:
        store.close()


def test_vector_search_returns_hit_metadata(tmp_path) -> None:
    store = _store_with_entities(tmp_path, ["authenticate"])
    try:
        rng = np.random.default_rng(6)
        store.update_embeddings([("py:a.py:authenticate", _unit(rng.random(DIM)), "h")])
        hit = vector_search(store.conn, _unit(rng.random(DIM)), limit=1)[0]
        assert hit.name == "authenticate"
        assert hit.file == "a.py"
        assert hit.type == "function"
        assert -1.0 <= hit.similarity <= 1.0
    finally:
        store.close()


# ---------- integration with the real embedder ----------


def test_real_embedding_roundtrip(tmp_path) -> None:
    """Store real embeddings, search by a semantically-near query."""
    try:
        from codegraph.embeddings.pipeline import embed_one
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"embeddings unavailable: {exc}")

    store = _store_with_entities(tmp_path, ["authenticate", "render_template", "parse_csv"])
    try:
        texts = {
            "authenticate": "validate user credentials and create a login session",
            "render_template": "render an HTML template with the given context",
            "parse_csv": "read and parse rows from a comma-separated values file",
        }
        try:
            rows = [(f"py:a.py:{n}", embed_one(t).tolist(), "h") for n, t in texts.items()]
        except Exception as exc:  # noqa: BLE001 - model download failure
            pytest.skip(f"embedding model unavailable: {exc}")
        store.update_embeddings(rows)

        query = embed_one("log in a user by checking their password").tolist()
        hits = vector_search(store.conn, query, limit=3)
        assert hits[0].name == "authenticate"
    finally:
        store.close()
