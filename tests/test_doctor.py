"""Tests for the `codegraph doctor` setup health check."""

from __future__ import annotations

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
    monkeypatch.setattr(t, "global_config_path", lambda: tmp_path / ".claude.json")
    monkeypatch.setattr(t, "local_config_path", lambda: tmp_path / ".mcp.json")
    monkeypatch.chdir(tmp_path)
    orig = _registry._REGISTRY.copy()
    _registry._REGISTRY["claude"] = t
    yield t
    _registry._REGISTRY.clear()
    _registry._REGISTRY.update(orig)


def test_doctor_all_green_after_init(tmp_path: Path, patched_claude) -> None:
    """A repo set up via init passes every doctor check."""
    (tmp_path / "mod.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    assert runner.invoke(app, ["init", "--no-embed"]).exit_code == 0

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    out = _plain(result.output)
    # All four checks should pass; no FAIL lines.
    assert "FAIL" not in out
    assert out.count("PASS") >= 4
    assert "entities" in out


def test_doctor_flags_missing_setup(tmp_path: Path, patched_claude) -> None:
    """In a bare directory with no index/config/guide, doctor reports failures
    with fix commands and still exits 0 (diagnostic, not a gate)."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    out = _plain(result.output)
    assert "FAIL" in out
    assert "codegraph index ." in out  # fix hint for the missing index
    assert "codegraph install claude" in out  # fix hint for missing MCP/guide
