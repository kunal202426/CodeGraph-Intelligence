"""Locate a CodeGraph database by walking up the directory tree.

This makes a single MCP server entry work across many projects: the server is
launched with the agent's working directory, and ``discover_db`` climbs from
there to find the nearest ``.codegraph/graph.duckdb`` -- so opening project A
resolves A's index and project B resolves B's, with no per-project config.

Public API
----------
discover_db(start=None) -> Path | None
    Nearest .codegraph/graph.duckdb at or above *start* (default: CWD).
DEFAULT_DB_RELPATH
    The repo-relative location an index is written to.
"""

from __future__ import annotations

from pathlib import Path

# Where `codegraph index` writes a project's graph (relative to the repo root).
DEFAULT_DB_RELPATH = Path(".codegraph/graph.duckdb")


def discover_db(start: Path | None = None) -> Path | None:
    """Return the nearest ``.codegraph/graph.duckdb`` at or above *start*.

    Walks from *start* (default: current working directory) up to the filesystem
    root, returning the first existing database found. Returns ``None`` if no
    index exists anywhere on the path to root.
    """
    base = Path(start) if start is not None else Path.cwd()
    base = base.resolve()
    # Include base itself, then each ancestor up to (and including) the root.
    for directory in (base, *base.parents):
        candidate = directory / DEFAULT_DB_RELPATH
        if candidate.exists():
            return candidate
    return None
