# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Base-class extraction for Ruby classes.

`class Foo < Base` -- Ruby modules (mixed in via `include`) also carry
methods, but detecting `include` reliably needs distinguishing it from any
other bare method call in a class body; the `< Base` superclass syntax is
unambiguous, so this covers that case only.
"""

from __future__ import annotations

from tree_sitter import Node


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def extract_base_classes(class_node: Node, source: bytes) -> list[str]:
    """Base class name from `class Foo < Base`, or `[]` for a class with no
    superclass clause."""
    superclass = class_node.child_by_field_name("superclass")
    if superclass is None:
        return []
    name_node = next((c for c in superclass.children if c.type == "constant"), None)
    name = _text(name_node, source)
    return [name] if name else []
