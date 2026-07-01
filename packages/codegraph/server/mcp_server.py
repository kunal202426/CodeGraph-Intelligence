# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""MCP server exposing CodeGraph to MCP-compatible agents (T7.1 skeleton).

Declares a suite of tools over the indexed graph so an agent (e.g. Claude Code)
can call CodeGraph directly. A representative few:

  - search_code        — hybrid literal + semantic search
  - get_entity_context — full source + immediate neighbours for an entity_id
  - impact_analysis    — reverse-call blast radius for an entity_id
  - ask_codebase       — natural-language question answered via GraphRAG
  - get_unsummarized_entities / store_summaries — agent writes per-entity
        summaries back into the index (no API key), enriching semantic search

Run as a stdio server:  python -m codegraph.server.mcp_server --db <graph.duckdb>

MCP stdio framing uses stdout for protocol messages, so this module must never
print to stdout — diagnostics go to stderr only. Tool dispatch (call_tool) is
wired to the library in T7.2.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from codegraph.graph.queries import find_callers, hybrid_search, read_baseline_tokens
from codegraph.graph.store import GraphStore

DEFAULT_DB = Path(".codegraph/graph.duckdb")

# Set from --db (or CODEGRAPH_DB) in main(); read by the tool handlers (T7.2).
_db_path: Path | None = None

# How long (seconds) to reuse a stale-file count before re-walking the repo.
_STALE_TTL_SEC = 300


class _StalenessCache:
    """Thread-safe TTL cache for the per-process stale-file count.

    Walking the repo to count stale files on every search query would add
    10-50 ms per call. This cache reuses the result for 5 minutes and is
    reset to 0 immediately after a successful reindex so the next get_context
    call doesn't spuriously warn about staleness that was just fixed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count: int = 0
        self._expires: float = 0.0  # monotonic epoch; 0 means "expired"

    def get(self) -> int | None:
        """Return the cached count if still fresh, else None."""
        with self._lock:
            if time.monotonic() < self._expires:
                return self._count
            return None

    def set(self, count: int) -> None:
        with self._lock:
            self._count = count
            self._expires = time.monotonic() + _STALE_TTL_SEC

    def reset(self) -> None:
        """Expire the cache immediately so the next call re-walks the repo."""
        with self._lock:
            self._expires = 0.0


_stale_cache = _StalenessCache()


def _get_stale_count() -> int:
    """Return the number of source files changed since the last index.

    Uses _stale_cache to avoid re-walking the repo on every tool call.
    Returns 0 on any error so a broken staleness check never blocks search.
    """
    cached = _stale_cache.get()
    if cached is not None:
        return cached
    try:
        from codegraph.sync.watcher import count_stale_files

        count = count_stale_files(_repo_root_for_db(), get_db_path())
    except Exception:  # noqa: BLE001 — staleness check is best-effort
        count = 0
    _stale_cache.set(count)
    return count


server: Server = Server("codegraph")


def get_db_path() -> Path:
    """Resolve the graph DB path.

    Precedence: explicit --db > CODEGRAPH_DB env > walk-up discovery from CWD >
    default. Discovery lets one MCP server entry serve many projects: the nearest
    ``.codegraph/graph.duckdb`` at or above the working directory wins.
    """
    if _db_path is not None:
        return _db_path
    env = os.environ.get("CODEGRAPH_DB")
    if env:
        return Path(env)
    from codegraph.graph.locate import discover_db

    discovered = discover_db()
    return discovered if discovered is not None else DEFAULT_DB


def tool_definitions() -> list[Tool]:
    """The four tools this server advertises (pure — used by list_tools + tests)."""
    return [
        Tool(
            name="search_code",
            description="Prefer this over grep/file-reading for finding code. Hybrid "
            "literal + semantic search over the indexed codebase -- returns matching "
            "entities (functions/classes/modules) with file:line and entity_id, using "
            "far fewer tokens than scanning files. Use when you need a quick list of "
            "candidate locations; follow up with get_context for the full picture.",
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
            description="Use this instead of opening a file when you already know the "
            "entity_id and need its full source plus immediate graph neighbours "
            "(callers, callees, imports). Returns exactly one entity's body and its "
            "links -- cheaper and more precise than reading the whole file.",
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
            description="Use this before editing an entity to see what would break -- the "
            "reverse-call blast radius (transitive callers). Prefer this over manually "
            "grepping for usages; it follows the resolved call graph across files.",
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
            description="Use this for broad 'how does X work?' questions when you want a "
            "synthesized answer rather than raw entities. Returns a grounded natural-"
            "language answer with entity_id citations via GraphRAG. Requires an index "
            "built with embeddings.",
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
                "Use this to answer 'how does A reach B?' -- the shortest call chain "
                "between two entity_ids via BFS over directed call edges (max 7 hops by "
                "default). Returns the labeled sequence from source to destination. "
                "Prefer this over manually following calls through files."
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
                "START HERE before reading any source file. The primary tool -- one call "
                "returns hybrid search results packed with signatures, docstrings, a "
                "short source preview, and each entity's callers and callees. Replaces "
                "3-4 round-trips (search + entity + impact) and uses ~10x fewer tokens "
                "than opening files. Defaults to lean summaries; pass detail='full' only "
                "when you need complete bodies (1-2 entities at a time)."
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
                    "detail": {
                        "type": "string",
                        "enum": ["summary", "full"],
                        "default": "summary",
                        "description": (
                            "'summary' (default, token-lean): signature + docstring + "
                            "short source preview. 'full': complete source bodies -- use "
                            "sparingly, only for 1-2 entities."
                        ),
                    },
                    "max_tokens": {
                        "type": "integer",
                        "default": 1500,
                        "description": (
                            "Approx output token budget; entities beyond it are dropped "
                            "and 'truncated' is set in the response."
                        ),
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_files",
            description=(
                "Use this instead of listing the directory tree to understand project "
                "layout from the index: every source file with its language, line count, "
                "and entity count. Optionally filter by language name (e.g. 'python', "
                "'typescript', 'go')."
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
                "Call this once at the start of a session to confirm the index exists "
                "and is fresh. Returns file, entity, and edge counts; embedding "
                "coverage; and whether source files changed since the last index "
                "(staleness). If stale, run the reindex tool before relying on results."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="reindex",
            description=(
                "Use this when index_status reports the index is stale, to refresh it "
                "without leaving the chat. Re-parses only the source files changed since "
                "the last index (incremental, fast) and updates the graph. Returns how "
                "many files and entities were refreshed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "no_embed": {
                        "type": "boolean",
                        "default": False,
                        "description": "Skip recomputing embeddings for changed files (faster).",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="get_unsummarized_entities",
            description=(
                "Fetch a batch of code entities that still lack a natural-language "
                "summary, so you can write one for each. Returns entity_id, type, "
                "qualified_name, location, signature, and a short source preview -- "
                "enough to summarize without opening files. Pair with store_summaries: "
                "call this, write a one-line summary per entity, store them, and repeat "
                "until 'remaining' reaches 0. This enriches the index using your own "
                "reasoning (no API key needed) and improves later semantic search."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Max entities to return this batch (1-200, default 20).",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="store_summaries",
            description=(
                "Call this after get_unsummarized_entities to write the summaries you "
                "wrote back into the index; it persists them and re-embeds just those "
                "entities so semantic search improves immediately. Input is a list of "
                "{entity_id, summary}. Use one short, information-dense sentence per "
                "entity describing what it does and why."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of {entity_id, summary} objects to persist.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "entity_id": {"type": "string"},
                                "summary": {"type": "string"},
                            },
                            "required": ["entity_id", "summary"],
                        },
                    },
                },
                "required": ["items"],
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

# Columns returned in get_context "summary" mode (omits raw_source to stay token-lean).
_SUMMARY_COLUMNS = tuple(c for c in _ENTITY_COLUMNS if c != "raw_source")

# Lines of source shown as a preview in summary mode.
_SOURCE_PREVIEW_LINES = 8

# Max neighbour ids listed per entity in get_context summary mode (counts are
# always exact; the full lists are available via impact_analysis / full detail).
_NEIGHBOR_CAP = 8


def _source_preview(raw_source: str | None, max_lines: int = _SOURCE_PREVIEW_LINES) -> str:
    """Return the first *max_lines* lines of source with a truncation marker."""
    if not raw_source:
        return ""
    lines = raw_source.splitlines()
    if len(lines) <= max_lines:
        return raw_source
    head = "\n".join(lines[:max_lines])
    return f"{head}\n... ({len(lines) - max_lines} more lines; pass detail='full' for all)"


def _labels_for(conn, entity_ids: list[str]) -> dict[str, str]:
    """Map each entity_id to a human-readable 'name (file:line)' label.

    Ids with no matching entity row (e.g. external targets) fall back to the id
    itself so the agent always gets a usable string.
    """
    labels: dict[str, str] = {}
    for eid in entity_ids:
        row = conn.execute(
            "SELECT name, file, start_line FROM entities WHERE entity_id = ?",
            [eid],
        ).fetchone()
        labels[eid] = f"{row[0]} ({row[1]}:{row[2]})" if row else eid
    return labels


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
        # Resolve readable labels while the store is open (path is short, <= max_hops).
        label_map = _labels_for(store.conn, path) if path else {}
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
                "labels": [],
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
            "labels": [label_map.get(eid, eid) for eid in path],
        }
    )


def _get_context(args: dict[str, Any]) -> str:
    """Hybrid search packed with callers/callees in one response (T12.1).

    Defaults to token-lean summaries (signature + docstring + short source
    preview). Pass ``detail="full"`` to include complete ``raw_source`` bodies.
    """
    from codegraph.ai.tokens import estimate_tokens

    query = str(args["query"])
    limit = max(1, min(int(args.get("limit", 5)), 10))
    detail = str(args.get("detail", "summary")).lower()
    max_tokens = max(100, int(args.get("max_tokens", 1500)))
    full = detail == "full"
    columns = _ENTITY_COLUMNS if full else _SUMMARY_COLUMNS

    store = _open_store()
    try:
        has_vectors = store.count_embedded() > 0
        vector = _maybe_embed(query) if has_vectors else None
        hits = hybrid_search(store.conn, query, vector, limit=limit)

        # Surface the one failure that is otherwise silent: a --no-embed index
        # degrades semantic search to literal-only with no signal to the agent.
        warnings: list[str] = []
        stale = _get_stale_count()
        if stale > 0:
            noun = "file" if stale == 1 else "files"
            warnings.append(
                f"Index stale: {stale} source {noun} changed since last index. "
                "Call reindex before relying on these results."
            )
        if not has_vectors:
            warnings.append(
                "No embeddings in this index -- results are literal matches only. "
                "Run the reindex tool (or `codegraph index` without --no-embed) for "
                "semantic search."
            )

        if not hits:
            return json.dumps(
                {
                    "query": query,
                    "total": 0,
                    "detail": detail,
                    "truncated": False,
                    "tokens_estimated": 0,
                    "tokens_if_read": 0,
                    "tokens_saved": 0,
                    "savings_ratio": 0.0,
                    "warnings": warnings,
                    "entities": [],
                }
            )

        entities = []
        used_tokens = 0
        truncated = False
        col_select = ", ".join(columns)
        for hit in hits:
            eid = hit.entity_id
            row = store.conn.execute(
                f"SELECT {col_select} FROM entities WHERE entity_id = ?",
                [eid],
            ).fetchone()
            if row is None:
                continue
            entity: dict[str, Any] = dict(zip(columns, row, strict=True))

            # In summary mode, attach a short preview instead of the full body.
            if not full:
                preview_row = store.conn.execute(
                    "SELECT raw_source FROM entities WHERE entity_id = ?",
                    [eid],
                ).fetchone()
                entity["source_preview"] = _source_preview(preview_row[0] if preview_row else None)

            # Outbound: imports + calls (what this entity depends on)
            deps = [
                r[0]
                for r in store.conn.execute(
                    "SELECT DISTINCT dst_id FROM edges "
                    "WHERE src_id = ? AND type IN ('calls', 'imports')",
                    [eid],
                ).fetchall()
            ]
            # Inbound: direct callers of this entity
            callers = [
                r[0]
                for r in store.conn.execute(
                    "SELECT DISTINCT src_id FROM edges WHERE dst_id = ? AND type = 'calls'",
                    [eid],
                ).fetchall()
            ]

            # Always report the true neighbour counts. In summary mode cap the
            # actual id lists so a hub function (many callers) can't bloat the
            # response -- the agent gets the count + a sample, and can call
            # impact_analysis / get_entity_context for the complete list.
            entity["depends_on_count"] = len(deps)
            entity["called_by_count"] = len(callers)
            if full:
                entity["depends_on"] = deps
                entity["called_by"] = callers
            else:
                entity["depends_on"] = deps[:_NEIGHBOR_CAP]
                entity["called_by"] = callers[:_NEIGHBOR_CAP]

            entity["via"] = list(hit.retrievers)

            # Token budget: always include the first entity, then stop once the
            # running estimate would exceed max_tokens.
            entity_tokens = estimate_tokens(json.dumps(entity))
            if entities and used_tokens + entity_tokens > max_tokens:
                truncated = True
                break
            used_tokens += entity_tokens
            entities.append(entity)

        # Savings: how many tokens reading the surfaced entities' files in full
        # would cost, versus the (lean) context we actually returned.
        tokens_if_read = read_baseline_tokens(
            store.conn, [e["file"] for e in entities if e.get("file")]
        )
        tokens_saved = max(0, tokens_if_read - used_tokens)
        savings_ratio = round(tokens_if_read / used_tokens, 1) if used_tokens else 0.0

        return json.dumps(
            {
                "query": query,
                "total": len(entities),
                "detail": detail,
                "truncated": truncated,
                "tokens_estimated": used_tokens,
                "tokens_if_read": tokens_if_read,
                "tokens_saved": tokens_saved,
                "savings_ratio": savings_ratio,
                "warnings": warnings,
                "entities": entities,
            }
        )
    finally:
        store.close()


def _list_files(args: dict[str, Any]) -> str:
    """Return indexed files with language, LOC, and entity count (T12.3)."""
    language_filter = args.get("language")

    store = _open_store()
    try:
        if language_filter:
            rows = store.conn.execute(
                "SELECT f.path, f.language, f.loc, COUNT(e.entity_id) "
                "FROM files f LEFT JOIN entities e ON e.file = f.path "
                "WHERE f.language = ? "
                "GROUP BY f.path, f.language, f.loc ORDER BY f.path",
                [language_filter],
            ).fetchall()
        else:
            rows = store.conn.execute(
                "SELECT f.path, f.language, f.loc, COUNT(e.entity_id) "
                "FROM files f LEFT JOIN entities e ON e.file = f.path "
                "GROUP BY f.path, f.language, f.loc ORDER BY f.path"
            ).fetchall()
    finally:
        store.close()

    files = [{"path": r[0], "language": r[1], "loc": r[2] or 0, "entity_count": r[3]} for r in rows]
    return json.dumps({"total": len(files), "files": files})


def _repo_root_for_db() -> Path:
    """Best-effort repo root for the active DB, always absolute.

    A discovered/standard DB lives at ``<root>/.codegraph/graph.duckdb``, so the
    root is two levels up. Otherwise fall back to the current working directory.
    The result is resolved to an absolute path: ``find_stale_files`` yields
    absolute paths, and ``index_one_file`` does ``abs_path.relative_to(root)`` --
    which raises (and silently no-ops the reindex) if *root* is relative like
    ``Path('.')``. Resolving here keeps reindex working for a relative ``--db``.
    """
    db = get_db_path()
    if db.parent.name == ".codegraph":
        return db.parent.parent.resolve()
    return Path(".").resolve()


# Cap on files reindexed in a single MCP call -- beyond this, suggest the CLI so
# an agent call doesn't block on a full cold index.
_REINDEX_FILE_CAP = 500


def _index_status(_args: dict[str, Any]) -> str:
    """Return index-level statistics and staleness indicator (T12.3)."""
    store = _open_store()
    try:
        n_files = store.count_files()
        n_entities = store.count_entities()
        n_edges = store.count_edges()
        n_embedded = store.count_embedded()
        n_summarized = store.count_summarized()
    finally:
        store.close()

    stale_files = 0
    try:
        from codegraph.sync.watcher import count_stale_files

        stale_files = count_stale_files(_repo_root_for_db(), get_db_path())
    except Exception:  # noqa: BLE001 — staleness check is best-effort
        pass

    return json.dumps(
        {
            "db_path": str(get_db_path()),
            "files": n_files,
            "entities": n_entities,
            "edges": n_edges,
            "embedded": n_embedded,
            "summarized": n_summarized,
            "stale_files": stale_files,
            "stale": stale_files > 0,
        }
    )


def _reindex(args: dict[str, Any]) -> str:
    """Re-parse only the files changed since the last index (T17.1).

    Reuses ``find_stale_files`` + ``index_one_file`` so an agent can refresh a
    stale index from within the chat. Caps the batch at ``_REINDEX_FILE_CAP`` and
    suggests the CLI for larger refreshes.
    """
    import time

    from codegraph.sync.watcher import find_stale_files, index_one_file

    no_embed = bool(args.get("no_embed", False))
    db = get_db_path()
    if not db.exists():
        return json.dumps({"error": f"No graph database at {db}. Run `codegraph index <repo>`."})

    root = _repo_root_for_db()
    stale = find_stale_files(root, db)

    if not stale:
        _stale_cache.set(0)
        return json.dumps(
            {"reindexed": 0, "entities": 0, "elapsed_ms": 0.0, "message": "Index already fresh."}
        )

    if len(stale) > _REINDEX_FILE_CAP:
        return json.dumps(
            {
                "error": (
                    f"{len(stale)} files changed (> {_REINDEX_FILE_CAP}); that is a large "
                    "refresh. Run `codegraph index <repo>` in a terminal instead."
                ),
                "stale_files": len(stale),
            }
        )

    start = time.monotonic()
    total_entities = 0
    reindexed = 0
    failed = 0
    for abs_path in stale:
        try:
            total_entities += index_one_file(root, abs_path, db, no_embed=no_embed)
            reindexed += 1
        except Exception:  # noqa: BLE001 — one bad file shouldn't abort the batch
            failed += 1
            continue
    elapsed_ms = (time.monotonic() - start) * 1000.0

    if failed == 0:
        _stale_cache.set(0)
    else:
        _stale_cache.reset()
    return json.dumps(
        {
            "reindexed": reindexed,
            "entities": total_entities,
            "failed": failed,
            "elapsed_ms": round(elapsed_ms, 1),
            "no_embed": no_embed,
        }
    )


# Entity kinds worth summarizing (modules are too coarse; variables too granular).
_SUMMARIZABLE_TYPES = ("function", "method", "class", "interface")

# Cap on entities handled per get_unsummarized_entities / store_summaries call, so
# an agent batch stays small and the inline re-embed never blocks for long.
_SUMMARIZE_BATCH_CAP = 200


def _placeholders(n: int) -> str:
    """Build a ``?, ?, ...`` parameter list of length *n* for a SQL IN clause."""
    return ", ".join(["?"] * n)


def _get_unsummarized_entities(args: dict[str, Any]) -> str:
    """Return a batch of entities with no summary yet, for the agent to describe."""
    limit = max(1, min(int(args.get("limit", 20)), _SUMMARIZE_BATCH_CAP))
    where = (
        f"(summary IS NULL OR summary = '') AND type IN ({_placeholders(len(_SUMMARIZABLE_TYPES))})"
    )
    store = _open_store()
    try:
        rows = store.conn.execute(
            f"SELECT entity_id, type, qualified_name, file, start_line, signature, raw_source "
            f"FROM entities WHERE {where} ORDER BY entity_id LIMIT ?",
            [*_SUMMARIZABLE_TYPES, limit],
        ).fetchall()
        remaining_row = store.conn.execute(
            f"SELECT COUNT(*) FROM entities WHERE {where}",
            list(_SUMMARIZABLE_TYPES),
        ).fetchone()
    finally:
        store.close()
    items = [
        {
            "entity_id": r[0],
            "type": r[1],
            "qualified_name": r[2],
            "location": f"{r[3]}:{r[4]}",
            "signature": r[5],
            "source_preview": _source_preview(r[6]),
        }
        for r in rows
    ]
    remaining = int(remaining_row[0]) if remaining_row else 0
    return json.dumps({"count": len(items), "remaining": remaining, "entities": items})


def _reembed_entities(store: GraphStore, entity_ids: list[str]) -> int:
    """Rebuild + store embeddings for specific entities (their summary just changed).

    Non-fatal: if the embedding stack is unavailable, summaries are still saved and
    the next ``codegraph index`` picks up the embed-hash drift.
    """
    if not entity_ids:
        return 0
    try:
        from codegraph.embeddings.chunking import build_embed_input_from_fields, embed_input_hash
        from codegraph.embeddings.pipeline import embed_batch
    except Exception:  # noqa: BLE001 — torch/model unavailable
        return 0

    rows = store.conn.execute(
        f"SELECT entity_id, type, qualified_name, signature, docstring, raw_source, summary "
        f"FROM entities WHERE entity_id IN ({_placeholders(len(entity_ids))})",
        entity_ids,
    ).fetchall()
    pending: list[tuple[str, str, str]] = []
    for eid, etype, qname, sig, doc, raw, summary in rows:
        text = build_embed_input_from_fields(etype, qname, sig, doc, raw, summary)
        pending.append((eid, text, embed_input_hash(text)))
    if not pending:
        return 0
    try:
        vectors = embed_batch([p[1] for p in pending])
        store.update_embeddings(
            [(pending[i][0], vectors[i].tolist(), pending[i][2]) for i in range(len(pending))]
        )
    except Exception:  # noqa: BLE001 — embedding failure is non-fatal; summaries persist
        return 0
    return len(pending)


def _store_summaries(args: dict[str, Any]) -> str:
    """Persist agent-written summaries and re-embed those entities (write tool)."""
    raw_items = args.get("items")
    if not isinstance(raw_items, list):
        return json.dumps({"error": "items must be a list of {entity_id, summary} objects."})
    if len(raw_items) > _SUMMARIZE_BATCH_CAP:
        return json.dumps(
            {
                "error": (
                    f"{len(raw_items)} items (> {_SUMMARIZE_BATCH_CAP}); split into smaller "
                    "batches."
                )
            }
        )

    pairs: list[tuple[str, str]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        eid = str(it.get("entity_id", "")).strip()
        summary = str(it.get("summary", "")).strip()
        if eid and summary:
            pairs.append((eid, summary))
    if not pairs:
        return json.dumps(
            {"stored": 0, "reembedded": 0, "message": "No valid {entity_id, summary} items."}
        )

    db = get_db_path()
    if not db.exists():
        return json.dumps({"error": f"No graph database at {db}. Run `codegraph index <repo>`."})

    store = GraphStore(db, read_only=False)
    try:
        store.update_summaries(pairs)
        reembedded = _reembed_entities(store, [p[0] for p in pairs])
    finally:
        store.close()
    return json.dumps({"stored": len(pairs), "reembedded": reembedded})


_HANDLERS = {
    "search_code": _search_code,
    "get_entity_context": _get_entity_context,
    "impact_analysis": _impact_analysis,
    "ask_codebase": _ask_codebase,
    "trace_path": _trace_path,
    "get_context": _get_context,
    "list_files": _list_files,
    "index_status": _index_status,
    "reindex": _reindex,
    "get_unsummarized_entities": _get_unsummarized_entities,
    "store_summaries": _store_summaries,
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


def _warm_embedding_model() -> None:
    """Load the embedding model in the MAIN thread at startup.

    ``get_context`` embeds its query for semantic search. Sync MCP handlers run
    via ``anyio.to_thread``, so a lazy first load would import the heavy
    sentence-transformers / torch / scikit-learn stack inside a worker thread
    while the asyncio stdio loop runs in the main thread -- and a first-time
    import of that stack off the main thread can deadlock or stall for minutes,
    making the first ``get_context`` appear frozen. Pre-loading here, in the main
    thread before the serve loop starts, makes the first call fast and reliable.

    Skipped when the index has no embeddings. Non-fatal: any failure just falls
    back to lazy loading + literal-only search (handlers already handle that).
    """
    import sys

    try:
        db = get_db_path()
        if not db.exists():
            return
        with GraphStore(db, read_only=True) as store:
            if store.count_embedded() == 0:
                return
        from codegraph.embeddings.pipeline import embed_one

        embed_one("warmup")
        print("CodeGraph: embedding model ready.", file=sys.stderr)
    except Exception:  # noqa: BLE001 — warmup is best-effort; lazy load still works
        pass


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

    # Warm the embedding model in the main thread BEFORE serving, so the first
    # get_context doesn't trigger a heavy off-main-thread import that can hang.
    _warm_embedding_model()

    import anyio

    anyio.run(_serve)


if __name__ == "__main__":
    main()
