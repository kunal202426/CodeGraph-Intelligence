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
import json
import os
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from codegraph.graph.queries import find_callers, hybrid_search
from codegraph.graph.store import GraphStore

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


# ----------------------------------------------------------------------
# Tool implementations (sync; run off the event loop via anyio.to_thread).
# Each opens a read-only store, returns a JSON string, and never raises —
# errors become {"error": ...} so the agent gets a usable message.

_ENTITY_COLUMNS = (
    "entity_id",
    "type",
    "name",
    "qualified_name",
    "language",
    "file",
    "start_line",
    "end_line",
    "signature",
    "docstring",
    "raw_source",
)


def _open_store() -> GraphStore:
    db = get_db_path()
    if not db.exists():
        raise FileNotFoundError(f"No graph database at {db}. Run `codegraph index <repo>` first.")
    return GraphStore(db, read_only=True)


def _maybe_embed(query: str) -> list[float] | None:
    try:
        from codegraph.embeddings.pipeline import embed_one

        return embed_one(query).tolist()
    except Exception:  # noqa: BLE001 - model unavailable → literal search only
        return None


def _search_code(args: dict[str, Any]) -> str:
    query = str(args["query"])
    limit = int(args.get("limit", 10))
    store = _open_store()
    try:
        # Only pay the embedding cost when the index actually has vectors.
        vector = _maybe_embed(query) if store.count_embedded() > 0 else None
        hits = hybrid_search(store.conn, query, vector, limit=limit)
    finally:
        store.close()
    return json.dumps(
        [
            {
                "entity_id": h.entity_id,
                "type": h.type,
                "name": h.name,
                "file": h.file,
                "start_line": h.start_line,
                "docstring": h.docstring,
                "via": list(h.retrievers),
            }
            for h in hits
        ]
    )


def _get_entity_context(args: dict[str, Any]) -> str:
    entity_id = str(args["entity_id"])
    store = _open_store()
    try:
        row = store.conn.execute(
            f"SELECT {', '.join(_ENTITY_COLUMNS)} FROM entities WHERE entity_id = ?",
            [entity_id],
        ).fetchone()
        if row is None:
            return json.dumps({"error": f"No entity {entity_id!r}."})
        entity = dict(zip(_ENTITY_COLUMNS, row, strict=True))
        calls_out = store.conn.execute(
            "SELECT DISTINCT dst_id FROM edges WHERE src_id = ? AND type IN ('calls', 'imports')",
            [entity_id],
        ).fetchall()
        called_by = store.conn.execute(
            "SELECT DISTINCT src_id FROM edges WHERE dst_id = ? AND type = 'calls'",
            [entity_id],
        ).fetchall()
    finally:
        store.close()
    return json.dumps(
        {
            "entity": entity,
            "depends_on": [r[0] for r in calls_out],
            "called_by": [r[0] for r in called_by],
        }
    )


def _impact_analysis(args: dict[str, Any]) -> str:
    entity_id = str(args["entity_id"])
    depth = int(args.get("depth", 3))
    store = _open_store()
    try:
        tree = find_callers(store.conn, entity_id, depth=depth)
    finally:
        store.close()
    return json.dumps(
        {
            "root": tree.root,
            "total": tree.total,
            "truncated": tree.truncated,
            "callers": {
                callee: [
                    {"entity_id": c.entity_id, "name": c.name, "type": c.type, "file": c.file}
                    for c in callers
                ]
                for callee, callers in tree.callers.items()
            },
        }
    )


def _ask_codebase(args: dict[str, Any]) -> str:
    query = str(args["query"])
    store = _open_store()
    try:
        if store.count_embedded() == 0:
            return json.dumps(
                {"error": "This index has no embeddings; re-index without --no-embed."}
            )
        from codegraph.ai.graphrag import GraphRAG
        from codegraph.ai.llm import LLM, LLMError

        rag = GraphRAG(store, LLM())
        try:
            answer = "".join(rag.ask_stream(query))
        except LLMError as exc:
            return json.dumps({"error": str(exc)})
    finally:
        store.close()
    return json.dumps({"answer": answer})


_HANDLERS = {
    "search_code": _search_code,
    "get_entity_context": _get_entity_context,
    "impact_analysis": _impact_analysis,
    "ask_codebase": _ask_codebase,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    import anyio

    try:
        text = await anyio.to_thread.run_sync(handler, arguments or {})
    except Exception as exc:  # noqa: BLE001 - report to the agent instead of crashing the server
        text = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    return [TextContent(type="text", text=text)]


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
