"""Tests for analysis/traversal.py — find_shortest_path BFS."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.analysis.traversal import find_shortest_path
from codegraph.graph.store import GraphStore

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    """Minimal GraphStore with only the edges table seeded."""
    s = GraphStore(tmp_path / "g.duckdb")
    s.init_schema()
    yield s
    s.close()


def _edge(store: GraphStore, src: str, dst: str) -> None:
    """Insert a single 'calls' edge (no FK checks on edges table)."""
    store.conn.execute(
        "INSERT OR IGNORE INTO edges (src_id, dst_id, type, line) VALUES (?, ?, 'calls', 1)",
        [src, dst],
    )


# Short aliases so test graphs are compact.
A, B, C, D, E = "py:a.py:A", "py:b.py:B", "py:c.py:C", "py:d.py:D", "py:e.py:E"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_same_src_dst_returns_single_node(store: GraphStore) -> None:
    assert find_shortest_path(store.conn, A, A) == [A]


def test_direct_call_returns_two_node_path(store: GraphStore) -> None:
    _edge(store, A, B)
    assert find_shortest_path(store.conn, A, B) == [A, B]


def test_two_hop_path(store: GraphStore) -> None:
    _edge(store, A, B)
    _edge(store, B, C)
    assert find_shortest_path(store.conn, A, C) == [A, B, C]


def test_three_hop_path(store: GraphStore) -> None:
    _edge(store, A, B)
    _edge(store, B, C)
    _edge(store, C, D)
    assert find_shortest_path(store.conn, A, D) == [A, B, C, D]


def test_no_path_returns_none(store: GraphStore) -> None:
    _edge(store, A, B)
    # C is disconnected from A.
    assert find_shortest_path(store.conn, A, C) is None


def test_unreachable_when_edge_is_reversed(store: GraphStore) -> None:
    _edge(store, B, A)  # B calls A, not A calls B
    assert find_shortest_path(store.conn, A, B) is None


def test_max_hops_respected(store: GraphStore) -> None:
    # Chain A→B→C→D requires 3 hops; max_hops=2 should return None.
    _edge(store, A, B)
    _edge(store, B, C)
    _edge(store, C, D)
    assert find_shortest_path(store.conn, A, D, max_hops=2) is None


def test_cycle_safe(store: GraphStore) -> None:
    # A→B→A cycle; BFS must terminate and find A→B.
    _edge(store, A, B)
    _edge(store, B, A)
    assert find_shortest_path(store.conn, A, B) == [A, B]


def test_shortest_among_multiple_paths(store: GraphStore) -> None:
    # Long path: A→B→C.  Short path: A→C.  Should return [A, C].
    _edge(store, A, B)
    _edge(store, B, C)
    _edge(store, A, C)  # shortcut
    assert find_shortest_path(store.conn, A, C) == [A, C]


def test_external_edges_excluded_from_traversal(store: GraphStore) -> None:
    # A calls external:fmt, which calls B.  BFS must NOT traverse through external:.
    store.conn.execute(
        "INSERT OR IGNORE INTO edges (src_id, dst_id, type, line) VALUES (?, ?, 'calls', 1)",
        [A, "external:fmt"],
    )
    store.conn.execute(
        "INSERT OR IGNORE INTO edges (src_id, dst_id, type, line) VALUES (?, ?, 'calls', 1)",
        ["external:fmt", B],
    )
    # B is only reachable through external — should return None.
    assert find_shortest_path(store.conn, A, B) is None
