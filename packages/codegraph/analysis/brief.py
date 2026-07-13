# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Project brief: a small, pre-computed session-start orientation summary.

The `get_context`/`search_code` tools answer "where is X" once you already
know roughly what you're looking for. A fresh session doesn't -- it burns
several exploratory calls just figuring out the shape of the codebase before
it can ask a targeted question. `build_project_brief` answers that first
question in one cheap query pass: language/size stats, architectural layers
(reusing the existing layer heuristic), the highest fan-in entities ("hot
paths" -- the functions everything else calls into), and HTTP entry points
(reusing the route->handler edges the resolver already produces).

Computed on demand from the existing indexed tables -- no new storage, no
caching layer. A handful of aggregate SQL queries over `entities`/`edges`,
fast even on a large repo since they're index-backed COUNT/GROUP BY, not a
graph walk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from codegraph.analysis.patterns import analyze_layers

_HOT_PATH_LIMIT = 8
_ENTRY_POINT_LIMIT = 8
_DIRS_PER_LAYER = 6


@dataclass(frozen=True)
class HotPath:
    """An entity many other entities call into -- a likely core abstraction."""

    name: str  # qualified_name
    file: str
    callers: int


@dataclass(frozen=True)
class EntryPoint:
    """An HTTP route and the handler it resolves to."""

    route: str  # "GET /api/stats"
    handler: str  # qualified_name
    file: str


@dataclass
class ProjectBrief:
    file_count: int
    entity_count: int
    languages: dict[str, int] = field(default_factory=dict)  # language -> file count
    layers: dict[str, list[str]] = field(default_factory=dict)  # layer -> dirs (capped)
    layer_more: dict[str, int] = field(default_factory=dict)  # layer -> dirs omitted
    hot_paths: list[HotPath] = field(default_factory=list)
    entry_points: list[EntryPoint] = field(default_factory=list)


def build_project_brief(conn: duckdb.DuckDBPyConnection) -> ProjectBrief:
    """Compute a `ProjectBrief` from the current index."""
    file_count = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    entity_count = conn.execute("SELECT count(*) FROM entities").fetchone()[0]

    languages = dict(
        conn.execute(
            "SELECT language, count(*) FROM files GROUP BY language ORDER BY count(*) DESC"
        ).fetchall()
    )

    layers_present = analyze_layers(conn).layers_present
    layers: dict[str, list[str]] = {}
    layer_more: dict[str, int] = {}
    for layer, dirs in layers_present.items():
        layers[layer] = dirs[:_DIRS_PER_LAYER]
        if len(dirs) > _DIRS_PER_LAYER:
            layer_more[layer] = len(dirs) - _DIRS_PER_LAYER

    hot_rows = conn.execute(
        """
        SELECT e.qualified_name, e.file, cnt.n
        FROM (
            SELECT dst_id, count(*) AS n
            FROM edges
            WHERE type = 'calls'
              AND dst_id NOT LIKE 'external:%'
              AND dst_id NOT LIKE 'route:%'
              AND dst_id NOT LIKE 'wildcard:%'
            GROUP BY dst_id
            ORDER BY n DESC
            LIMIT ?
        ) cnt
        JOIN entities e ON e.entity_id = cnt.dst_id
        ORDER BY cnt.n DESC
        """,
        [_HOT_PATH_LIMIT],
    ).fetchall()
    hot_paths = [HotPath(name=r[0], file=r[1], callers=r[2]) for r in hot_rows]

    entry_rows = conn.execute(
        """
        SELECT e.src_id, t.qualified_name, t.file
        FROM edges e
        JOIN entities t ON t.entity_id = e.dst_id
        WHERE e.type = 'calls' AND e.src_id LIKE 'route:%' AND e.dst_id NOT LIKE 'external:%'
        ORDER BY e.src_id
        LIMIT ?
        """,
        [_ENTRY_POINT_LIMIT],
    ).fetchall()
    entry_points = [
        EntryPoint(route=r[0].removeprefix("route:"), handler=r[1], file=r[2]) for r in entry_rows
    ]

    return ProjectBrief(
        file_count=file_count,
        entity_count=entity_count,
        languages=languages,
        layers=layers,
        layer_more=layer_more,
        hot_paths=hot_paths,
        entry_points=entry_points,
    )
