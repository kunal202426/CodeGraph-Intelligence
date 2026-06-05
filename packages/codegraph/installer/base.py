# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Target ABC and JSON config utilities for the CodeGraph installer.

Public API
----------
McpEntry           -- dataclass for one MCP server entry
Target             -- abstract base class every install target must implement
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Key used in all mcpServers dictionaries for the CodeGraph entry.
_SERVER_KEY = "codegraph"


# ---------------------------------------------------------------------------
# McpEntry
# ---------------------------------------------------------------------------


@dataclass
class McpEntry:
    """Serialisable representation of one MCP server config entry."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (omits empty *env*)."""
        d: dict[str, Any] = {"command": self.command, "args": self.args}
        if self.env:
            d["env"] = self.env
        return d


def _make_entry(db: Path | None) -> McpEntry:
    """Build the CodeGraph MCP server entry.

    When *db* is given, the entry pins that database with ``--db``. When *db* is
    ``None``, no ``--db`` is written so the server discovers the nearest
    ``.codegraph/graph.duckdb`` from its working directory -- one entry then
    serves every project.
    """
    args = ["-m", "codegraph.server.mcp_server"]
    if db is not None:
        args += ["--db", str(db.resolve())]
    return McpEntry(command=sys.executable, args=args)


# ---------------------------------------------------------------------------
# Target ABC
# ---------------------------------------------------------------------------


class Target(ABC):
    """Abstract base for a single MCP agent install target.

    Subclasses declare ``name`` and ``display_name`` as class-level strings
    and implement ``global_config_path()`` + ``is_available()``.  The concrete
    install/uninstall logic is provided here via read-modify-write JSON helpers
    so each target only needs to know *where* its config lives.
    """

    name: str
    display_name: str

    # ------------------------------------------------------------------
    # Abstract interface

    @abstractmethod
    def global_config_path(self) -> Path:
        """Absolute path to the user-level config file for this agent."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this agent appears to be installed on this machine."""

    # ------------------------------------------------------------------
    # Optional overrides

    def local_config_path(self) -> Path:
        """Project-level config file.  Defaults to ``.mcp.json`` in CWD."""
        return Path(".mcp.json")

    def build_entry(self, db: Path | None) -> McpEntry:
        """Build the McpEntry for *db* (``None`` = rely on discovery)."""
        return _make_entry(db)

    # ------------------------------------------------------------------
    # Concrete helpers

    def config_snippet(self, db: Path | None) -> str:
        """Return the JSON that ``install`` would write (for --print-config)."""
        return json.dumps(
            {"mcpServers": {_SERVER_KEY: self.build_entry(db).to_dict()}},
            indent=2,
        )

    def install(self, db: Path | None, *, global_: bool = True) -> None:
        """Idempotently add the CodeGraph MCP entry to the agent config."""
        path = self.global_config_path() if global_ else self.local_config_path()
        self._write_entry(path, db)

    def uninstall(self, *, global_: bool = True) -> None:
        """Remove only the CodeGraph MCP entry from the agent config."""
        path = self.global_config_path() if global_ else self.local_config_path()
        self._remove_entry(path)

    def is_configured(self, *, global_: bool = True) -> bool:
        """Return True if the CodeGraph entry is already present."""
        path = self.global_config_path() if global_ else self.local_config_path()
        try:
            data = _read_json(path)
            return _SERVER_KEY in data.get("mcpServers", {})
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    # ------------------------------------------------------------------
    # Private read-modify-write helpers

    def _write_entry(self, path: Path, db: Path | None) -> None:
        data = _read_json_or_empty(path)
        servers: dict[str, Any] = data.setdefault("mcpServers", {})
        servers[_SERVER_KEY] = self.build_entry(db).to_dict()
        _write_json(path, data)

    def _remove_entry(self, path: Path) -> None:
        try:
            data = _read_json(path)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        servers: dict[str, Any] = data.get("mcpServers", {})
        servers.pop(_SERVER_KEY, None)
        if not servers:
            data.pop("mcpServers", None)
        _write_json(path, data)


# ---------------------------------------------------------------------------
# JSON file utilities (package-internal; used by Target subclasses)
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    """Parse *path* as JSON.  Raises FileNotFoundError or json.JSONDecodeError."""
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    """Parse *path* as JSON; return ``{}`` on missing file or parse error."""
    try:
        return _read_json(path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Create parent directories and write *data* as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
