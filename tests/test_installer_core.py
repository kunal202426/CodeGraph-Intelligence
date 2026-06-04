"""Tests for T13.1 — installer core: Target ABC, McpEntry, registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from codegraph.installer.base import (
    McpEntry,
    Target,
    _make_entry,
    _read_json,
    _read_json_or_empty,
    _write_json,
)
from codegraph.installer.registry import get_target, list_targets, register_target

# ---------------------------------------------------------------------------
# McpEntry
# ---------------------------------------------------------------------------


def test_mcpentry_to_dict_minimal() -> None:
    e = McpEntry(command="python", args=["-m", "foo"])
    d = e.to_dict()
    assert d["command"] == "python"
    assert d["args"] == ["-m", "foo"]
    assert "env" not in d  # empty env must not be emitted


def test_mcpentry_to_dict_with_env() -> None:
    e = McpEntry(command="python", args=[], env={"FOO": "bar"})
    assert e.to_dict()["env"] == {"FOO": "bar"}


def test_mcpentry_empty_env_not_in_dict() -> None:
    e = McpEntry(command="x", args=[])
    assert "env" not in e.to_dict()


def test_make_entry_has_db_arg(tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    entry = _make_entry(db)
    assert entry.command  # sys.executable, non-empty
    assert "--db" in entry.args
    idx = entry.args.index("--db")
    # Path must be absolute (resolve() was called).
    assert entry.args[idx + 1] == str(db.resolve())


def test_make_entry_has_module_arg(tmp_path: Path) -> None:
    entry = _make_entry(tmp_path / "g.duckdb")
    assert "-m" in entry.args
    assert "codegraph.server.mcp_server" in entry.args


# ---------------------------------------------------------------------------
# JSON file utilities
# ---------------------------------------------------------------------------


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "conf.json"
    _write_json(p, {"key": "value", "n": 42})
    assert _read_json(p) == {"key": "value", "n": 42}


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "a" / "b" / "c.json"
    _write_json(p, {})
    assert p.exists()


def test_read_json_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _read_json(tmp_path / "nope.json")


def test_read_json_or_empty_missing(tmp_path: Path) -> None:
    assert _read_json_or_empty(tmp_path / "nope.json") == {}


def test_read_json_or_empty_bad_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert _read_json_or_empty(p) == {}


# ---------------------------------------------------------------------------
# Target — concrete stub for testing
# ---------------------------------------------------------------------------


class _DummyTarget(Target):
    name = "dummy"
    display_name = "Dummy Agent"

    def __init__(self, config: Path) -> None:
        self._config = config

    def global_config_path(self) -> Path:
        return self._config

    def is_available(self) -> bool:
        return True


def test_target_install_creates_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    _DummyTarget(cfg).install(db)
    data = json.loads(cfg.read_text())
    assert "mcpServers" in data
    assert "codegraph" in data["mcpServers"]
    assert "--db" in data["mcpServers"]["codegraph"]["args"]


def test_target_install_is_idempotent(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    t = _DummyTarget(cfg)
    t.install(db)
    t.install(db)
    data = json.loads(cfg.read_text())
    # Only one codegraph entry, not duplicated.
    assert len([k for k in data["mcpServers"] if k == "codegraph"]) == 1


def test_target_install_preserves_other_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"other": "stuff", "mcpServers": {"other_server": {"command": "x"}}}),
        encoding="utf-8",
    )
    _DummyTarget(cfg).install(tmp_path / "g.duckdb")
    data = json.loads(cfg.read_text())
    assert data["other"] == "stuff"
    assert "other_server" in data["mcpServers"]
    assert "codegraph" in data["mcpServers"]


def test_target_uninstall_removes_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    t = _DummyTarget(cfg)
    t.install(db)
    assert t.is_configured()
    t.uninstall()
    assert not t.is_configured()


def test_target_uninstall_noop_if_absent(tmp_path: Path) -> None:
    t = _DummyTarget(tmp_path / "nope.json")
    t.uninstall()  # must not raise


def test_target_uninstall_preserves_other_servers(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"mcpServers": {"other": {"command": "y"}, "codegraph": {"command": "p"}}}),
        encoding="utf-8",
    )
    _DummyTarget(cfg).uninstall()
    data = json.loads(cfg.read_text())
    assert "other" in data["mcpServers"]
    assert "codegraph" not in data["mcpServers"]


def test_target_uninstall_removes_empty_mcp_servers_key(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    db = tmp_path / "g.duckdb"
    t = _DummyTarget(cfg)
    t.install(db)  # only codegraph entry
    t.uninstall()
    data = json.loads(cfg.read_text())
    # Empty mcpServers dict should be cleaned up.
    assert "mcpServers" not in data


def test_target_config_snippet_is_valid_json(tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    snippet = _DummyTarget(tmp_path / "cfg.json").config_snippet(db)
    parsed = json.loads(snippet)
    assert "mcpServers" in parsed
    assert "codegraph" in parsed["mcpServers"]


def test_target_is_configured_false_when_file_missing(tmp_path: Path) -> None:
    assert not _DummyTarget(tmp_path / "nope.json").is_configured()


def test_target_local_config_path_default() -> None:
    t = _DummyTarget(Path("irrelevant"))
    assert t.local_config_path() == Path(".mcp.json")


def test_target_install_local(tmp_path: Path) -> None:
    cfg = tmp_path / ".mcp.json"
    t = _DummyTarget(tmp_path / "global.json")
    # Monkey-patch local path to point into tmp_path.
    t.local_config_path = lambda: cfg  # type: ignore[method-assign]
    db = tmp_path / "g.duckdb"
    t.install(db, global_=False)
    assert t.is_configured(global_=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_register_and_get(tmp_path: Path) -> None:
    t = _DummyTarget(tmp_path / "cfg.json")
    register_target(t)
    assert get_target("dummy") is t


def test_registry_list_targets_is_sorted(tmp_path: Path) -> None:
    names = [t.name for t in list_targets()]
    assert names == sorted(names)


def test_registry_unknown_target_raises() -> None:
    with pytest.raises(KeyError, match="Unknown target"):
        get_target("_nonexistent_target_xyz_9999_")


def test_registry_re_register_replaces(tmp_path: Path) -> None:
    t1 = _DummyTarget(tmp_path / "a.json")
    t2 = _DummyTarget(tmp_path / "b.json")
    register_target(t1)
    register_target(t2)
    assert get_target("dummy") is t2
