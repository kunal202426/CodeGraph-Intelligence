"""Tests for T3.3 — embedding-input chunking + auto-embed during index."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.embeddings.chunking import (
    build_embed_input,
    build_embed_input_from_fields,
    embed_input_hash,
)
from codegraph.graph.store import GraphStore
from codegraph.uir import EntityType, Language, UIREntity, hash_source
from typer.testing import CliRunner

SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------- build_embed_input ----------


def test_includes_type_and_qualified_name() -> None:
    text = build_embed_input_from_fields("function", "auth.login.authenticate", None, None, None)
    assert text == "function auth.login.authenticate"


def test_includes_signature_and_docstring_and_body() -> None:
    text = build_embed_input_from_fields(
        "function",
        "f",
        "def f(x: int) -> int",
        "Doubles x.",
        "def f(x: int) -> int:\n    return x * 2\n",
    )
    assert "function f" in text
    assert "def f(x: int) -> int" in text
    assert "Doubles x." in text
    assert "return x * 2" in text


def test_body_is_truncated_to_1500_chars() -> None:
    big = "x = 1\n" * 1000  # ~6000 chars
    text = build_embed_input_from_fields("module", "m", None, None, big)
    # header "module m\n" (9) + up to 1500 body chars
    assert len(text) <= 9 + 1500 + 1


def test_build_embed_input_from_entity() -> None:
    e = UIREntity(
        entity_id="py:a.py:f",
        type=EntityType.FUNCTION,
        name="f",
        qualified_name="f",
        language=Language.PYTHON,
        file="a.py",
        start_line=1,
        end_line=2,
        raw_source="def f(): return 1\n",
        signature="def f()",
        docstring="A function.",
        hash=hash_source("x"),
    )
    text = build_embed_input(e)
    assert text.startswith("function f")
    assert "def f()" in text
    assert "A function." in text


def test_embed_input_hash_is_deterministic() -> None:
    a = embed_input_hash("function f\ndef f(): ...")
    b = embed_input_hash("function f\ndef f(): ...")
    assert a == b
    assert len(a) == 64


# ---------- CLI auto-embed ----------


def test_index_no_embed_leaves_zero_embeddings(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0
    store = GraphStore(db)
    try:
        assert store.count_embedded() == 0
        assert store.count_entities() > 0
    finally:
        store.close()


def test_index_embeds_all_entities(runner: CliRunner, tmp_path: Path) -> None:
    """With embeddings on, every entity gets a vector (skip if model unavailable)."""
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0

    store = GraphStore(db)
    try:
        if "Embeddings skipped" in result.stdout:
            pytest.skip("embedding model unavailable in this environment")
        assert store.count_embedded() == store.count_entities()
        assert "Embedded" in result.stdout
    finally:
        store.close()


def test_semantic_search_after_index(runner: CliRunner, tmp_path: Path) -> None:
    """End-to-end: index → vector_search retrieves a semantically-relevant entity."""
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0
    if "Embeddings skipped" in result.stdout:
        pytest.skip("embedding model unavailable in this environment")

    from codegraph.embeddings.pipeline import embed_one
    from codegraph.graph.queries import vector_search

    store = GraphStore(db)
    try:
        query = embed_one("check a user's password and log them in").tolist()
        hits = vector_search(store.conn, query, limit=5)
        names = [h.name for h in hits]
        assert "authenticate" in names
    finally:
        store.close()
