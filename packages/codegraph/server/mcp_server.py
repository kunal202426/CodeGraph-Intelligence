"""MCP server exposing CodeGraph to MCP-compatible agents (T7.1 skeleton).

Declares four tools over the indexed graph so an agent (e.g. Claude Code) can
call CodeGraph directly:

  - search_code        — hybrid literal + semantic search
  - get_entity_context — full source + immediate neighbours for an entity_id
  - impact_analysis    — reverse-call blast radius for an entity_id
  - ask_codebase       — natural-language question answered via GraphRAG

Run as a stdio server:  python -m codegraph.server.mcp_server --db <graph.duckdb>

MCP stdio framing uses stdout for protocol messages, so this module must never
print to stdout — diagnostics go to stderr only. Tool dispatch (call_tool) is
wired to the library in T7.2.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from mcp.server import Server
from mcp.types import Tool

DEFAULT_DB = Path(".codegraph/graph.duckdb")

# Set from --db (or CODEGRAPH_DB) in main(); read by the tool handlers (T7.2).
_db_path: Path | None = None

server: Server = Server("codegraph")


def get_db_path() -> Path:
    """Resolve the graph DB path: explicit --db > CODEGRAPH_DB env > default."""
    if _db_path is not None:
        return _db_path
    env = os.environ.get("CODEGRAPH_DB")
    return Path(env) if env else DEFAULT_DB


def tool_definitions() -> list[Tool]:
    """The four tools this server advertises (pure — used by list_tools + tests)."""
    return [
        Tool(
            name="search_code",
            description="Hybrid literal + semantic search over the indexed codebase. "
            "Returns matching entities (functions/classes/modules) with file:line.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text or natural language."},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_entity_context",
            description="Get the full source plus immediate graph neighbours "
            "(callers, callees, imports) for a given entity_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity id, e.g. py:auth/login.py:authenticate.",
                    },
                },
                "required": ["entity_id"],
            },
        ),
        Tool(
            name="impact_analysis",
            description="Find what would break if this entity changed — the reverse-call "
            "blast radius (transitive callers).",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "depth": {"type": "integer", "default": 3},
                },
                "required": ["entity_id"],
            },
        ),
        Tool(
            name="ask_codebase",
            description="Ask a natural-language question about the codebase. Returns a "
            "grounded answer with entity_id citations via GraphRAG.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
    ]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return tool_definitions()


async def _serve() -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    global _db_path
    parser = argparse.ArgumentParser(prog="codegraph-mcp", description="CodeGraph MCP server.")
    parser.add_argument("--db", type=Path, default=None, help="Path to the graph DuckDB file.")
    args = parser.parse_args()
    if args.db is not None:
        _db_path = args.db

    import anyio

    anyio.run(_serve)


if __name__ == "__main__":
    main()
