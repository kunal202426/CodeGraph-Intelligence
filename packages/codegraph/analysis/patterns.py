# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Layered-architecture analysis.

Classifies each top-level directory into an architectural layer by name, then
inspects the file-level import graph to summarize cross-layer flow and flag
*layering violations* — a lower layer importing from a higher one (e.g. a data
model importing a web route), which inverts the intended dependency direction.

Layer ranks (high → low): presentation (0) < service (1) < data (2). Healthy
imports flow downward (presentation → service → data, src_rank < dst_rank); an
edge with src_rank > dst_rank is a violation. Directories that match no layer
keyword are "other" and excluded from violation checks.

Heuristic only — based on directory naming conventions, not provable structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

_LAYER_KEYWORDS: dict[str, frozenset[str]] = {
    "presentation": frozenset(
        {
            "api",
            "apis",
            "routes",
            "route",
            "controllers",
            "controller",
            "views",
            "view",
            "handlers",
            "handler",
            "web",
            "ui",
            "endpoints",
            "endpoint",
            "rest",
            "graphql",
        }
    ),
    "service": frozenset(
        {
            "services",
            "service",
            "logic",
            "core",
            "domain",
            "usecases",
            "usecase",
            "managers",
            "manager",
            "business",
        }
    ),
    "data": frozenset(
        {
            "models",
            "model",
            "db",
            "database",
            "repositories",
            "repository",
            "dao",
            "store",
            "stores",
            "schema",
            "schemas",
            "entities",
            "entity",
            "orm",
            "migrations",
        }
    ),
}
_LAYER_RANK = {"presentation": 0, "service": 1, "data": 2}


def classify_layer(top_dir: str) -> str:
    """Map a top-level directory name to a layer, or 'other' if it matches none."""
    d = top_dir.lower().strip("/")
    for layer, keywords in _LAYER_KEYWORDS.items():
        if d in keywords:
            return layer
    return "other"


def _top_dir(file: str) -> str:
    head, sep, _ = file.partition("/")
    return head if sep else "."


@dataclass
class LayerViolation:
    src_file: str
    dst_file: str
    src_layer: str
    dst_layer: str


@dataclass
class LayerReport:
    layers_present: dict[str, list[str]] = field(default_factory=dict)  # layer -> sorted dirs
    flows: dict[tuple[str, str], int] = field(default_factory=dict)  # (src,dst layer) -> count
    violations: list[LayerViolation] = field(default_factory=list)


def analyze_layers(conn: duckdb.DuckDBPyConnection) -> LayerReport:
    """Classify directories into layers and analyze cross-layer import flow."""
    # Which layers exist, and which dirs map to each.
    dir_rows = conn.execute("SELECT DISTINCT file FROM entities").fetchall()
    layers_present: dict[str, set[str]] = {}
    for (file,) in dir_rows:
        d = _top_dir(file)
        layers_present.setdefault(classify_layer(d), set()).add(d)

    # Cross-file import edges (same shape as the cycle detector).
    edge_rows = conn.execute(
        """
        SELECT DISTINCT s.file AS src_file, d.file AS dst_file
        FROM edges e
        JOIN entities s ON s.entity_id = e.src_id
        JOIN entities d ON d.entity_id = e.dst_id
        WHERE e.type = 'imports' AND s.file <> d.file
        """
    ).fetchall()

    flows: dict[tuple[str, str], int] = {}
    violations: list[LayerViolation] = []
    for src_file, dst_file in edge_rows:
        src_layer = classify_layer(_top_dir(src_file))
        dst_layer = classify_layer(_top_dir(dst_file))
        if src_layer == dst_layer:
            continue
        flows[(src_layer, dst_layer)] = flows.get((src_layer, dst_layer), 0) + 1
        src_rank = _LAYER_RANK.get(src_layer)
        dst_rank = _LAYER_RANK.get(dst_layer)
        # Both ranked, and a lower layer imports a higher one → violation.
        if src_rank is not None and dst_rank is not None and src_rank > dst_rank:
            violations.append(
                LayerViolation(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_layer=src_layer,
                    dst_layer=dst_layer,
                )
            )

    return LayerReport(
        layers_present={k: sorted(v) for k, v in sorted(layers_present.items())},
        flows=flows,
        violations=sorted(violations, key=lambda v: (v.src_file, v.dst_file)),
    )
