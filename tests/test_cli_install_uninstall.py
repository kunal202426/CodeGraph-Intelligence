"""Tests for T13.3 — codegraph install / uninstall CLI commands."""

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


# ---------------------------------------------------------------------------
# Fixture: a patched target whose config paths land in tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Replace the 'claude' registry entry with a tmp_path-backed instance."""
    from codegraph.installer.targets.claude_code import ClaudeCodeTarget

    t = ClaudeCodeTarget()
    cfg = tmp_path / ".claude.json"
    monkeypatch.setattr(t, "global_config_path", lambda: cfg)
    local_cfg = tmp_path / ".mcp.json"
    monkeypatch.setattr(t, "local_config_path", lambda: local_cfg)
    orig = _registry._REGISTRY.copy()
    _registry._REGISTRY["claude"] = t
    yield t, cfg, local_cfg
    _registry._REGISTRY.clear()
    _registry._REGISTRY.update(orig)


# ---------------------------------------------------------------------------
# install — --print-config (dry-run)
# ---------------------------------------------------------------------------


def test_install_print_config_outputs_json(tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    result = runner.invoke(app, ["install", "claude", "--db", str(db), "--print-config"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert "mcpServers" in data
    assert "codegraph" in data["mcpServers"]
    assert "--db" in data["mcpServers"]["codegraph"]["args"]


def test_install_print_config_does_not_write(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["install", "claude", "--db", str(db), "--print-config"])
    assert not cfg.exists()


# ---------------------------------------------------------------------------
# install — --yes (non-interactive)
# ---------------------------------------------------------------------------


def test_install_yes_creates_config(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    result = runner.invoke(app, ["install", "claude", "--db", str(db), "--yes"])
    assert result.exit_code == 0, result.output
    assert cfg.exists()
    data = json.loads(cfg.read_text())
    assert "mcpServers" in data
    assert "codegraph" in data["mcpServers"]


def test_install_yes_prints_installed(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    result = runner.invoke(app, ["install", "claude", "--db", str(db), "--yes"])
    assert "Installed" in _plain(result.output)


def test_install_yes_idempotent(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["install", "claude", "--db", str(db), "--yes"])
    result = runner.invoke(app, ["install", "claude", "--db", str(db), "--yes"])
    assert result.exit_code == 0
    data = json.loads(cfg.read_text())
    assert len([k for k in data["mcpServers"] if k == "codegraph"]) == 1


# ---------------------------------------------------------------------------
# install — interactive prompt
# ---------------------------------------------------------------------------


def test_install_confirm_y_proceeds(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    result = runner.invoke(app, ["install", "claude", "--db", str(db)], input="y\n")
    assert result.exit_code == 0
    assert cfg.exists()


def test_install_confirm_n_aborts(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    result = runner.invoke(app, ["install", "claude", "--db", str(db)], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in _plain(result.output)
    assert not cfg.exists()


# ---------------------------------------------------------------------------
# install — local scope
# ---------------------------------------------------------------------------


def test_install_local_writes_local_config(tmp_path: Path, patched_claude) -> None:
    _, _, local_cfg = patched_claude
    db = tmp_path / "g.duckdb"
    result = runner.invoke(
        app, ["install", "claude", "--db", str(db), "--location", "local", "--yes"]
    )
    assert result.exit_code == 0
    assert local_cfg.exists()


# ---------------------------------------------------------------------------
# install — error cases
# ---------------------------------------------------------------------------


def test_install_unknown_target_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["install", "unknown_agent_xyz", "--yes"])
    assert result.exit_code == 1
    assert "Unknown target" in _plain(result.output)


def test_install_invalid_location_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["install", "claude", "--location", "everywhere", "--yes"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_removes_entry(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["install", "claude", "--db", str(db), "--yes"])
    assert "codegraph" in json.loads(cfg.read_text())["mcpServers"]

    result = runner.invoke(app, ["uninstall", "claude", "--yes"])
    assert result.exit_code == 0
    data = json.loads(cfg.read_text())
    assert "codegraph" not in data.get("mcpServers", {})


def test_uninstall_not_configured_is_noop(tmp_path: Path, patched_claude) -> None:
    result = runner.invoke(app, ["uninstall", "claude", "--yes"])
    assert result.exit_code == 0
    assert "not configured" in _plain(result.output).lower()


def test_uninstall_confirm_n_aborts(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["install", "claude", "--db", str(db), "--yes"])
    result = runner.invoke(app, ["uninstall", "claude"], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in _plain(result.output)
    # Config untouched.
    assert "codegraph" in json.loads(cfg.read_text())["mcpServers"]


def test_uninstall_prints_uninstalled(tmp_path: Path, patched_claude) -> None:
    _, cfg, _ = patched_claude
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["install", "claude", "--db", str(db), "--yes"])
    result = runner.invoke(app, ["uninstall", "claude", "--yes"])
    assert "Uninstalled" in _plain(result.output)


def test_uninstall_unknown_target_exits_1() -> None:
    result = runner.invoke(app, ["uninstall", "unknown_agent_xyz", "--yes"])
    assert result.exit_code == 1
