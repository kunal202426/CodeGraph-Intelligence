"""Tests for Phase 24 -- installer targets for opencode, Kiro, Hermes, Antigravity.

Kiro and Antigravity use the same mcpServers.codegraph JSON shape as the
original four targets. opencode (mcp.codegraph, array-form command) and
Hermes (YAML, not JSON) needed their own read-modify-write logic, so they
get more targeted shape/content-preservation tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import codegraph.installer  # noqa: F401 -- triggers auto-registration
import pytest
from codegraph.installer.registry import get_target, list_targets
from codegraph.installer.targets.antigravity import AntigravityTarget
from codegraph.installer.targets.hermes import HermesTarget
from codegraph.installer.targets.kiro import KiroTarget
from codegraph.installer.targets.opencode import OpencodeTarget

_STANDARD_SHAPE_TARGETS = [KiroTarget, AntigravityTarget]
_ALL_NEW_TARGETS = [KiroTarget, OpencodeTarget, HermesTarget, AntigravityTarget]
_ALL_NEW_NAMES = {"kiro", "opencode", "hermes", "antigravity"}


# ---------------------------------------------------------------------------
# Registration + metadata
# ---------------------------------------------------------------------------


def test_all_four_new_targets_registered() -> None:
    names = {t.name for t in list_targets()}
    assert _ALL_NEW_NAMES.issubset(names)


def test_get_target_by_name() -> None:
    for name in _ALL_NEW_NAMES:
        t = get_target(name)
        assert t.name == name


@pytest.mark.parametrize("cls", _ALL_NEW_TARGETS)
def test_target_has_display_name(cls) -> None:
    assert cls.display_name


@pytest.mark.parametrize("cls", _ALL_NEW_TARGETS)
def test_target_name_is_short_identifier(cls) -> None:
    assert cls.name == cls.name.lower()
    assert " " not in cls.name


@pytest.mark.parametrize("cls", _ALL_NEW_TARGETS)
def test_is_available_returns_bool(cls) -> None:
    assert isinstance(cls().is_available(), bool)


# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------


def test_kiro_global_config_path() -> None:
    p = KiroTarget().global_config_path()
    assert p.name == "mcp.json"
    assert str(Path(".kiro") / "settings") in str(p)


def test_kiro_local_config_path() -> None:
    assert KiroTarget().local_config_path() == Path(".kiro") / "settings" / "mcp.json"


def test_opencode_global_config_path_defaults_to_jsonc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    p = OpencodeTarget().global_config_path()
    assert p.name == "opencode.jsonc"
    assert ".config" in str(p) and "opencode" in str(p)


def test_opencode_prefers_existing_json_over_jsonc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    config_dir = tmp_path / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.json").write_text("{}", encoding="utf-8")
    p = OpencodeTarget().global_config_path()
    assert p.name == "opencode.json"


def test_opencode_respects_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg = tmp_path / "custom-xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    p = OpencodeTarget().global_config_path()
    assert str(xdg) in str(p)


def test_hermes_global_config_path_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    p = HermesTarget().global_config_path()
    assert p == tmp_path / ".hermes" / "config.yaml"


def test_hermes_respects_hermes_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom-hermes"
    monkeypatch.setenv("HERMES_HOME", str(custom))
    p = HermesTarget().global_config_path()
    assert p == custom / "config.yaml"


def test_antigravity_defaults_to_legacy_path_without_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = AntigravityTarget().global_config_path()
    assert "antigravity" in str(p)


def test_antigravity_switches_to_unified_path_with_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    unified_dir = tmp_path / ".gemini" / "config"
    unified_dir.mkdir(parents=True)
    (unified_dir / ".migrated").touch()
    p = AntigravityTarget().global_config_path()
    assert p == unified_dir / "mcp_config.json"


def test_antigravity_switches_to_unified_path_if_file_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Antigravity creates the unified file on first post-migration launch
    even before writing the .migrated marker in some versions -- the file's
    mere existence is also a signal."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    unified_dir = tmp_path / ".gemini" / "config"
    unified_dir.mkdir(parents=True)
    (unified_dir / "mcp_config.json").write_text("{}", encoding="utf-8")
    p = AntigravityTarget().global_config_path()
    assert p == unified_dir / "mcp_config.json"


# ---------------------------------------------------------------------------
# is_available -- both branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _ALL_NEW_TARGETS)
def test_is_available_false_when_nothing_found(
    cls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    with patch("shutil.which", return_value=None):
        assert cls().is_available() is False


# ---------------------------------------------------------------------------
# Kiro / Antigravity -- standard mcpServers.codegraph shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _STANDARD_SHAPE_TARGETS)
def test_install_writes_standard_mcp_entry(cls, tmp_path: Path) -> None:
    t = cls()
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    t._write_entry(cfg, db)
    data = json.loads(cfg.read_text())
    entry = data["mcpServers"]["codegraph"]
    assert "--db" in entry["args"]


@pytest.mark.parametrize("cls", _STANDARD_SHAPE_TARGETS)
def test_uninstall_removes_standard_entry(cls, tmp_path: Path) -> None:
    t = cls()
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    t._write_entry(cfg, db)
    t._remove_entry(cfg)
    data = json.loads(cfg.read_text())
    assert "codegraph" not in data.get("mcpServers", {})


@pytest.mark.parametrize("cls", _STANDARD_SHAPE_TARGETS)
def test_config_snippet_valid_json(cls, tmp_path: Path) -> None:
    snippet = cls().config_snippet(tmp_path / "g.duckdb")
    parsed = json.loads(snippet)
    assert "codegraph" in parsed["mcpServers"]


# ---------------------------------------------------------------------------
# opencode -- mcp.codegraph, array-form command
# ---------------------------------------------------------------------------


def test_opencode_write_entry_shape(tmp_path: Path) -> None:
    t = OpencodeTarget()
    cfg = tmp_path / "opencode.jsonc"
    db = tmp_path / "g.duckdb"
    t._write_entry(cfg, db)
    data = json.loads(cfg.read_text())
    entry = data["mcp"]["codegraph"]
    assert entry["type"] == "local"
    assert entry["enabled"] is True
    assert isinstance(entry["command"], list)
    assert "--db" in entry["command"]
    assert "mcpServers" not in data


def test_opencode_is_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    t = OpencodeTarget()
    assert t.is_configured(global_=True) is False
    t.install(None, global_=True)
    assert t.is_configured(global_=True) is True
    t.uninstall(global_=True)
    assert t.is_configured(global_=True) is False


def test_opencode_preserves_sibling_mcp_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "opencode.jsonc"
    cfg.write_text(
        json.dumps({"mcp": {"other": {"type": "local", "command": ["x"], "enabled": True}}}),
        encoding="utf-8",
    )
    t = OpencodeTarget()
    t._write_entry(cfg, None)
    data = json.loads(cfg.read_text())
    assert "other" in data["mcp"]
    assert "codegraph" in data["mcp"]

    t._remove_entry(cfg)
    data = json.loads(cfg.read_text())
    assert "other" in data["mcp"]
    assert "codegraph" not in data["mcp"]


def test_opencode_remove_entry_drops_empty_mcp_wrapper(tmp_path: Path) -> None:
    cfg = tmp_path / "opencode.jsonc"
    t = OpencodeTarget()
    t._write_entry(cfg, None)
    t._remove_entry(cfg)
    data = json.loads(cfg.read_text())
    assert "mcp" not in data


def test_opencode_config_snippet_valid_json(tmp_path: Path) -> None:
    snippet = OpencodeTarget().config_snippet(tmp_path / "g.duckdb")
    parsed = json.loads(snippet)
    assert "codegraph" in parsed["mcp"]


# ---------------------------------------------------------------------------
# Hermes -- YAML, mcp_servers.codegraph
# ---------------------------------------------------------------------------


def test_hermes_write_entry_creates_block(tmp_path: Path) -> None:
    t = HermesTarget()
    cfg = tmp_path / "config.yaml"
    db = tmp_path / "g.duckdb"
    t._write_entry(cfg, db)
    text = cfg.read_text(encoding="utf-8")
    assert "mcp_servers:" in text
    assert "  codegraph:" in text
    assert "--db" in text


def test_hermes_is_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    t = HermesTarget()
    assert t.is_configured(global_=True) is False
    t.install(None, global_=True)
    assert t.is_configured(global_=True) is True
    t.uninstall(global_=True)
    assert t.is_configured(global_=True) is False


def test_hermes_write_entry_is_idempotent(tmp_path: Path) -> None:
    t = HermesTarget()
    cfg = tmp_path / "config.yaml"
    t._write_entry(cfg, None)
    first = cfg.read_text(encoding="utf-8")
    t._write_entry(cfg, None)
    second = cfg.read_text(encoding="utf-8")
    assert first == second
    assert first.count("codegraph:") == 1


def test_hermes_preserves_unrelated_content_and_sibling_server(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "some_other_key:\n  nested: value\n\nmcp_servers:\n  other_server:\n    command: foo\n",
        encoding="utf-8",
    )
    t = HermesTarget()
    t._write_entry(cfg, None)
    text = cfg.read_text(encoding="utf-8")
    assert "some_other_key:" in text
    assert "nested: value" in text
    assert "other_server:" in text
    assert "codegraph:" in text

    t._remove_entry(cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "some_other_key:" in text
    assert "other_server:" in text
    assert "codegraph:" not in text


def test_hermes_remove_entry_drops_empty_mcp_servers_key(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    t = HermesTarget()
    t._write_entry(cfg, None)
    t._remove_entry(cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "mcp_servers:" not in text


def test_hermes_remove_entry_missing_file_is_noop(tmp_path: Path) -> None:
    t = HermesTarget()
    cfg = tmp_path / "does_not_exist.yaml"
    t._remove_entry(cfg)  # must not raise
    assert not cfg.exists()


def test_hermes_config_snippet_mentions_command(tmp_path: Path) -> None:
    snippet = HermesTarget().config_snippet(tmp_path / "g.duckdb")
    assert "mcp_servers:" in snippet
    assert "codegraph:" in snippet
