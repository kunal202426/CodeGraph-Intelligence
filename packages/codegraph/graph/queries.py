"""Canned graph queries used by the CLI and (later) the FastAPI / MCP servers.

Each function takes a raw `duckdb.DuckDBPyConnection` (i.e. `store.conn`) and
returns plain Python tuples / dataclasses. Keep these query functions side-
effect-free: callers wrap them in transactions or surface them as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


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
