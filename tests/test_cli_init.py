"""Tests for T18.2 — codegraph init one-shot setup."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.installer import registry as _registry
from typer.testing import CliRunner

runner = CliRunner()


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text)


@pytest.fixture
def patched_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Replace the 'claude' target with a tmp_path-backed config; isolate CWD."""
    from codegraph.installer.targets.claude_code import ClaudeCodeTarget

    t = ClaudeCodeTarget()
    cfg = tmp_path / ".claude.json"
    monkeypatch.setattr(t, "global_config_path", lambda: cfg)
    monkeypatch.setattr(t, "local_config_path", lambda: tmp_path / ".mcp.json")
    monkeypatch.chdir(tmp_path)
    orig = _registry._REGISTRY.copy()
    _registry._REGISTRY["claude"] = t
    yield t, cfg
    _registry._REGISTRY.clear()
    _registry._REGISTRY.update(orig)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    return repo


def test_init_indexes_installs_and_writes_guide(tmp_path: Path, patched_claude) -> None:
    _, cfg = patched_claude
    repo = _make_repo(tmp_path)

    result = runner.invoke(app, ["init", str(repo), "--no-embed"])
    assert result.exit_code == 0, result.output

    # 1. Index created inside the repo (enables walk-up discovery).
    assert (repo / ".codegraph" / "graph.duckdb").exists()
    # 2. MCP entry registered, with NO --db (discovery mode).
    entry = json.loads(cfg.read_text())["mcpServers"]["codegraph"]
    assert "--db" not in entry["args"]
    # 3. Agent guide written into the repo.
    guide = repo / "CLAUDE.md"
    assert guide.exists()
    assert "<!-- BEGIN CODEGRAPH -->" in guide.read_text(encoding="utf-8")


def test_init_prints_three_steps_and_next_steps(tmp_path: Path, patched_claude) -> None:
    repo = _make_repo(tmp_path)
    result = runner.invoke(app, ["init", str(repo), "--no-embed"])
    out = _plain(result.output)
    assert "Step 1/3" in out
    assert "Step 2/3" in out
    assert "Step 3/3" in out
    assert "Done." in out


def test_init_unknown_target_fails_before_indexing(tmp_path: Path, patched_claude) -> None:
    repo = _make_repo(tmp_path)
    result = runner.invoke(app, ["init", str(repo), "--target", "no_such_agent", "--no-embed"])
    assert result.exit_code == 1
    assert "Unknown target" in _plain(result.output)
    # Fail-fast: no index should have been created.
    assert not (repo / ".codegraph" / "graph.duckdb").exists()


def test_init_invalid_location_fails(tmp_path: Path, patched_claude) -> None:
    repo = _make_repo(tmp_path)
    result = runner.invoke(app, ["init", str(repo), "--location", "everywhere", "--no-embed"])
    assert result.exit_code == 1


def test_init_defaults_to_cwd(tmp_path: Path, patched_claude) -> None:
    """With no repo arg, init operates on the current directory."""
    # patched_claude already chdir'd into tmp_path; add a source file there.
    (tmp_path / "mod.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "--no-embed"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".codegraph" / "graph.duckdb").exists()
    assert (tmp_path / "CLAUDE.md").exists()
