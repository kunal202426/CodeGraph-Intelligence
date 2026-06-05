# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Install target: Cursor IDE.

Global config:  ~/.cursor/mcp.json   (mcpServers key)
Project config: .cursor/mcp.json     (mcpServers key)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from codegraph.installer.base import Target


class CursorTarget(Target):
    name = "cursor"
    display_name = "Cursor"

    def global_config_path(self) -> Path:
        return Path.home() / ".cursor" / "mcp.json"

    def local_config_path(self) -> Path:
        return Path(".cursor") / "mcp.json"

    def is_available(self) -> bool:
        """True if the ``cursor`` command exists or ``~/.cursor/`` is present."""
        return shutil.which("cursor") is not None or (Path.home() / ".cursor").is_dir()
