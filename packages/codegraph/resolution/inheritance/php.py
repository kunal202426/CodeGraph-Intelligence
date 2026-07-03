# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Base-class extraction for PHP classes.

`class Foo extends Base { ... }` -- only `extends` is captured, not
`implements`: PHP interfaces (unlike Java's) can't declare a method body,
so recording one as a base wouldn't help resolve any call.
"""

from __future__ import annotations

from tree_sitter import Node


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def extract_base_classes(class_decl: Node, source: bytes) -> list[str]:
    """Base class name from `class Foo extends Base { ... }`, or `[]` for a
    class with no `extends` clause."""
    for child in class_decl.children:
        if child.type != "base_clause":
            continue
        name_node = next((c for c in child.children if c.type == "name"), None)
        name = _text(name_node, source)
        return [name] if name else []
    return []
