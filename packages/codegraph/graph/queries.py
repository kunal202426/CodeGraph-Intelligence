"""Canned graph queries used by the CLI and (later) the FastAPI / MCP servers.

Each function takes a raw `duckdb.DuckDBPyConnection` (i.e. `store.conn`) and
returns plain Python tuples / dataclasses. Keep these query functions side-
effect-free: callers wrap them in transactions or surface them as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

# Entity-id language prefixes — used by `find_entity_by_name_or_id` to tell
# "this is an entity_id, not a free-form name" without a regex.
_ENTITY_ID_PREFIXES = ("py:", "ts:", "js:")


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
