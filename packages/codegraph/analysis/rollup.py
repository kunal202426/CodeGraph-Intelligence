# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Structural file/directory rollups -- a free, always-fresh summary layer.

`project_brief` already proved the pattern: a handful of index-backed
aggregate queries over `entities`/`edges` are fast enough to compute on
demand, so there's no caching layer and nothing to invalidate. This module
extends the same idea down to individual files and directories -- "what's in
this file" and "what does this directory do" answered from the graph shape
alone (entity counts, names, call fan-in), with no LLM involvement and no
storage of its own.

This is the *free* tier. Where an agent has paid to write a real natural-
language description (via `store_summaries` with `scope="file"`/`"dir"`),
that's cached separately in `context_summaries` (see `graph/store.py`) and
preferred over this structural fallback by the callers in `server/
mcp_server.py`. A cold index with nobody having written a summary yet still
gets something better than a bare file list -- that's the point of keeping
this tier free.
"""

from __future__ import annotations

import duckdb

from codegraph.graph.store import escape_like
from codegraph.uir import hash_source

_FILE_ROLLUP_NAME_CAP = 8
_DIR_ROLLUP_TOP_ENTITIES = 5

# Entity type names that don't pluralize by just appending "s" (EntityType in
# uir.py: module/function/method/interface/type_alias/variable all do; class
# is the one exception -- "classs" is what a naive `f"{t}s"` produces).
_IRREGULAR_PLURALS = {"class": "classes"}


def _pluralize(entity_type: str) -> str:
    return _IRREGULAR_PLURALS.get(entity_type, f"{entity_type}s")


def build_file_rollup(conn: duckdb.DuckDBPyConnection, path: str) -> str:
    """One-line structural summary of a file: entity counts by type + names.

    Empty string for a file with no indexed entities (a config file, a
    generated file, etc.) -- callers treat that as "nothing to show", not an
    error.
    """
    rows = conn.execute(
        "SELECT type, name FROM entities WHERE file = ? ORDER BY start_line",
        [path],
    ).fetchall()
    if not rows:
        return ""

    counts: dict[str, int] = {}
    names: list[str] = []
    for entity_type, name in rows:
        counts[entity_type] = counts.get(entity_type, 0) + 1
        names.append(name)

    type_desc = ", ".join(
        f"{n} {t if n == 1 else _pluralize(t)}"
        for t, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    shown = names[:_FILE_ROLLUP_NAME_CAP]
    names_desc = ", ".join(shown)
    if len(names) > len(shown):
        names_desc += f", +{len(names) - len(shown)} more"
    return f"{type_desc}: {names_desc}"


def build_dir_rollup(conn: duckdb.DuckDBPyConnection, dir_path: str) -> str:
    """Structural summary of a directory (recursive): file/entity counts plus
    its highest fan-in entities -- the same "hot path" signal `project_brief`
    already uses, scoped to one subtree instead of the whole repo.

    `dir_path` of "" or "." means the repo root (every indexed file).
    """
    prefix = "" if dir_path in ("", ".") else f"{dir_path}/"
    like_pattern = f"{escape_like(prefix)}%" if prefix else "%"

    counts = conn.execute(
        "SELECT COUNT(DISTINCT f.path), COUNT(e.entity_id) "
        "FROM files f LEFT JOIN entities e ON e.file = f.path "
        "WHERE f.path LIKE ? ESCAPE '\\'",
        [like_pattern],
    ).fetchone()
    file_count, entity_count = (counts[0], counts[1]) if counts else (0, 0)
    if not file_count:
        return ""

    top_rows = conn.execute(
        """
        SELECT e.qualified_name, cnt.n
        FROM (
            SELECT dst_id, count(*) AS n
            FROM edges
            WHERE type = 'calls' AND dst_id NOT LIKE 'external:%'
            GROUP BY dst_id
        ) cnt
        JOIN entities e ON e.entity_id = cnt.dst_id
        WHERE e.file LIKE ? ESCAPE '\\'
        ORDER BY cnt.n DESC
        LIMIT ?
        """,
        [like_pattern, _DIR_ROLLUP_TOP_ENTITIES],
    ).fetchall()

    summary = f"{file_count} files, {entity_count} entities"
    if top_rows:
        top_desc = ", ".join(f"{name} ({n} callers)" for name, n in top_rows)
        summary += f". Most-used: {top_desc}"
    return summary


def top_level_dirs_with_hash(conn: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """Every top-level directory (same population `project_brief`'s top_dirs
    uses) paired with a content hash derived from its files' own hashes --
    changes exactly when a file is added, removed, or edited anywhere in that
    directory. Used to decide whether a cached `context_summaries` NL
    description of a directory is still fresh."""
    rows = conn.execute(
        "SELECT split_part(path, '/', 1) AS dir, string_agg(hash, '|' ORDER BY path) "
        "FROM files WHERE path LIKE '%/%' GROUP BY dir ORDER BY dir"
    ).fetchall()
    return [(d, hash_source(h or "")) for d, h in rows]
