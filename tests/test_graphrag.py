"""Tests for T5.2 — hybrid graph + vector retrieval.

Model-free: entities are indexed with --no-embed, then given hand-crafted
one-hot embeddings via `update_embeddings`, so cosine similarity is exact and
deterministic without loading sentence-transformers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.ai.graphrag import GraphRAG, RetrievedEntity, _combined_score, retrieve
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner

_DIM = 384


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _onehot(i: int) -> list[float]:
    v = [0.0] * _DIM
    v[i] = 1.0
    return v


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


# login() calls authenticate(); unrelated() is disconnected.
_REPO = {
    "a.py": (
        "def authenticate():\n"
        "    return 1\n"
        "\n"
        "def login():\n"
        "    return authenticate()\n"
        "\n"
        "def unrelated():\n"
        "    return 2\n"
    ),
}


def _index_and_embed(runner: CliRunner, tmp_path: Path) -> tuple[GraphStore, dict[str, str]]:
    """Index the repo, then assign one-hot embeddings. Returns (store, name->entity_id)."""
    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0

    store = GraphStore(db)
    ids = {
        name: eid
        for eid, name in store.conn.execute("SELECT entity_id, name FROM entities").fetchall()
    }
    # authenticate → axis 0, login → axis 1, unrelated → axis 2, module → axis 3.
    store.update_embeddings(
        [
            (ids["authenticate"], _onehot(0), "h0"),
            (ids["login"], _onehot(1), "h1"),
            (ids["unrelated"], _onehot(2), "h2"),
            (ids["a"], _onehot(3), "h3"),  # module entity (qualified_name == "a")
        ]
    )
    return store, ids


# ---------- pure scoring ----------


def test_combined_score_similarity_only() -> None:
    assert _combined_score(sim=1.0, degree=0, recency=0.0, max_degree=0) == pytest.approx(0.6)


def test_combined_score_degree_only() -> None:
    # log1p(10)/log1p(10) == 1 → 0.3 * 1
    assert _combined_score(sim=0.0, degree=10, recency=0.0, max_degree=10) == pytest.approx(0.3)


def test_combined_score_higher_similarity_wins() -> None:
    hi = _combined_score(sim=0.9, degree=1, recency=0.0, max_degree=4)
    lo = _combined_score(sim=0.1, degree=1, recency=0.0, max_degree=4)
    assert hi > lo


def test_combined_score_clamps_negative_similarity() -> None:
    assert _combined_score(sim=-0.5, degree=0, recency=0.0, max_degree=0) == 0.0


# ---------- retrieval ----------


def test_seed_plus_graph_expansion(runner: CliRunner, tmp_path: Path) -> None:
    store, ids = _index_and_embed(runner, tmp_path)
    try:
        # pool=1 → only the top semantic match (authenticate) is a seed.
        hits = retrieve(store.conn, _onehot(0), k=10, pool=1)
    finally:
        store.close()
    by_id = {h.entity_id: h for h in hits}
    assert ids["authenticate"] in by_id
    assert by_id[ids["authenticate"]].via == "vector"
    # login calls authenticate → pulled in via 1-hop graph expansion, not vector.
    assert ids["login"] in by_id
    assert by_id[ids["login"]].via == "graph"
    # unrelated is neither a seed (pool=1) nor a neighbour → excluded.
    assert ids["unrelated"] not in by_id


def test_results_deduped(runner: CliRunner, tmp_path: Path) -> None:
    store, ids = _index_and_embed(runner, tmp_path)
    try:
        # pool=3 → authenticate + login both seeds; login is also a neighbour.
        hits = retrieve(store.conn, _onehot(0), k=10, pool=3)
    finally:
        store.close()
    occurrences = [h for h in hits if h.entity_id == ids["login"]]
    assert len(occurrences) == 1


def test_outbound_neighbors_recorded(runner: CliRunner, tmp_path: Path) -> None:
    store, ids = _index_and_embed(runner, tmp_path)
    try:
        hits = retrieve(store.conn, _onehot(1), k=10, pool=1)  # seed = login
    finally:
        store.close()
    login = next(h for h in hits if h.entity_id == ids["login"])
    assert ids["authenticate"] in login.neighbors


def test_top_seed_ranks_first(runner: CliRunner, tmp_path: Path) -> None:
    store, ids = _index_and_embed(runner, tmp_path)
    try:
        hits = retrieve(store.conn, _onehot(0), k=10, pool=10)
    finally:
        store.close()
    # authenticate has the only perfect similarity → must rank first.
    assert hits[0].entity_id == ids["authenticate"]


def test_k_truncates(runner: CliRunner, tmp_path: Path) -> None:
    store, _ = _index_and_embed(runner, tmp_path)
    try:
        hits = retrieve(store.conn, _onehot(0), k=2, pool=10)
    finally:
        store.close()
    assert len(hits) <= 2


def test_empty_query_vector_returns_empty(runner: CliRunner, tmp_path: Path) -> None:
    store, _ = _index_and_embed(runner, tmp_path)
    try:
        assert retrieve(store.conn, [], k=5) == []
    finally:
        store.close()


def test_no_embeddings_returns_empty(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    store = GraphStore(db)
    try:
        assert retrieve(store.conn, _onehot(0), k=5) == []  # nothing embedded
    finally:
        store.close()


def test_graphrag_wrapper_uses_injected_embedder(runner: CliRunner, tmp_path: Path) -> None:
    store, ids = _index_and_embed(runner, tmp_path)
    try:
        rag = GraphRAG(store, embedder=lambda _q: _onehot(0))
        hits = rag.retrieve("how does auth work?", k=10, pool=1)
    finally:
        store.close()
    assert any(h.entity_id == ids["authenticate"] for h in hits)
    assert all(isinstance(h, RetrievedEntity) for h in hits)
