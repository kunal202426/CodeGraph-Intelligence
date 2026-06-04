"""Built-in install targets.

Importing this subpackage registers all four targets in the global registry.
"""

from __future__ import annotations

from codegraph.installer.registry import register_target
from codegraph.installer.targets.claude_code import ClaudeCodeTarget
from codegraph.installer.targets.codex import CodexTarget
from codegraph.installer.targets.cursor import CursorTarget
from codegraph.installer.targets.gemini import GeminiTarget

register_target(ClaudeCodeTarget())
register_target(CodexTarget())
register_target(CursorTarget())
register_target(GeminiTarget())

__all__ = [
    "ClaudeCodeTarget",
    "CodexTarget",
    "CursorTarget",
    "GeminiTarget",
]
