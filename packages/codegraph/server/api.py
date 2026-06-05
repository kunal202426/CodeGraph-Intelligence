# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""FastAPI app exposing the graph for the web UI (T6.1).

`create_app(db_path)` builds an app bound to one indexed DuckDB file. Each
request opens its own read-only connection (DuckDB allows many concurrent
readers), so the app is safe under the threadpool FastAPI uses for sync routes.

Endpoints (all under /api):
  GET  /health                      → liveness probe
  GET  /graph?type=module           → file-level import graph (nodes + edges)
  GET  /graph?type=entity&file=...   → entities in one file + their out-edges
  GET  /search?q=&semantic=&limit=   → hybrid/literal search hits
  GET  /entity/{entity_id}           → full UIR record
  GET  /impact/{entity_id}?depth=    → reverse-call blast radius
  POST /ask    {query}               → SSE stream of a grounded GraphRAG answer

The built frontend (if present) is mounted at / by `codegraph serve` (T6.6).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from codegraph.graph.queries import find_callers, hybrid_search

# Vite dev server origin (T6.2+). The packaged build is served same-origin.
_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

# Default location of the built frontend (Vite's build outDir, see vite.config.ts).
_DEFAULT_STATIC = Path(__file__).with_name("static")


class AskRequest(BaseModel):
    query: str
    k: int = 15
    max_tokens: int = 2000


def create_app(db_path: Path | str, static_dir: Path | str | None = None) -> FastAPI:
    """Build a FastAPI app bound to the indexed graph at `db_path`.

    If a built frontend exists at `static_dir` (default: the packaged
    `server/static`), it is mounted at `/` as an SPA — added after the API
    routes so `/api/*` always takes precedence.
    """
    db_path = Path(db_path)
    app = FastAPI(title="CodeGraph", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_conn() -> Iterator[duckdb.DuckDBPyConnection]:
        if not db_path.exists():
            raise HTTPException(status_code=503, detail=f"No graph database at {db_path}.")
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/graph")
    def graph(
        type: str = Query("module", pattern="^(module|entity)$"),
        file: str | None = None,
        conn: duckdb.DuckDBPyConnection = Depends(get_conn),
    ) -> dict[str, list[dict]]:
        if type == "module":
            return _module_graph(conn)
        if not file:
            raise HTTPException(status_code=400, detail="type=entity requires a file= param.")
        return _entity_graph(conn, file)

    @app.get("/api/search")
    def search(
        q: str = Query(..., min_length=1),
        semantic: bool = False,
        limit: int = 20,
        conn: duckdb.DuckDBPyConnection = Depends(get_conn),
    ) -> dict[str, list[dict]]:
        query_vector = _maybe_embed(q) if semantic else None
        text_arg = "" if (semantic and query_vector is not None) else q
        hits = hybrid_search(conn, text_arg, query_vector, limit=limit)
        return {
            "results": [
                {
                    "entity_id": h.entity_id,
                    "type": h.type,
                    "name": h.name,
                    "qualified_name": h.qualified_name,
                    "file": h.file,
                    "start_line": h.start_line,
                    "docstring": h.docstring,
                    "score": h.score,
                    "retrievers": list(h.retrievers),
                }
                for h in hits
            ]
        }

    @app.get("/api/entity/{entity_id:path}")
    def entity(
        entity_id: str,
        conn: duckdb.DuckDBPyConnection = Depends(get_conn),
    ) -> dict:
        row = conn.execute(
            """
            SELECT entity_id, type, name, qualified_name, language, file,
                   start_line, end_line, signature, docstring, raw_source,
                   is_exported, is_async, parent_id
            FROM entities WHERE entity_id = ?
            """,
            [entity_id],
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"No entity {entity_id!r}.")
        cols = (
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
            "is_exported",
            "is_async",
            "parent_id",
        )
        return dict(zip(cols, row, strict=True))

    @app.get("/api/impact/{entity_id:path}")
    def impact(
        entity_id: str,
        depth: int = 3,
        conn: duckdb.DuckDBPyConnection = Depends(get_conn),
    ) -> dict:
        tree = find_callers(conn, entity_id, depth=depth)
        return {
            "root": tree.root,
            "total": tree.total,
            "truncated": tree.truncated,
            "callers": {
                callee: [
                    {
                        "entity_id": c.entity_id,
                        "name": c.name,
                        "type": c.type,
                        "file": c.file,
                        "start_line": c.start_line,
                    }
                    for c in callers
                ]
                for callee, callers in tree.callers.items()
            },
        }

    @app.post("/api/ask")
    def ask(body: AskRequest) -> StreamingResponse:
        if not db_path.exists():
            raise HTTPException(status_code=503, detail="No graph database.")
        from codegraph.ai.graphrag import GraphRAG
        from codegraph.ai.llm import LLM, LLMError
        from codegraph.graph.store import GraphStore

        def event_stream() -> Iterator[str]:
            store = GraphStore(db_path, read_only=True)
            try:
                if store.count_embedded() == 0:
                    yield _sse(
                        {"error": "This index has no embeddings; re-index without --no-embed."}
                    )
                    return
                rag = GraphRAG(store, LLM())
                try:
                    for token in rag.ask_stream(body.query, k=body.k, max_tokens=body.max_tokens):
                        yield _sse({"token": token})
                except LLMError as exc:
                    yield _sse({"error": str(exc)})
                except Exception as exc:  # noqa: BLE001 - surface to the client, don't 500
                    yield _sse({"error": f"{type(exc).__name__}: {exc}"})
                else:
                    yield _sse({"done": True})
            finally:
                store.close()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # Mount the built SPA last so it doesn't shadow the /api routes above.
    static = Path(static_dir) if static_dir is not None else _DEFAULT_STATIC
    if (static / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(static), html=True), name="static")

    return app


# ----------------------------------------------------------------------
# Helpers


def _sse(payload: dict) -> str:
    """Format one Server-Sent Event line."""
    return f"data: {json.dumps(payload)}\n\n"


def _maybe_embed(query: str) -> list[float] | None:
    """Embed `query`, or None if the model is unavailable (degrade to literal)."""
    try:
        from codegraph.embeddings.pipeline import embed_one

        return embed_one(query).tolist()
    except Exception:  # noqa: BLE001 - model/torch unavailable → caller falls back
        return None


def _module_graph(conn: duckdb.DuckDBPyConnection) -> dict[str, list[dict]]:
    """File-level import graph keyed by each file's module entity_id.

    Nodes are module entities (id = entity_id, label = file path) so the UI can
    fetch a clicked node's full record from /api/entity. Edges map file→file
    imports onto the corresponding module entity_ids.
    """
    nodes = [
        {"id": eid, "label": file, "language": language}
        for eid, file, language in conn.execute(
            """
            SELECT m.entity_id, m.file, f.language
            FROM entities m
            JOIN files f ON f.path = m.file
            WHERE m.type = 'module'
            ORDER BY m.file
            """
        ).fetchall()
    ]
    edges = [
        {"source": src, "target": dst, "type": "imports"}
        for src, dst in conn.execute(
            """
            SELECT DISTINCT sm.entity_id, dm.entity_id
            FROM edges e
            JOIN entities s ON s.entity_id = e.src_id
            JOIN entities d ON d.entity_id = e.dst_id
            JOIN entities sm ON sm.file = s.file AND sm.type = 'module'
            JOIN entities dm ON dm.file = d.file AND dm.type = 'module'
            WHERE e.type = 'imports' AND s.file <> d.file
            """
        ).fetchall()
    ]
    return {"nodes": nodes, "edges": edges}


def _entity_graph(conn: duckdb.DuckDBPyConnection, file: str) -> dict[str, list[dict]]:
    nodes = [
        {"id": eid, "label": name, "type": etype, "start_line": line}
        for eid, name, etype, line in conn.execute(
            "SELECT entity_id, name, type, start_line FROM entities WHERE file = ? ORDER BY start_line",
            [file],
        ).fetchall()
    ]
    edges = [
        {"source": src, "target": dst, "type": etype}
        for src, dst, etype in conn.execute(
            """
            SELECT e.src_id, e.dst_id, e.type
            FROM edges e
            JOIN entities s ON s.entity_id = e.src_id
            WHERE s.file = ? AND e.type IN ('calls', 'imports')
            ORDER BY e.line
            """,
            [file],
        ).fetchall()
    ]
    return {"nodes": nodes, "edges": edges}
