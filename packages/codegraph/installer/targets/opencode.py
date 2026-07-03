# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Install target: opencode.

Global config: ~/.config/opencode/opencode.jsonc -- XDG-style on every
platform, Windows included (opencode resolves its config dir from
XDG_CONFIG_HOME or ~/.config unconditionally; it never reads %APPDATA%).
Falls back to opencode.json when that file already exists instead.
Project config: ./opencode.jsonc (same fallback rule).

Config shape differs from every other target here -- opencode wraps MCP
servers under ``mcp.<name>`` (not ``mcpServers.<name>``), and the command
is a single string array combining binary + args rather than a separate
command/args pair:

    {"mcp": {"codegraph": {"type": "local", "command": [...], "enabled": true}}}

So this overrides the base class's read-modify-write instead of using it
as-is. The array form is built from the same ``build_entry()`` every other
target uses (still resolves ``--db`` the same way), just reshaped.

Simplification: reads/writes go through the plain ``json`` module rather
than a JSONC-aware parser, so any ``//`` comments in an existing
opencode.jsonc are dropped on rewrite. Acceptable for a first install --
opencode's own auto-generated file has none -- but a real JSONC round-trip
would need a dedicated parser this project doesn't otherwise depend on.

Docs: https://opencode.ai/docs/config
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from codegraph.installer.base import _SERVER_KEY, McpEntry, Target


def _global_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "opencode"


def _pick_config_path(directory: Path) -> Path:
    """Prefer an existing .jsonc, then .json, else default to .jsonc (what
    opencode itself creates on first run)."""
    jsonc = directory / "opencode.jsonc"
    json_path = directory / "opencode.json"
    if jsonc.exists():
        return jsonc
    if json_path.exists():
        return json_path
    return jsonc


def _opencode_entry(entry: McpEntry) -> dict[str, Any]:
    return {"type": "local", "command": [entry.command, *entry.args], "enabled": True}


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class OpencodeTarget(Target):
    name = "opencode"
    display_name = "opencode"

    def global_config_path(self) -> Path:
        return _pick_config_path(_global_config_dir())

    def local_config_path(self) -> Path:
        return _pick_config_path(Path("."))

    def is_available(self) -> bool:
        """True if the ``opencode`` command exists or its config dir is present."""
        return shutil.which("opencode") is not None or _global_config_dir().is_dir()

    def config_snippet(self, db: Path | None) -> str:
        return json.dumps({"mcp": {_SERVER_KEY: _opencode_entry(self.build_entry(db))}}, indent=2)

    def is_configured(self, *, global_: bool = True) -> bool:
        path = self.global_config_path() if global_ else self.local_config_path()
        data = _read_json_or_empty(path)
        return _SERVER_KEY in data.get("mcp", {})

    def _write_entry(self, path: Path, db: Path | None) -> None:
        data = _read_json_or_empty(path)
        mcp: dict[str, Any] = data.setdefault("mcp", {})
        mcp[_SERVER_KEY] = _opencode_entry(self.build_entry(db))
        _write_json(path, data)

    def _remove_entry(self, path: Path) -> None:
        data = _read_json_or_empty(path)
        if not data:
            return
        mcp: dict[str, Any] = data.get("mcp", {})
        mcp.pop(_SERVER_KEY, None)
        if not mcp:
            data.pop("mcp", None)
        _write_json(path, data)
