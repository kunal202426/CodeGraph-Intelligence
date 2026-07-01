# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Import-cycle detection via Tarjan's strongly-connected-components.

A *strongly connected component* (SCC) of size >= 2 in the file-level import
graph is, by definition, a circular import: every file in the component can
reach every other one by following `imports` edges. We build the file graph
from resolved import edges, run an iterative Tarjan SCC, and report the
non-trivial components.

Tarjan runs iteratively (explicit work stack) rather than recursively so it
survives deep graphs — a recursive DFS would blow Python's ~1000-frame limit
on a real repo (fastapi indexes ~1100 files).
"""

from __future__ import annotations

import duckdb


def build_import_graph(conn: duckdb.DuckDBPyConnection) -> dict[str, set[str]]:
    """Build a file -> {imported files} adjacency map from resolved imports.

    Only in-repo edges count: joining `edges` to `entities` on both endpoints
    drops `external:` / `wildcard:` targets (they have no entity row), and the
    `s.file <> d.file` filter drops self-imports (which can't form a 2+ cycle).
    Every file that appears as either endpoint becomes a node, so isolated
    importers still show up as singleton SCCs (and are filtered out later).
    """
    rows = conn.execute(
        """
        SELECT DISTINCT s.file AS src_file, d.file AS dst_file
        FROM edges e
        JOIN entities s ON s.entity_id = e.src_id
        JOIN entities d ON d.entity_id = e.dst_id
        WHERE e.type = 'imports' AND s.file <> d.file
        """
    ).fetchall()

    graph: dict[str, set[str]] = {}
    for src, dst in rows:
        graph.setdefault(src, set()).add(dst)
        graph.setdefault(dst, set())  # ensure the imported file is a node too
    return graph


def tarjan_scc(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return all strongly connected components of `graph`.

    `graph` maps each node to the set of nodes it points at. Iterative Tarjan;
    neighbours are visited in sorted order for deterministic output. Each
    returned component lists its member nodes (order within a component is the
    algorithm's pop order — callers that need stability should sort).
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = 0
    result: list[list[str]] = []

    for root in graph:
        if root in index_of:
            continue
        # Explicit DFS work stack of (node, neighbour-iterator).
        work: list[tuple[str, object]] = [(root, iter(sorted(graph[root])))]
        index_of[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True

        while work:
            v, neighbours = work[-1]
            descended = False
            for w in neighbours:  # type: ignore[assignment]
                if w not in index_of:
                    index_of[w] = lowlink[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, iter(sorted(graph[w]))))
                    descended = True
                    break
                if on_stack.get(w):
                    lowlink[v] = min(lowlink[v], index_of[w])
            if descended:
                continue

            # v is fully explored: if it's a root of an SCC, pop the component.
            if lowlink[v] == index_of[v]:
                component: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == v:
                        break
                result.append(component)

            work.pop()
            if work:  # propagate lowlink up to the parent frame
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[v])

    return result


def find_cycles(conn: duckdb.DuckDBPyConnection, min_size: int = 2) -> list[list[str]]:
    """Find import cycles: SCCs of the file import graph with >= `min_size` files.

    Returns a list of cycles, each a sorted list of file paths. The outer list
    is sorted for deterministic output.
    """
    graph = build_import_graph(conn)
    cycles = [sorted(scc) for scc in tarjan_scc(graph) if len(scc) >= min_size]
    cycles.sort()
    return cycles
