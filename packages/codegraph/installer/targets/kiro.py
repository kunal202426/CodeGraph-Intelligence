# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Install target: Kiro CLI / IDE.

Global config:  ~/.kiro/settings/mcp.json   (mcpServers key)
Project config: .kiro/settings/mcp.json     (mcpServers key)

Same mcpServers.codegraph shape as Claude/Cursor/Gemini, so no overrides of
the base read-modify-write logic are needed. Paths are identical across
platforms since Kiro resolves its config root from the home directory on
macOS, Linux, and Windows alike.

Docs: https://kiro.dev/docs/cli/mcp/
"""

from __future__ import annotations

import shutil
from pathlib import Path

from codegraph.installer.base import Target


class KiroTarget(Target):
    name = "kiro"
    display_name = "Kiro"

    def global_config_path(self) -> Path:
        return Path.home() / ".kiro" / "settings" / "mcp.json"

    def local_config_path(self) -> Path:
        return Path(".kiro") / "settings" / "mcp.json"

    def is_available(self) -> bool:
        """True if the ``kiro`` command exists or ``~/.kiro/`` is present."""
        return shutil.which("kiro") is not None or (Path.home() / ".kiro").is_dir()
