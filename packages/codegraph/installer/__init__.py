# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
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
KiroTarget         -- Kiro (~/.kiro/settings/mcp.json)
OpencodeTarget     -- opencode (~/.config/opencode/opencode.jsonc)
HermesTarget       -- Hermes Agent (~/.hermes/config.yaml)
AntigravityTarget  -- Antigravity IDE (~/.gemini/config/mcp_config.json)
"""

from __future__ import annotations

# Import the targets subpackage to trigger auto-registration of all built-in targets.
import codegraph.installer.targets  # noqa: E402, F401
from codegraph.installer.base import McpEntry, Target
from codegraph.installer.registry import get_target, list_targets, register_target
from codegraph.installer.targets import (  # noqa: E402
    AntigravityTarget,
    ClaudeCodeTarget,
    CodexTarget,
    CursorTarget,
    GeminiTarget,
    HermesTarget,
    KiroTarget,
    OpencodeTarget,
)

__all__ = [
    "AntigravityTarget",
    "ClaudeCodeTarget",
    "CodexTarget",
    "CursorTarget",
    "GeminiTarget",
    "HermesTarget",
    "KiroTarget",
    "McpEntry",
    "OpencodeTarget",
    "Target",
    "get_target",
    "list_targets",
    "register_target",
]
