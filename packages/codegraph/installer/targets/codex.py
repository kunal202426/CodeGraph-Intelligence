"""Install target: OpenAI Codex CLI.

Global config:  ~/.codex/config.json   (mcpServers key)
Project config: .codex/config.json     (mcpServers key)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from codegraph.installer.base import Target


class CodexTarget(Target):
    name = "codex"
    display_name = "Codex CLI"

    def global_config_path(self) -> Path:
        return Path.home() / ".codex" / "config.json"

    def local_config_path(self) -> Path:
        return Path(".codex") / "config.json"

    def is_available(self) -> bool:
        """True if the ``codex`` command exists or ``~/.codex/`` is present."""
        return shutil.which("codex") is not None or (Path.home() / ".codex").is_dir()
