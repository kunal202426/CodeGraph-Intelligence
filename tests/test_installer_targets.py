"""Tests for T13.2 — built-in install targets (Claude Code, Cursor, Codex, Gemini)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import codegraph.installer  # noqa: F401 — triggers auto-registration
import pytest
from codegraph.installer.registry import get_target, list_targets
from codegraph.installer.targets.claude_code import ClaudeCodeTarget
from codegraph.installer.targets.codex import CodexTarget
from codegraph.installer.targets.cursor import CursorTarget
from codegraph.installer.targets.gemini import GeminiTarget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_TARGETS = [ClaudeCodeTarget, CursorTarget, CodexTarget, GeminiTarget]
_ALL_NAMES = {"claude", "cursor", "codex", "gemini"}


# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------


def test_all_four_targets_registered() -> None:
    names = {t.name for t in list_targets()}
    assert _ALL_NAMES.issubset(names)


def test_get_target_by_name() -> None:
    for name in _ALL_NAMES:
        t = get_target(name)
        assert t.name == name


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_target_has_display_name(cls) -> None:
    assert cls.display_name


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_target_name_is_short_identifier(cls) -> None:
    # name should be lowercase, no spaces
    assert cls.name == cls.name.lower()
    assert " " not in cls.name


# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------


def test_claude_global_config_path() -> None:
    t = ClaudeCodeTarget()
    p = t.global_config_path()
    assert p.name == ".claude.json"
    assert p.is_absolute()


def test_claude_local_config_path_default() -> None:
    # Inherits base default: .mcp.json in CWD
    assert ClaudeCodeTarget().local_config_path() == Path(".mcp.json")


def test_cursor_global_config_path() -> None:
    t = CursorTarget()
    p = t.global_config_path()
    assert p.name == "mcp.json"
    assert ".cursor" in str(p)
    assert p.is_absolute()


def test_cursor_local_config_path() -> None:
    assert CursorTarget().local_config_path() == Path(".cursor") / "mcp.json"


def test_codex_global_config_path() -> None:
    t = CodexTarget()
    p = t.global_config_path()
    assert p.name == "config.json"
    assert ".codex" in str(p)
    assert p.is_absolute()


def test_codex_local_config_path() -> None:
    assert CodexTarget().local_config_path() == Path(".codex") / "config.json"


def test_gemini_global_config_path() -> None:
    t = GeminiTarget()
    p = t.global_config_path()
    assert p.name == "settings.json"
    assert ".gemini" in str(p)
    assert p.is_absolute()


def test_gemini_local_config_path() -> None:
    assert GeminiTarget().local_config_path() == Path(".gemini") / "settings.json"


# ---------------------------------------------------------------------------
# is_available — both branches (which found / not found, dir exists / not)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_is_available_returns_bool(cls) -> None:
    assert isinstance(cls().is_available(), bool)


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_is_available_true_when_which_found(cls) -> None:
    with patch("shutil.which", return_value="/usr/bin/fake"):
        assert cls().is_available() is True


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_is_available_false_when_neither_found(cls, tmp_path: Path) -> None:
    with (
        patch("shutil.which", return_value=None),
        patch.object(Path, "is_dir", return_value=False),
    ):
        assert cls().is_available() is False


# ---------------------------------------------------------------------------
# Install / uninstall via tmp_path override
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_install_writes_mcp_entry(cls, tmp_path: Path) -> None:
    t = cls()
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    t._write_entry(cfg, db)
    data = json.loads(cfg.read_text())
    assert "mcpServers" in data
    entry = data["mcpServers"]["codegraph"]
    assert "--db" in entry["args"]
    assert "codegraph.server.mcp_server" in entry["args"]


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_uninstall_removes_entry(cls, tmp_path: Path) -> None:
    t = cls()
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    t._write_entry(cfg, db)
    t._remove_entry(cfg)
    data = json.loads(cfg.read_text())
    assert "codegraph" not in data.get("mcpServers", {})


@pytest.mark.parametrize("cls", _ALL_TARGETS)
def test_config_snippet_valid_json(cls, tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    snippet = cls().config_snippet(db)
    parsed = json.loads(snippet)
    assert "mcpServers" in parsed
    assert "codegraph" in parsed["mcpServers"]
