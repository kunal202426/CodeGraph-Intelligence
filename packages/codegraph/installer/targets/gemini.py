# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Install target: Google Gemini CLI.

Global config:  ~/.gemini/settings.json   (mcpServers key)
Project config: .gemini/settings.json     (mcpServers key)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from codegraph.installer.base import Target


class GeminiTarget(Target):
    name = "gemini"
    display_name = "Gemini CLI"

    def global_config_path(self) -> Path:
        return Path.home() / ".gemini" / "settings.json"

    def local_config_path(self) -> Path:
        return Path(".gemini") / "settings.json"

    def is_available(self) -> bool:
        """True if the ``gemini`` command exists or ``~/.gemini/`` is present."""
        return shutil.which("gemini") is not None or (Path.home() / ".gemini").is_dir()
