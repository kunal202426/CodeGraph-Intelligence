# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Graph traversal helpers — BFS path finding over the call graph.

Public API
----------
find_shortest_path(conn, src_id, dst_id, max_hops=7) -> list[str] | None
    Shortest directed call chain between two entity_ids.
"""

from __future__ import annotations

from collections import deque

import duckdb

# SQL to fetch outgoing call targets for one entity, filtering out external
# and unresolved (provisional) entities so BFS stays inside the index.
_CALLS_SQL = """
SELECT DISTINCT dst_id
FROM edges
WHERE src_id = ?
  AND type = 'calls'
  AND dst_id NOT LIKE 'external:%'
  AND dst_id NOT LIKE '%:?%'
"""


def find_shortest_path(
    conn: duckdb.DuckDBPyConnection,
    src_id: str,
    dst_id: str,
    max_hops: int = 7,
) -> list[str] | None:
    """Return the shortest call path from *src_id* to *dst_id*.

    Performs BFS over directed ``calls`` edges.  Returns a list of
    entity_ids (including both endpoints), or ``None`` if no path exists
    within *max_hops* steps.

    External (``external:...``) and unresolved (``...:?...``) edge targets
    are excluded from traversal so the BFS stays within the indexed codebase.

    Args:
        conn:     DuckDB connection with the CodeGraph schema.
        src_id:   Starting entity_id.
        dst_id:   Target entity_id.
        max_hops: Maximum number of call edges to follow (default 7).

    Returns:
        ``[src_id, ..., dst_id]`` on success, ``None`` if unreachable.
    """
    if src_id == dst_id:
        return [src_id]

    # BFS: queue of (current_id, path_from_src_to_current)
    queue: deque[tuple[str, list[str]]] = deque([(src_id, [src_id])])
    visited: set[str] = {src_id}

    while queue:
        current, path = queue.popleft()

        # Stop expanding this branch if we have already used max_hops edges.
        if len(path) - 1 >= max_hops:
            continue

        for (next_id,) in conn.execute(_CALLS_SQL, [current]).fetchall():
            if next_id in visited:
                continue
            new_path = [*path, next_id]
            if next_id == dst_id:
                return new_path
            visited.add(next_id)
            queue.append((next_id, new_path))

    return None
