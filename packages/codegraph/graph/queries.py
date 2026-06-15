# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Canned graph queries used by the CLI, FastAPI server, and MCP tools.

Each function takes a raw `duckdb.DuckDBPyConnection` (i.e. `store.conn`) and
returns plain Python dataclasses. Keep these query functions side-effect-free:
callers wrap them in transactions or surface them as JSON.

Module layout
-------------
search_literal     — substring + docstring ILIKE, ranked by match quality
vector_search      — cosine similarity over entity embeddings
hybrid_search      — Reciprocal Rank Fusion of literal + vector results
find_entity_by_name_or_id — resolve a user-typed reference to EntityRow(s)
find_dependencies  — BFS outbound walk (imports + calls) → DepTree
find_callers       — BFS inbound walk (reverse calls) → ImpactTree
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import duckdb

from codegraph.ai.tokens import estimate_tokens

# Entity-id language prefixes — used by `find_entity_by_name_or_id` to tell
# "this is an entity_id, not a free-form name" without a regex.
# Must cover every lang CodeGraph indexes so that passing a full entity_id as
# a query (e.g. "go:pkg/server.go:Handler") triggers the exact-match branch.
_ENTITY_ID_PREFIXES = ("py:", "ts:", "js:", "go:", "rs:", "java:", "rb:", "php:", "c:", "cpp:")


def read_baseline_tokens(conn: duckdb.DuckDBPyConnection, files: Iterable[str]) -> int:
    """Estimate the tokens an agent would spend reading *files* in full.

    Sums ``estimate_tokens`` over the ``raw_source`` of every indexed entity in
    the given files — i.e. roughly what it costs to read those whole files. Used
    to show how many tokens ``get_context`` / the ``context`` CLI saved versus
    opening the files directly. The result is an estimate (4-chars/token
    heuristic, and it omits non-entity lines like imports/blank space), so it is
    a conservative lower bound on the real read cost.
    """
    unique = [f for f in dict.fromkeys(files) if f]
    if not unique:
        return 0
    placeholders = ",".join(["?"] * len(unique))
    rows = conn.execute(
        f"SELECT raw_source FROM entities "
        f"WHERE file IN ({placeholders}) AND raw_source IS NOT NULL",
        unique,
    ).fetchall()
    return sum(estimate_tokens(r[0]) for r in rows)


@dataclass(frozen=True)
class SearchHit:
    """One row from a literal/hybrid search result."""

    entity_id: str
    type: str
    name: str
    qualified_name: str
    file: str
    start_line: int
    docstring: str | None


def search_literal(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    limit: int = 20,
) -> list[SearchHit]:
    """Substring + docstring ILIKE search.

    Ranking (best → worst):
      1. Exact case-insensitive name match
      2. Name starts with the query
      3. Name contains the query (anywhere)
      4. Docstring-only match (name didn't match at all)

    Ties broken by shorter `name` length (more specific) then alphabetical.
    """
    if not query:
        return []
    pattern = f"%{query}%"
    rows = conn.execute(
        """
        SELECT entity_id, type, name, qualified_name, file, start_line, docstring
        FROM entities
        WHERE name ILIKE ? OR docstring ILIKE ?
        ORDER BY
          CASE
            WHEN LOWER(name) = LOWER(?)          THEN 0
            WHEN name ILIKE ? || '%'             THEN 1
            WHEN name ILIKE ?                    THEN 2
            ELSE 3
          END,
          LENGTH(name),
          name
        LIMIT ?
        """,
        [pattern, pattern, query, query, pattern, limit],
    ).fetchall()
    return [
        SearchHit(
            entity_id=r[0],
            type=r[1],
            name=r[2],
            qualified_name=r[3],
            file=r[4],
            start_line=r[5],
            docstring=r[6],
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# Vector search (T3.2)

_EMBEDDING_DIM = 384


@dataclass(frozen=True)
class VectorHit:
    """One row from a cosine-similarity vector search."""

    entity_id: str
    type: str
    name: str
    qualified_name: str
    file: str
    start_line: int
    docstring: str | None
    similarity: float


def vector_search(
    conn: duckdb.DuckDBPyConnection,
    query_vector: list[float],
    limit: int = 20,
) -> list[VectorHit]:
    """Cosine-similarity search over `entities.embedding`.

    `query_vector` must be a list of EMBEDDING_DIM plain Python floats (call
    `.tolist()` on the numpy array from the embedder). Entities without an
    embedding are skipped. Results are ordered by descending similarity.
    """
    if not query_vector:
        return []
    rows = conn.execute(
        f"""
        SELECT entity_id, type, name, qualified_name, file, start_line, docstring,
               array_cosine_similarity(embedding, ?::FLOAT[{_EMBEDDING_DIM}]) AS sim
        FROM entities
        WHERE embedding IS NOT NULL
        ORDER BY sim DESC
        LIMIT ?
        """,
        [query_vector, limit],
    ).fetchall()
    return [
        VectorHit(
            entity_id=r[0],
            type=r[1],
            name=r[2],
            qualified_name=r[3],
            file=r[4],
            start_line=r[5],
            docstring=r[6],
            similarity=float(r[7]),
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# Hybrid search — Reciprocal Rank Fusion of literal + vector (T3.4)


@dataclass(frozen=True)
class HybridHit:
    """A fused search result. `retrievers` records which retrievers found it."""

    entity_id: str
    type: str
    name: str
    qualified_name: str
    file: str
    start_line: int
    docstring: str | None
    score: float
    retrievers: tuple[str, ...]  # subset of ("literal", "semantic"), ordered


def hybrid_search(
    conn: duckdb.DuckDBPyConnection,
    query_text: str,
    query_vector: list[float] | None,
    limit: int = 20,
    pool: int = 20,
    rrf_k: int = 60,
) -> list[HybridHit]:
    """Fuse literal + vector search via Reciprocal Rank Fusion.

    Each retriever returns up to `pool` ranked results; an entity's fused score
    is ``sum(1 / (rrf_k + rank))`` over the lists it appears in (rank is
    1-indexed). Pass an empty `query_text` to skip literal, or `None`
    `query_vector` to skip vector — so the three CLI modes share one path:

        literal   → hybrid_search(text, None)
        semantic  → hybrid_search("",   vec)
        hybrid    → hybrid_search(text, vec)
    """
    literal_hits = search_literal(conn, query_text, limit=pool) if query_text else []
    vector_hits = vector_search(conn, query_vector, limit=pool) if query_vector else []

    scores: dict[str, float] = {}
    retrievers: dict[str, list[str]] = {}
    # Common metadata keyed by entity_id (both hit types share these fields).
    meta: dict[str, tuple] = {}

    def _accumulate(hits, label: str) -> None:
        for rank, hit in enumerate(hits, start=1):
            eid = hit.entity_id
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (rrf_k + rank)
            retrievers.setdefault(eid, []).append(label)
            meta.setdefault(
                eid,
                (hit.type, hit.name, hit.qualified_name, hit.file, hit.start_line, hit.docstring),
            )

    _accumulate(literal_hits, "literal")
    _accumulate(vector_hits, "semantic")

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    results: list[HybridHit] = []
    for eid, score in ordered[:limit]:
        m = meta[eid]
        results.append(
            HybridHit(
                entity_id=eid,
                type=m[0],
                name=m[1],
                qualified_name=m[2],
                file=m[3],
                start_line=m[4],
                docstring=m[5],
                score=score,
                retrievers=tuple(retrievers[eid]),
            )
        )
    return results


# ----------------------------------------------------------------------
# Entity lookup + dependency BFS (T2.6 / T4.3)


@dataclass(frozen=True)
class EntityRow:
    entity_id: str
    type: str
    name: str
    file: str
    start_line: int


def find_entity_by_name_or_id(conn: duckdb.DuckDBPyConnection, query: str) -> list[EntityRow]:
    """Resolve a user-typed entity reference.

    If `query` looks like an entity_id (`py:...` / `ts:...` / `js:...`), return
    at most one row matching it exactly. Otherwise look up by `name` (exact,
    case-sensitive), then by `qualified_name`. Ordered by file/line for
    deterministic disambiguation output.
    """
    if not query:
        return []

    if query.startswith(_ENTITY_ID_PREFIXES):
        row = conn.execute(
            "SELECT entity_id, type, name, file, start_line FROM entities WHERE entity_id = ?",
            [query],
        ).fetchone()
        return [EntityRow(*row)] if row else []

    rows = conn.execute(
        """
        SELECT entity_id, type, name, file, start_line
        FROM entities
        WHERE name = ? OR qualified_name = ?
        ORDER BY file, start_line
        """,
        [query, query],
    ).fetchall()
    return [EntityRow(*r) for r in rows]


@dataclass(frozen=True)
class DepNode:
    """One outbound dependency from some parent entity."""

    entity_id: str  # dst_id — either a real entity_id or external:/wildcard: marker
    name: str  # display name (real entity name OR the dst_id verbatim for externals)
    type: str  # entity type, or "external" / "wildcard"
    file: str | None  # populated when dst is a real entity
    start_line: int | None
    edge_type: str  # "imports" / "calls"
    confidence: float
    is_external: bool  # True if dst has no matching entity row


@dataclass
class DepTree:
    """BFS result: a map from parent entity_id to its outbound DepNodes,
    truncated at the requested depth. The root is the starting entity_id."""

    root: str
    children: dict[str, list[DepNode]] = field(default_factory=dict)
    truncated: bool = False  # True when at least one branch hit the depth limit


def find_dependencies(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    depth: int = 3,
    edge_types: tuple[str, ...] = ("imports", "calls"),
) -> DepTree:
    """Breadth-first walk over outbound edges of `entity_id`.

    - Follows only edges whose `type` is in `edge_types` (defaults to imports + calls).
    - Truncates each branch at `depth` hops.
    - Cycle-safe: visits each real entity at most once.
    - External / wildcard targets become leaves (no further traversal).
    """
    if depth <= 0:
        return DepTree(root=entity_id, children={}, truncated=True)

    visited: set[str] = {entity_id}
    children: dict[str, list[DepNode]] = {}
    truncated = False
    frontier: list[str] = [entity_id]
    type_placeholders = ",".join(["?"] * len(edge_types))

    for level in range(depth):
        next_frontier: list[str] = []
        for parent in frontier:
            rows = conn.execute(
                f"""
                SELECT e.dst_id, e.type, e.confidence,
                       ent.type, ent.name, ent.file, ent.start_line
                FROM edges e
                LEFT JOIN entities ent ON ent.entity_id = e.dst_id
                WHERE e.src_id = ? AND e.type IN ({type_placeholders})
                ORDER BY e.line, e.dst_id
                """,
                [parent, *edge_types],
            ).fetchall()

            kids: list[DepNode] = []
            for dst_id, etype, conf, ent_type, ent_name, ent_file, ent_line in rows:
                is_external = ent_name is None
                kids.append(
                    DepNode(
                        entity_id=dst_id,
                        name=ent_name if ent_name is not None else dst_id,
                        type=ent_type if ent_type is not None else _classify_unresolved(dst_id),
                        file=ent_file,
                        start_line=ent_line,
                        edge_type=etype,
                        confidence=conf,
                        is_external=is_external,
                    )
                )
                if not is_external and dst_id not in visited:
                    visited.add(dst_id)
                    if level < depth - 1:
                        next_frontier.append(dst_id)
                    else:
                        truncated = True

            if kids:
                children[parent] = kids

        frontier = next_frontier
        if not frontier:
            break

    return DepTree(root=entity_id, children=children, truncated=truncated)


def _classify_unresolved(dst_id: str) -> str:
    if dst_id.startswith("wildcard:"):
        return "wildcard"
    if dst_id.startswith("external:"):
        return "external"
    return "unresolved"


# ----------------------------------------------------------------------
# Reverse-call impact analysis (T4.3)


@dataclass(frozen=True)
class CallerNode:
    """One entity that calls the parent entity (an inbound `calls` edge)."""

    entity_id: str
    name: str
    type: str
    file: str | None
    start_line: int | None
    confidence: float


@dataclass
class ImpactTree:
    """Reverse-BFS result: map from a callee entity_id to its direct callers.

    The root is the entity whose blast radius we asked for. Walking
    `callers[root]` gives depth-1 callers; recursing gives the transitive set.
    """

    root: str
    callers: dict[str, list[CallerNode]] = field(default_factory=dict)
    truncated: bool = False  # True when at least one branch hit the depth limit
    total: int = 0  # distinct entities in the blast radius (excluding root)


def find_callers(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    depth: int = 3,
) -> ImpactTree:
    """Breadth-first walk over inbound `calls` edges of `entity_id`.

    Answers "what would break if this entity changed" by following call edges
    backwards: every `src_id` of a `calls` edge pointing at the current entity
    is a caller, then we recurse on those callers.

    - Truncates each branch at `depth` hops.
    - Cycle-safe: visits each entity at most once (recursion stops on revisit).
    - A caller that calls the parent from several lines appears once per parent.
    """
    if depth <= 0:
        return ImpactTree(root=entity_id, callers={}, truncated=True, total=0)

    visited: set[str] = {entity_id}
    callers: dict[str, list[CallerNode]] = {}
    truncated = False
    frontier: list[str] = [entity_id]

    for level in range(depth):
        next_frontier: list[str] = []
        for callee in frontier:
            rows = conn.execute(
                """
                SELECT e.src_id, e.confidence,
                       ent.type, ent.name, ent.file, ent.start_line
                FROM edges e
                LEFT JOIN entities ent ON ent.entity_id = e.src_id
                WHERE e.dst_id = ? AND e.type = 'calls'
                ORDER BY ent.file, ent.start_line, e.src_id
                """,
                [callee],
            ).fetchall()

            kids: list[CallerNode] = []
            seen_here: set[str] = set()  # dedupe multiple call sites from one caller
            for src_id, conf, ent_type, ent_name, ent_file, ent_line in rows:
                if src_id in seen_here:
                    continue
                seen_here.add(src_id)
                kids.append(
                    CallerNode(
                        entity_id=src_id,
                        name=ent_name if ent_name is not None else src_id,
                        type=ent_type if ent_type is not None else "unresolved",
                        file=ent_file,
                        start_line=ent_line,
                        confidence=conf,
                    )
                )
                if src_id not in visited:
                    visited.add(src_id)
                    if level < depth - 1:
                        next_frontier.append(src_id)
                    else:
                        truncated = True

            if kids:
                callers[callee] = kids

        frontier = next_frontier
        if not frontier:
            break

    return ImpactTree(
        root=entity_id,
        callers=callers,
        truncated=truncated,
        total=len(visited) - 1,  # exclude the root itself
    )
