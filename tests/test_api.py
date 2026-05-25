"""Tests for T6.1 — FastAPI endpoints.

Indexes the sample repo with --no-embed (no model), builds the app over that DB,
and exercises every endpoint with the FastAPI TestClient. The /ask happy path is
driven by a monkeypatched GraphRAG so it needs no live API or embedding model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app as cli_app
from codegraph.graph.store import GraphStore
from codegraph.server.api import create_app
from fastapi.testclient import TestClient
from typer.testing import CliRunner

SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")
_DIM = 384


@pytest.fixture
def db(tmp_path: Path) -> Path:
    out = tmp_path / "g.duckdb"
    runner = CliRunner()
    assert (
        runner.invoke(
            cli_app, ["index", str(SAMPLE_REPO), "--db", str(out), "--no-embed"]
        ).exit_code
        == 0
    )
    return out


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _onehot(i: int) -> list[float]:
    v = [0.0] * _DIM
    v[i] = 1.0
    return v


# ---------- health ----------


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_missing_db_returns_503(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "nope.duckdb"))
    assert client.get("/api/graph").status_code == 503


# ---------- graph ----------


def test_module_graph(client: TestClient) -> None:
    data = client.get("/api/graph?type=module").json()
    labels = {n["label"] for n in data["nodes"]}
    assert "main.py" in labels  # file path is the label
    # Nodes are keyed by module entity_id so the UI can fetch /api/entity.
    assert all(n["id"].startswith(("py:", "ts:", "js:")) for n in data["nodes"])
    assert data["edges"]  # imports edges exist between files
    assert all({"source", "target", "type"} <= e.keys() for e in data["edges"])
    # Edge endpoints reference node ids (entity_ids), not raw file paths.
    node_ids = {n["id"] for n in data["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in data["edges"])


def test_entity_graph_for_file(client: TestClient) -> None:
    data = client.get("/api/graph?type=entity&file=auth/login.py").json()
    labels = {n["label"] for n in data["nodes"]}
    assert "authenticate" in labels


def test_entity_graph_requires_file(client: TestClient) -> None:
    assert client.get("/api/graph?type=entity").status_code == 400


# ---------- search ----------


def test_search_literal(client: TestClient) -> None:
    data = client.get("/api/search?q=authenticate").json()
    names = {h["name"] for h in data["results"]}
    assert "authenticate" in names


def test_search_requires_query(client: TestClient) -> None:
    assert client.get("/api/search?q=").status_code == 422  # min_length=1


# ---------- entity ----------


def test_entity_lookup(client: TestClient) -> None:
    eid = client.get("/api/search?q=authenticate").json()["results"][0]["entity_id"]
    r = client.get(f"/api/entity/{eid}")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_id"] == eid
    assert body["name"] == "authenticate"
    assert "raw_source" in body


def test_entity_not_found(client: TestClient) -> None:
    assert client.get("/api/entity/py:nope.py:ghost").status_code == 404


# ---------- impact ----------


def test_impact(client: TestClient) -> None:
    eid = client.get("/api/search?q=authenticate").json()["results"][0]["entity_id"]
    data = client.get(f"/api/impact/{eid}").json()
    assert data["root"] == eid
    assert data["total"] >= 1  # authenticate has callers in the fixture
    # The root's direct callers are listed under its id.
    assert any(eid in callers_map for callers_map in [data["callers"]])


# ---------- ask (SSE) ----------


def test_ask_no_embeddings_streams_error(client: TestClient) -> None:
    r = client.post("/api/ask", json={"query": "how does login work?"})
    assert r.status_code == 200
    assert "embeddings" in r.text.lower()


def test_ask_streams_tokens(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Give the index embeddings so the endpoint proceeds past the guard.
    store = GraphStore(db)
    ids = {
        name: eid
        for eid, name in store.conn.execute("SELECT entity_id, name FROM entities").fetchall()
    }
    store.update_embeddings([(next(iter(ids.values())), _onehot(0), "h0")])
    store.close()

    class _FakeRAG:
        def __init__(self, store, llm=None, embedder=None) -> None:
            pass

        def ask_stream(self, query, k=15, max_tokens=2000):
            yield "Auth is in "
            yield "[py:auth/login.py:authenticate]."

    monkeypatch.setattr("codegraph.ai.graphrag.GraphRAG", _FakeRAG)
    monkeypatch.setattr("codegraph.ai.llm.LLM", lambda *a, **k: object())

    client = TestClient(create_app(db))
    r = client.post("/api/ask", json={"query": "auth?"})
    assert r.status_code == 200
    assert "py:auth/login.py:authenticate" in r.text
    assert '"done": true' in r.text
