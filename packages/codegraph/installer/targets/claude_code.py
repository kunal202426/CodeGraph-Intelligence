"""Install target: Claude Code (Anthropic CLI).

Global config:  ~/.claude.json   (mcpServers key)
Project config: .mcp.json        (mcpServers key)

Reference:
  claude mcp add codegraph -- python -m codegraph.server.mcp_server --db ...
"""

from __future__ import annotations

import shutil
from pathlib import Path

from codegraph.installer.base import Target


class ClaudeCodeTarget(Target):
    name = "claude"
    display_name = "Claude Code"

    def global_config_path(self) -> Path:
        return Path.home() / ".claude.json"

    def is_available(self) -> bool:
        """True if the ``claude`` command exists or ``~/.claude/`` is present."""
        return shutil.which("claude") is not None or (Path.home() / ".claude").is_dir()
