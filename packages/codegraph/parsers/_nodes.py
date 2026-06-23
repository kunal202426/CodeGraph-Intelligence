# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""Small tree-sitter node helpers shared across the language parsers."""

from __future__ import annotations

import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    from tree_sitter import Node


def first_child(node: Node, kind: str) -> Node | None:
    """Return the first direct child of *node* whose type is *kind*, or None."""
    return next((c for c in node.children if c.type == kind), None)


def node_text(node: Node | None, source: bytes) -> str | None:
    """Decode the source span covered by *node* (None-safe)."""
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
