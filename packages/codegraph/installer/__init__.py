"""CodeGraph agent installer.

Public API
----------
Target             -- ABC for MCP install targets
McpEntry           -- MCP server entry dataclass
register_target    -- add a target to the registry
get_target(name)   -- look up a target by name
list_targets()     -- all registered targets sorted by name

Built-in targets (registered automatically on import)
------------------------------------------------------
ClaudeCodeTarget   -- Claude Code (~/.claude.json)
CursorTarget       -- Cursor (~/.cursor/mcp.json)
CodexTarget        -- Codex CLI (~/.codex/config.json)
GeminiTarget       -- Gemini CLI (~/.gemini/settings.json)
"""

from __future__ import annotations

# Import the targets subpackage to trigger auto-registration of all built-in targets.
import codegraph.installer.targets  # noqa: E402, F401
from codegraph.installer.base import McpEntry, Target
from codegraph.installer.registry import get_target, list_targets, register_target
from codegraph.installer.targets import (  # noqa: E402
    ClaudeCodeTarget,
    CodexTarget,
    CursorTarget,
    GeminiTarget,
)

__all__ = [
    "ClaudeCodeTarget",
    "CodexTarget",
    "CursorTarget",
    "GeminiTarget",
    "McpEntry",
    "Target",
    "get_target",
    "list_targets",
    "register_target",
]
