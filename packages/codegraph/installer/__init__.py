"""CodeGraph agent installer.

Public API
----------
Target             -- ABC for MCP install targets
McpEntry           -- MCP server entry dataclass
register_target    -- add a target to the registry
get_target(name)   -- look up a target by name
list_targets()     -- all registered targets sorted by name
"""

from __future__ import annotations

from codegraph.installer.base import McpEntry, Target
from codegraph.installer.registry import get_target, list_targets, register_target

__all__ = [
    "McpEntry",
    "Target",
    "get_target",
    "list_targets",
    "register_target",
]
