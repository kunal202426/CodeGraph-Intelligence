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
        Tool(
            name="trace_path",
            description=(
                "Find the shortest call chain between two symbols via BFS over "
                "directed call edges (max 7 hops by default). Returns the sequence "
                "of entity_ids from source to destination. Useful for understanding "
                "how one function transitively reaches another."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "from_id": {
                        "type": "string",
                        "description": "Source entity_id (start of the call chain).",
                    },
                    "to_id": {
                        "type": "string",
                        "description": "Destination entity_id (end of the call chain).",
                    },
                    "max_hops": {
                        "type": "integer",
                        "default": 7,
                        "description": "Maximum call chain length to search.",
                    },
                },
                "required": ["from_id", "to_id"],
            },
        ),
        Tool(
            name="get_context",
            description=(
                "Primary context tool — one call returns hybrid search results packed "
                "with full source, signatures, docstrings, and each entity's immediate "
                "callers and callees. Replaces 3-4 round-trips (search + entity + "
                "impact) with a single request. Use this first when exploring an "
                "unfamiliar codebase or when you need to understand how a symbol fits "
                "into the call graph."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language question or symbol name.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                        "description": "Max entities to return (1-10, default 5).",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_files",
            description=(
                "List all source files in the indexed codebase with their language, "
                "line count, and entity count. Optionally filter by language name "
                "(e.g. 'python', 'typescript', 'go')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": "Filter to one language. Omit for all languages.",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="index_status",
            description=(
                "Return index statistics: file, entity, and edge counts; embedding "
                "coverage; and whether any source files have changed since the last "
                "index run (staleness indicator)."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
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


def _trace_path(args: dict[str, Any]) -> str:
    """Shortest call chain between two entity_ids (T12.2)."""
    from_id = str(args["from_id"])
    to_id = str(args["to_id"])
    max_hops = max(1, min(int(args.get("max_hops", 7)), 20))

    from codegraph.analysis.traversal import find_shortest_path

    store = _open_store()
    try:
        path = find_shortest_path(store.conn, from_id, to_id, max_hops=max_hops)
    finally:
        store.close()

    if path is None:
        return json.dumps(
            {
                "from_id": from_id,
                "to_id": to_id,
                "found": False,
                "hops": None,
                "path": [],
                "message": f"No call path found within {max_hops} hops.",
            }
        )
    return json.dumps(
        {
            "from_id": from_id,
            "to_id": to_id,
            "found": True,
            "hops": len(path) - 1,
            "path": path,
        }
    )


def _get_context(args: dict[str, Any]) -> str:
    """Hybrid search + full source + callers/callees in one response (T12.1)."""
    query = str(args["query"])
    limit = max(1, min(int(args.get("limit", 5)), 10))

    store = _open_store()
    try:
        vector = _maybe_embed(query) if store.count_embedded() > 0 else None
        hits = hybrid_search(store.conn, query, vector, limit=limit)

        if not hits:
            return json.dumps({"query": query, "total": 0, "entities": []})

        entities = []
        col_select = ", ".join(_ENTITY_COLUMNS)
        for hit in hits:
            eid = hit.entity_id
            row = store.conn.execute(
                f"SELECT {col_select} FROM entities WHERE entity_id = ?",
                [eid],
            ).fetchone()
            if row is None:
                continue
            entity: dict[str, Any] = dict(zip(_ENTITY_COLUMNS, row, strict=True))

            # Outbound: imports + calls (what this entity depends on)
            entity["depends_on"] = [
                r[0]
                for r in store.conn.execute(
                    "SELECT DISTINCT dst_id FROM edges "
                    "WHERE src_id = ? AND type IN ('calls', 'imports')",
                    [eid],
                ).fetchall()
            ]

            # Inbound: direct callers of this entity
            entity["called_by"] = [
                r[0]
                for r in store.conn.execute(
                    "SELECT DISTINCT src_id FROM edges WHERE dst_id = ? AND type = 'calls'",
                    [eid],
                ).fetchall()
            ]

            entity["via"] = list(hit.retrievers)
            entities.append(entity)

        return json.dumps({"query": query, "total": len(entities), "entities": entities})
    finally:
        store.close()


_HANDLERS = {
    "search_code": _search_code,
    "get_entity_context": _get_entity_context,
    "impact_analysis": _impact_analysis,
    "ask_codebase": _ask_codebase,
    "trace_path": _trace_path,
    "get_context": _get_context,
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

    # Staleness check (T11.3): warn to stderr if source files changed since last index.
    # stdout is reserved for MCP framing; all diagnostics must go to stderr.
    try:
        from codegraph.sync.watcher import count_stale_files

        stale = count_stale_files(Path("."), get_db_path())
        if stale > 0:
            import sys

            noun = "file" if stale == 1 else "files"
            print(
                f"CodeGraph: {stale} {noun} changed since last index. "
                "Re-run codegraph index to update.",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001 — staleness check is best-effort
        pass

    import anyio

    anyio.run(_serve)


if __name__ == "__main__":
    main()
