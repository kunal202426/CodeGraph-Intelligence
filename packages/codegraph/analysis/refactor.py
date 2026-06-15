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
  * Framework-registered entities — anything decorated with a registration
    decorator (Typer `@app.command`, FastAPI/Flask routes, pytest `@fixture`,
    Celery `@task`, …). These are invoked indirectly, so no in-graph caller is
    expected; flagging them was the dominant source of false positives.
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

import re
from dataclasses import dataclass

import duckdb

# Names that are entrypoints / implicitly invoked, never "dead" even with no callers.
_ENTRYPOINT_NAMES = frozenset({"main", "__main__"})

# Final segment of a decorator that registers an entity with a framework or the
# test runner — the entity is invoked indirectly (CLI dispatch, HTTP routing,
# fixture injection, task queue), which the static call graph cannot see. Such an
# entity having no in-graph callers does NOT make it dead, so we exclude it.
_REGISTRATION_DECORATORS = frozenset(
    {
        "command",  # Typer / Click
        "callback",  # Typer
        "fixture",  # pytest
        "route",  # Flask / Starlette
        "websocket",  # FastAPI / Starlette
        "get",  # HTTP route verbs (FastAPI / Flask / APIRouter)
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "task",  # Celery / task queues
    }
)

_DECORATOR_RE = re.compile(r"^\s*@\s*([\w.]+)")


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


def _is_framework_registered(raw_source: str | None) -> bool:
    """True if the entity's leading decorators register it with a framework or
    the test runner (Typer command, HTTP route, pytest fixture, …).

    Inspects only the decorator block at the top of `raw_source` (lines before
    the `def`/`class`). Matches on the decorator's final dotted segment, so both
    ``@app.command()`` and ``@fixture`` are caught.
    """
    if not raw_source:
        return False
    for line in raw_source.splitlines():
        stripped = line.strip()
        if stripped.startswith("@"):
            match = _DECORATOR_RE.match(line)
            if match and match.group(1).split(".")[-1] in _REGISTRATION_DECORATORS:
                return True
            continue
        if stripped.startswith(("def ", "async def ", "class ")):
            break  # reached the definition — no more decorators
        if stripped == "":
            continue  # blank line between decorators
        break  # anything else: not a decorator block
    return False


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
        SELECT e.entity_id, e.type, e.name, e.file, e.start_line, e.raw_source
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
        if not _is_excluded(r[2]) and not _is_framework_registered(r[5])
    ]
