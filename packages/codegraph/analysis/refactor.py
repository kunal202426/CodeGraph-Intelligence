# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Refactor-suggestion heuristics (T9.6).

Currently: dead-code detection — top-level functions and classes that nothing in
the indexed graph calls or imports. This is a *candidate* list, not proof: a
symbol can be reached in ways the static graph can't see (dynamic dispatch,
decorator-registered routes, external consumers), so the detector errs toward
excluding likely-live entities rather than flooding the caller with noise.

Excluded by design:
  * `main` / `__main__` — conventional entrypoints, always live.
  * Names starting with `test_` — invoked by the test runner, not the call graph.
  * Dunder methods (`__init__`, `__str__`, …) — called implicitly by Python.
  * Methods (by default) — `self.x()` call resolution is lossy in a static graph
    and produces far too many false positives. Pass `include_methods=True` to
    opt in when you understand the trade-off.

Public API
----------
find_dead_code(conn, *, include_methods=False) -> list[DeadEntity]
    Return functions/classes with no inbound calls or imports, sorted by
    file and start line.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

# Names that are entrypoints / implicitly invoked, never "dead" even with no callers.
_ENTRYPOINT_NAMES = frozenset({"main", "__main__"})


@dataclass(frozen=True)
class DeadEntity:
    """A code entity with no inbound calls/imports — a dead-code candidate."""

    entity_id: str
    type: str
    name: str
    file: str
    start_line: int


def _is_excluded(name: str) -> bool:
    if name in _ENTRYPOINT_NAMES:
        return True
    if name.startswith("test_"):  # test functions are invoked by the test runner
        return True
    return name.startswith("__") and name.endswith("__")  # dunders (implicit)


def find_dead_code(
    conn: duckdb.DuckDBPyConnection,
    *,
    include_methods: bool = False,
) -> list[DeadEntity]:
    """Find functions/classes that are never an edge destination (calls/imports).

    Returns candidates sorted by file then line. Methods are excluded by default
    (see module docstring); set `include_methods=True` to include them.
    """
    types = ["function", "class"]
    if include_methods:
        types.append("method")
    type_placeholders = ",".join(["?"] * len(types))

    rows = conn.execute(
        f"""
        SELECT e.entity_id, e.type, e.name, e.file, e.start_line
        FROM entities e
        WHERE e.type IN ({type_placeholders})
          AND NOT EXISTS (
            SELECT 1 FROM edges g
            WHERE g.dst_id = e.entity_id AND g.type IN ('calls', 'imports')
          )
        ORDER BY e.file, e.start_line
        """,
        types,
    ).fetchall()

    return [
        DeadEntity(entity_id=r[0], type=r[1], name=r[2], file=r[3], start_line=r[4])
        for r in rows
        if not _is_excluded(r[2])
    ]
