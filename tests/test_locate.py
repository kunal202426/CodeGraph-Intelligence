"""Tests for T16.1 — walk-up DB discovery."""

from __future__ import annotations

from pathlib import Path

from codegraph.graph.locate import DEFAULT_DB_RELPATH, discover_db


def _make_index(root: Path) -> Path:
    db = root / DEFAULT_DB_RELPATH
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("", encoding="utf-8")
    return db


def test_discovers_in_same_dir(tmp_path: Path) -> None:
    db = _make_index(tmp_path)
    assert discover_db(tmp_path) == db


def test_discovers_from_nested_subdir(tmp_path: Path) -> None:
    db = _make_index(tmp_path)
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert discover_db(nested) == db


def test_returns_none_when_absent(tmp_path: Path) -> None:
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    assert discover_db(nested) is None


def test_finds_nearest_not_ancestor(tmp_path: Path) -> None:
    # Index at the root AND at a nested project; nearest (nested) should win.
    _make_index(tmp_path)
    nested_proj = tmp_path / "packages" / "sub"
    nested_proj.mkdir(parents=True)
    nearer = _make_index(nested_proj)
    deeper = nested_proj / "src"
    deeper.mkdir()
    assert discover_db(deeper) == nearer


def test_defaults_to_cwd(tmp_path: Path, monkeypatch) -> None:
    db = _make_index(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert discover_db() == db
