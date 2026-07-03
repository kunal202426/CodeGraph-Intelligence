# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Base-class extraction for C++ classes/structs.

`class Foo : public Base, private IFoo { ... }` -- the access specifier
(`public`/`private`/`protected`) doesn't change whether a base's methods
are worth walking to for call resolution, so every base in the clause is
captured regardless of it. C has no classes, so this module is C++-only.
"""

from __future__ import annotations

from tree_sitter import Node


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def extract_base_classes(class_node: Node, source: bytes) -> list[str]:
    """Base class names from a `: public Base, private IFoo` clause, or `[]`
    for a class/struct with no base-class clause."""
    for child in class_node.children:
        if child.type != "base_class_clause":
            continue
        return [_text(c, source) for c in child.children if c.type == "type_identifier"]
    return []
