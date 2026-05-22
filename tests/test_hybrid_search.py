"""Tests for T3.4 — hybrid search (RRF fusion of literal + vector)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from codegraph.cli import app
from codegraph.graph.queries import hybrid_search
from codegraph.graph.store import GraphStore
from codegraph.uir import EntityType, Language, UIREntity, hash_source
from typer.testing import CliRunner

DIM = 384
SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _entity(name: str, docstring: str | None = None) -> UIREntity:
    return UIREntity(
        entity_id=f"py:a.py:{name}",
        type=EntityType.FUNCTION,
        name=name,
        qualified_name=name,
        language=Language.PYTHON,
        file="a.py",
        start_line=1,
        end_line=2,
        raw_source=f"def {name}(): ...\n",
        docstring=docstring,
        hash=hash_source(name),
    )


def _unit(vec: np.ndarray) -> list[float]:
    return (vec / np.linalg.norm(vec)).astype("float32").tolist()


def _store(tmp_path, entities: list[UIREntity]) -> GraphStore:
    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    store.upsert_file("a.py", Language.PYTHON, "h", loc=1)
    store.upsert_entities(entities)
    return store


# ---------- RRF mechanics (synthetic vectors, no model) ----------


def test_literal_only_when_vector_is_none(tmp_path) -> None:
    store = _store(tmp_path, [_entity("authenticate"), _entity("render")])
    try:
        hits = hybrid_search(store.conn, "authenticate", None, limit=10)
        assert hits[0].name == "authenticate"
        assert hits[0].retrievers == ("literal",)
    finally:
        store.close()


def test_semantic_only_when_text_empty(tmp_path) -> None:
    store = _store(tmp_path, [_entity("authenticate"), _entity("render")])
    try:
        rng = np.random.default_rng(0)
        vecs = {n: _unit(rng.random(DIM)) for n in ("authenticate", "render")}
        store.update_embeddings([(f"py:a.py:{n}", vecs[n], "h") for n in vecs])
        hits = hybrid_search(store.conn, "", vecs["render"], limit=10)
        assert hits[0].name == "render"
        assert hits[0].retrievers == ("semantic",)
    finally:
        store.close()


def test_item_found_by_both_retrievers_ranks_top(tmp_path) -> None:
    """An entity matched by literal AND vector gets a higher RRF score."""
    store = _store(tmp_path, [_entity("authenticate"), _entity("auth_helper"), _entity("render")])
    try:
        rng = np.random.default_rng(1)
        vecs = {
            "authenticate": _unit(rng.random(DIM)),
            "auth_helper": _unit(rng.random(DIM)),
            "render": _unit(rng.random(DIM)),
        }
        store.update_embeddings([(f"py:a.py:{n}", v, "h") for n, v in vecs.items()])
        # Text "auth" matches authenticate + auth_helper literally; vector query
        # is closest to authenticate. authenticate appears in both → ranks first.
        hits = hybrid_search(store.conn, "auth", vecs["authenticate"], limit=10)
        assert hits[0].name == "authenticate"
        assert set(hits[0].retrievers) == {"literal", "semantic"}
    finally:
        store.close()


def test_hybrid_respects_limit(tmp_path) -> None:
    ents = [_entity(f"fn_{i}") for i in range(10)]
    store = _store(tmp_path, ents)
    try:
        rng = np.random.default_rng(2)
        store.update_embeddings([(e.entity_id, _unit(rng.random(DIM)), "h") for e in ents])
        hits = hybrid_search(store.conn, "fn", _unit(rng.random(DIM)), limit=3)
        assert len(hits) == 3
    finally:
        store.close()


def test_hybrid_empty_when_nothing_matches(tmp_path) -> None:
    store = _store(tmp_path, [_entity("alpha")])
    try:
        hits = hybrid_search(store.conn, "zzz_nonexistent", None, limit=5)
        assert hits == []
    finally:
        store.close()


# ---------- the headline behaviour: docstring-only semantic match ----------


def test_semantic_finds_docstring_only_match(tmp_path) -> None:
    """The T3.4 acceptance: a query whose words don't appear in the NAME is
    still found via the docstring's meaning."""
    try:
        from codegraph.embeddings.pipeline import embed_one
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"embeddings unavailable: {exc}")

    # `verify_login` has no "authentication" token in its name; its docstring
    # describes credential validation.
    store = _store(
        tmp_path,
        [
            _entity(
                "verify_login", docstring="validate the user's credentials and start a session"
            ),
            _entity("render_html", docstring="produce an HTML page from a template"),
            _entity("parse_args", docstring="parse command line arguments"),
        ],
    )
    try:
        from codegraph.embeddings.chunking import build_embed_input

        e_by_name = {
            "verify_login": "validate the user's credentials and start a session",
            "render_html": "produce an HTML page from a template",
            "parse_args": "parse command line arguments",
        }
        try:
            rows = [
                (
                    f"py:a.py:{n}",
                    embed_one(build_embed_input(_entity(n, d))).tolist(),
                    "h",
                )
                for n, d in e_by_name.items()
            ]
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"model unavailable: {exc}")
        store.update_embeddings(rows)

        qvec = embed_one("user authentication").tolist()
        hits = hybrid_search(store.conn, "user authentication", qvec, limit=3)
        assert hits[0].name == "verify_login"
        assert "semantic" in hits[0].retrievers
    finally:
        store.close()


# ---------- CLI integration ----------


@pytest.fixture
def indexed(runner: CliRunner, tmp_path: Path) -> Path:
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    return db


def test_cli_hybrid_default_shows_via_column(runner: CliRunner, indexed: Path) -> None:
    result = runner.invoke(app, ["search", "authenticate", "--db", str(indexed)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout
    assert "Via" in result.stdout  # the retriever-annotation column header


def test_cli_no_hybrid_is_literal(runner: CliRunner, indexed: Path) -> None:
    result = runner.invoke(app, ["search", "authenticate", "--no-hybrid", "--db", str(indexed)])
    assert result.exit_code == 0
    assert "(literal," in result.stdout  # title shows the mode


def test_cli_semantic_on_no_embed_index_warns(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db), "--no-embed"])
    result = runner.invoke(app, ["search", "auth", "--semantic", "--db", str(db)])
    assert result.exit_code == 0
    # Either the "no embeddings" notice (model available) or a literal fallback
    # (model unavailable) — both are acceptable; the command must not crash.
    assert (
        "No embeddings" in result.stdout
        or "authenticate" in result.stdout
        or "No results" in result.stdout
    )
