# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Install target: Google Antigravity IDE.

Global config only -- Antigravity has no project-scoped config concept.

Antigravity migrated its MCP config location at some point; this picks
whichever one is actually live, via the same signal Antigravity itself
uses -- a ``.migrated`` marker file it writes on migration:

  unified (current):     ~/.gemini/config/mcp_config.json
  legacy (pre-migration): ~/.gemini/antigravity/mcp_config.json

``global_config_path()`` re-checks the marker on every call (not cached),
so install/uninstall/is_configured always target whichever file is live at
that moment -- no separate migration-sweep step needed.

Standard mcpServers.codegraph shape; no field-stripping quirks to work
around here since this project's McpEntry never emits a ``type`` field in
the first place.
"""

from __future__ import annotations

from pathlib import Path

from codegraph.installer.base import Target


def _unified_config_dir() -> Path:
    return Path.home() / ".gemini" / "config"


def _legacy_config_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity"


class AntigravityTarget(Target):
    name = "antigravity"
    display_name = "Antigravity IDE"

    def global_config_path(self) -> Path:
        unified_dir = _unified_config_dir()
        if (unified_dir / ".migrated").exists():
            return unified_dir / "mcp_config.json"
        unified_file = unified_dir / "mcp_config.json"
        if unified_file.exists():
            return unified_file
        return _legacy_config_dir() / "mcp_config.json"

    def is_available(self) -> bool:
        """True if either the unified or legacy Antigravity config dir exists."""
        return _unified_config_dir().is_dir() or _legacy_config_dir().is_dir()
