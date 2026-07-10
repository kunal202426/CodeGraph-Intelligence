# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Heuristic code-smell detection.

Four cheap, graph/AST-derived heuristics — no LLM, no full type analysis.
All four run on whatever data is already in the DuckDB graph; no extra passes.

  * god-class        — a class with too many methods (low cohesion / does too much)
  * large-class      — a class spanning too many lines (harder to reason about)
  * high-coupling    — a module that imports from too many other places (high fan-out,
                       brittle to upstream changes)
  * complex-function — a function/method with high approximate cyclomatic complexity
                       (counted from decision keywords in raw_source — fast but noisy)

Each detector compares a measured metric against a configurable threshold and
emits a `Smell`. Results are ranked by *severity* = metric / threshold (how many
times over the limit it is), so the worst offenders sort first regardless of
which heuristic flagged them.

All thresholds are intentionally conservative — the defaults flag genuinely large
entities, not merely above-average ones. Tune via `detect_smells(...)` kwargs.

Public API
----------
detect_smells(conn, **threshold_kwargs) -> list[Smell]
    Run all four detectors and return smells ranked worst-first.
cyclomatic_complexity(source) -> int
    Standalone complexity estimate (useful for one-off checks outside the DB).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb

# Default thresholds (see module docstring). Tunable via `detect_smells(...)`.
DEFAULT_GOD_CLASS_METHODS = 15
DEFAULT_LARGE_CLASS_LOC = 500
DEFAULT_HIGH_COUPLING_IMPORTS = 20
DEFAULT_COMPLEX_FUNCTION = 15

# Decision-point keywords for an approximate cyclomatic complexity.
# Complexity = 1 + (number of these branch points in the body). Word-boundary
# keywords cover Python/Ruby-style branches; `&&`/`||` are matched separately
# since they're symbols, not words, so `\b` can never match them -- without
# this, every C-family language this tool supports (Java, C/C++, C#, Go,
# Rust, JS/TS, PHP) has its boolean-operator branches silently uncounted.
# `catch` covers those same languages' exception handling (Python's is
# `except`, already listed).
_DECISION_RE = re.compile(r"\b(?:if|elif|for|while|and|or|except|case|catch)\b|&&|\|\|")


@dataclass(frozen=True)
class Smell:
    """One flagged code smell."""

    kind: str  # "god-class" / "large-class" / "high-coupling" / "complex-function"
    name: str  # class / function / module name (file path for high-coupling)
    file: str
    line: int | None  # start line of the offending entity (None for module-level)
    metric: int  # the measured value (method count, LOC, fan-out, complexity)
    threshold: int  # the limit it exceeded
    entity_id: str | None  # the offending entity, when there is one

    @property
    def severity(self) -> float:
        """How many times over the threshold (>= 1.0 by construction)."""
        return self.metric / self.threshold if self.threshold else float(self.metric)

    @property
    def detail(self) -> str:
        unit = {
            "god-class": "methods",
            "large-class": "LOC",
            "high-coupling": "imports",
            "complex-function": "complexity",
        }[self.kind]
        return f"{self.metric} {unit} (threshold {self.threshold})"


def cyclomatic_complexity(source: str) -> int:
    """Approximate cyclomatic complexity from decision-keyword counts.

    Heuristic only: counts `if/elif/for/while/and/or/except/case/catch` tokens
    plus `&&`/`||` in the source text (comments/strings included — acceptable
    noise for a smell). Returns 1 for straight-line code.
    """
    return 1 + len(_DECISION_RE.findall(source))


def _god_classes(conn: duckdb.DuckDBPyConnection, threshold: int) -> list[Smell]:
    rows = conn.execute(
        """
        SELECT p.entity_id, p.name, p.file, p.start_line, COUNT(c.entity_id) AS methods
        FROM entities p
        JOIN entities c ON c.parent_id = p.entity_id
        WHERE p.type = 'class'
        GROUP BY p.entity_id, p.name, p.file, p.start_line
        HAVING COUNT(c.entity_id) > ?
        """,
        [threshold],
    ).fetchall()
    return [
        Smell(
            kind="god-class",
            name=name,
            file=file,
            line=start_line,
            metric=int(methods),
            threshold=threshold,
            entity_id=eid,
        )
        for eid, name, file, start_line, methods in rows
    ]


def _large_classes(conn: duckdb.DuckDBPyConnection, threshold: int) -> list[Smell]:
    rows = conn.execute(
        """
        SELECT entity_id, name, file, start_line, (end_line - start_line + 1) AS loc
        FROM entities
        WHERE type = 'class' AND (end_line - start_line + 1) > ?
        """,
        [threshold],
    ).fetchall()
    return [
        Smell(
            kind="large-class",
            name=name,
            file=file,
            line=start_line,
            metric=int(loc),
            threshold=threshold,
            entity_id=eid,
        )
        for eid, name, file, start_line, loc in rows
    ]


def _high_coupling(conn: duckdb.DuckDBPyConnection, threshold: int) -> list[Smell]:
    rows = conn.execute(
        """
        SELECT s.file, COUNT(DISTINCT e.dst_id) AS fanout
        FROM edges e
        JOIN entities s ON s.entity_id = e.src_id
        WHERE e.type = 'imports'
        GROUP BY s.file
        HAVING COUNT(DISTINCT e.dst_id) > ?
        """,
        [threshold],
    ).fetchall()
    return [
        Smell(
            kind="high-coupling",
            name=file,
            file=file,
            line=None,
            metric=int(fanout),
            threshold=threshold,
            entity_id=None,
        )
        for file, fanout in rows
    ]


def _complex_functions(conn: duckdb.DuckDBPyConnection, threshold: int) -> list[Smell]:
    rows = conn.execute(
        """
        SELECT entity_id, name, file, start_line, raw_source
        FROM entities
        WHERE type IN ('function', 'method') AND raw_source IS NOT NULL
        """
    ).fetchall()
    smells: list[Smell] = []
    for eid, name, file, start_line, raw in rows:
        complexity = cyclomatic_complexity(raw)
        if complexity > threshold:
            smells.append(
                Smell(
                    kind="complex-function",
                    name=name,
                    file=file,
                    line=start_line,
                    metric=complexity,
                    threshold=threshold,
                    entity_id=eid,
                )
            )
    return smells


def detect_smells(
    conn: duckdb.DuckDBPyConnection,
    *,
    god_class_methods: int = DEFAULT_GOD_CLASS_METHODS,
    large_class_loc: int = DEFAULT_LARGE_CLASS_LOC,
    high_coupling_imports: int = DEFAULT_HIGH_COUPLING_IMPORTS,
    complex_function: int = DEFAULT_COMPLEX_FUNCTION,
) -> list[Smell]:
    """Run all four heuristics and return smells ranked worst-first.

    Sort key: descending severity (metric / threshold), then file/line for
    stable, deterministic output among equally-severe smells.
    """
    smells: list[Smell] = []
    smells += _god_classes(conn, god_class_methods)
    smells += _large_classes(conn, large_class_loc)
    smells += _high_coupling(conn, high_coupling_imports)
    smells += _complex_functions(conn, complex_function)
    smells.sort(key=lambda s: (-s.severity, s.file, s.line or 0, s.kind))
    return smells
