"""Tests for T5.5 — multi-pass repo architecture summary.

`select_representatives` is degree-based (no embeddings), and `summarize` is
driven by a fake LLM, so nothing here needs the model or a live API.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.ai.graphrag import GraphRAG, select_representatives
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner

SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _index(runner: CliRunner, repo: Path, db: Path) -> None:
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0


class _FakeLLM:
    """Records each complete() call and returns a unique marker per call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        self.calls.append(user)
        return f"SUMMARY_{len(self.calls)}"


# ---------- select_representatives ----------


def test_representatives_grouped_by_top_dir(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    _index(runner, SAMPLE_REPO, db)
    store = GraphStore(db)
    try:
        groups = select_representatives(store.conn, per_dir=10)
    finally:
        store.close()
    # sample_repo_py has auth/, api/, db/ and a root-level main.py.
    assert set(groups) == {"auth", "api", "db", "."}
    assert all(groups.values())  # every group has at least one entity


def test_representatives_sorted_by_degree_and_capped(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    _index(runner, SAMPLE_REPO, db)
    store = GraphStore(db)
    try:
        groups = select_representatives(store.conn, per_dir=2)
    finally:
        store.close()
    for entities in groups.values():
        assert len(entities) <= 2
        degrees = [e.degree for e in entities]
        assert degrees == sorted(degrees, reverse=True)


def test_representatives_empty_store(tmp_path: Path) -> None:
    store = GraphStore(tmp_path / "empty.duckdb")
    store.init_schema()
    try:
        assert select_representatives(store.conn) == {}
    finally:
        store.close()


# ---------- GraphRAG.summarize ----------


def test_summarize_multipass_and_markdown(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    _index(runner, SAMPLE_REPO, db)
    llm = _FakeLLM()
    store = GraphStore(db)
    try:
        md = GraphRAG(store, llm=llm).summarize(per_dir=5)
    finally:
        store.close()
    # 4 directories → 4 subsystem calls + 1 final synthesis call.
    assert len(llm.calls) == 5
    assert md.startswith("# Architecture Summary")
    assert "## Subsystems" in md
    assert "### (root)" in md  # the '.' group is rendered as (root)
    assert "### auth" in md
    assert "SUMMARY_5" in md  # the final overview (last call) is the document overview


def test_summarize_empty_store_no_llm_calls(tmp_path: Path) -> None:
    store = GraphStore(tmp_path / "empty.duckdb")
    store.init_schema()
    llm = _FakeLLM()
    try:
        md = GraphRAG(store, llm=llm).summarize()
    finally:
        store.close()
    assert llm.calls == []  # nothing to summarize → no LLM calls
    assert "No indexed entities" in md


# ---------- CLI ----------


class _FakeRAG:
    def __init__(self, store, llm=None, embedder=None) -> None:
        pass

    def summarize(self, per_dir: int = 10, max_tokens: int = 1000) -> str:
        return (
            "# Architecture Summary\n\nA tiny system.\n\n## Subsystems\n\n### auth\nAuth stuff.\n"
        )


def test_cli_summarize_writes_file(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "g.duckdb"
    _index(runner, SAMPLE_REPO, db)
    monkeypatch.setattr("codegraph.ai.graphrag.GraphRAG", _FakeRAG)
    monkeypatch.setattr("codegraph.ai.llm.LLM", lambda *a, **k: object())
    out = tmp_path / "SUMMARY.md"
    result = runner.invoke(app, ["summarize", "--db", str(db), "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    assert out.exists()
    assert "# Architecture Summary" in out.read_text(encoding="utf-8")
    assert "Wrote architecture summary" in result.stdout


def test_cli_summarize_missing_db(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["summarize", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout


def test_cli_summarize_no_entities(runner: CliRunner, tmp_path: Path) -> None:
    empty = tmp_path / "empty_repo"
    empty.mkdir()
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["index", str(empty), "--db", str(db), "--no-embed"])
    result = runner.invoke(app, ["summarize", "--db", str(db)])
    assert result.exit_code == 1
    assert "Nothing indexed" in result.stdout
