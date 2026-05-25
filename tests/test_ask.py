"""Tests for T5.4 — `ask` streaming wiring + CLI.

The happy path is exercised without a live API or the embedding model: a fake
LLM and one-hot embeddings are injected. Error paths (missing DB / no embeddings)
use the real command.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from codegraph.ai.graphrag import GraphRAG, load_system_prompt
from codegraph.ai.llm import LLMError
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


_REPO = {
    "a.py": ("def authenticate():\n    return 1\n\ndef login():\n    return authenticate()\n"),
}


def _index_and_embed(runner: CliRunner, tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    for rel, content in _REPO.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0
    store = GraphStore(db)
    ids = {
        name: eid
        for eid, name in store.conn.execute("SELECT entity_id, name FROM entities").fetchall()
    }
    store.update_embeddings(
        [
            (ids["authenticate"], _onehot(0), "h0"),
            (ids["login"], _onehot(1), "h1"),
            (ids["a"], _onehot(2), "h2"),
        ]
    )
    store.close()
    return db, ids


class _CaptureLLM:
    """Fake LLM recording the system/user it was handed; streams fixed tokens."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.system: str | None = None
        self.user: str | None = None

    def stream(self, system: str, user: str, max_tokens: int = 2000):
        self.system = system
        self.user = user
        yield from self.tokens


# ---------- GraphRAG.ask_stream ----------


def test_ask_stream_assembles_prompt_and_streams(runner: CliRunner, tmp_path: Path) -> None:
    db, ids = _index_and_embed(runner, tmp_path)
    llm = _CaptureLLM(["The ", "answer."])
    store = GraphStore(db)
    try:
        rag = GraphRAG(store, llm=llm, embedder=lambda _q: _onehot(0))
        out = list(rag.ask_stream("how does auth work?", k=10))
    finally:
        store.close()
    assert out == ["The ", "answer."]
    assert llm.system == load_system_prompt()
    assert "QUESTION: how does auth work?" in llm.user
    assert ids["authenticate"] in llm.user  # retrieved entity made it into the context


def test_ask_stream_without_llm_raises(runner: CliRunner, tmp_path: Path) -> None:
    db, _ = _index_and_embed(runner, tmp_path)
    store = GraphStore(db)
    try:
        rag = GraphRAG(store, llm=None, embedder=lambda _q: _onehot(0))
        with pytest.raises(LLMError):
            list(rag.ask_stream("q"))
    finally:
        store.close()


# ---------- CLI ----------


class _FakeRAG:
    """Drop-in for GraphRAG used to test the CLI without a model or API key."""

    def __init__(self, store, llm=None, embedder=None) -> None:
        self.store = store

    def ask_stream(self, query: str, k: int = 15, max_tokens: int = 2000):
        yield "Login is handled by "
        yield "[py:a.py:authenticate]."


def test_cli_ask_streams_answer(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _ = _index_and_embed(runner, tmp_path)
    # Avoid the real model/API: fake the RAG and make LLM() construction a no-op.
    monkeypatch.setattr("codegraph.ai.graphrag.GraphRAG", _FakeRAG)
    monkeypatch.setattr("codegraph.ai.llm.LLM", lambda *a, **k: object())
    result = runner.invoke(app, ["ask", "how does login work?", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Login is handled by [py:a.py:authenticate]." in result.stdout


def test_cli_ask_no_embeddings_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo).mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    result = runner.invoke(app, ["ask", "anything?", "--db", str(db)])
    assert result.exit_code == 1
    assert "no embeddings" in result.stdout.lower()


def test_cli_ask_missing_db_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["ask", "q", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout


def test_cli_ask_llm_error_is_friendly(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _ = _index_and_embed(runner, tmp_path)

    class _BoomRAG:
        def __init__(self, *a, **k) -> None:
            pass

        def ask_stream(self, *a, **k):
            raise LLMError("ANTHROPIC_API_KEY is not set.")
            yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr("codegraph.ai.graphrag.GraphRAG", _BoomRAG)
    monkeypatch.setattr("codegraph.ai.llm.LLM", lambda *a, **k: object())
    result = runner.invoke(app, ["ask", "q", "--db", str(db)])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.stdout


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="live Anthropic API key not available",
)
def test_cli_ask_live(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "def authenticate(email, password):\n"
        '    """Check a user\'s credentials and log them in."""\n'
        "    return email and password\n",
        encoding="utf-8",
    )
    db = tmp_path / "g.duckdb"
    idx = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    if "Embeddings skipped" in idx.stdout:
        pytest.skip("embedding model unavailable")
    result = runner.invoke(app, ["ask", "how does authentication work?", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.strip()  # got some grounded answer text
