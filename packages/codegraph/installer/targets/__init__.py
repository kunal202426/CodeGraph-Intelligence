# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Built-in install targets.

Importing this subpackage registers all eight targets in the global registry.
"""

from __future__ import annotations

from codegraph.installer.registry import register_target
from codegraph.installer.targets.antigravity import AntigravityTarget
from codegraph.installer.targets.claude_code import ClaudeCodeTarget
from codegraph.installer.targets.codex import CodexTarget
from codegraph.installer.targets.cursor import CursorTarget
from codegraph.installer.targets.gemini import GeminiTarget
from codegraph.installer.targets.hermes import HermesTarget
from codegraph.installer.targets.kiro import KiroTarget
from codegraph.installer.targets.opencode import OpencodeTarget

register_target(ClaudeCodeTarget())
register_target(CodexTarget())
register_target(CursorTarget())
register_target(GeminiTarget())
register_target(KiroTarget())
register_target(OpencodeTarget())
register_target(HermesTarget())
register_target(AntigravityTarget())

__all__ = [
    "AntigravityTarget",
    "ClaudeCodeTarget",
    "CodexTarget",
    "CursorTarget",
    "GeminiTarget",
    "HermesTarget",
    "KiroTarget",
    "OpencodeTarget",
]
