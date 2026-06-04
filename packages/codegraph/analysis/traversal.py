"""Graph traversal helpers — BFS path finding over the call graph.

Public API
----------
find_shortest_path(conn, src_id, dst_id, max_hops=7) -> list[str] | None
    Shortest directed call chain between two entity_ids.
"""

from __future__ import annotations

import duckdb


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
    raise NotImplementedError
