"""Tests for T18.1 — first-run model-download legibility."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
from codegraph.cli import app
from codegraph.embeddings.pipeline import EMBEDDING_DIM, model_is_cached
from typer.testing import CliRunner

runner = CliRunner()


def test_model_is_cached_returns_bool() -> None:
    assert isinstance(model_is_cached(), bool)


def _fake_embed_batch(texts: list[str], *_a, **_k) -> np.ndarray:
    return np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)


def test_index_prints_download_notice_when_uncached(tmp_path: Path) -> None:
    """When the model is not cached, `index` warns before the silent download."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "g.duckdb"

    with (
        patch("codegraph.embeddings.pipeline.model_is_cached", return_value=False),
        patch("codegraph.embeddings.pipeline.embed_batch", side_effect=_fake_embed_batch),
    ):
        result = runner.invoke(app, ["index", str(repo), "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "Downloading embedding model" in result.output


def test_index_no_notice_when_cached(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "g.duckdb"

    with (
        patch("codegraph.embeddings.pipeline.model_is_cached", return_value=True),
        patch("codegraph.embeddings.pipeline.embed_batch", side_effect=_fake_embed_batch),
    ):
        result = runner.invoke(app, ["index", str(repo), "--db", str(db)])

    assert result.exit_code == 0
    assert "Downloading embedding model" not in result.output


def test_index_no_notice_when_no_embed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "g.duckdb"
    result = runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0
    assert "Downloading embedding model" not in result.output
