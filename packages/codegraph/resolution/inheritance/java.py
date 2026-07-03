# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Base-class/interface extraction for Java classes.

`class Foo extends Base implements IFoo, IBar { ... }` -- both `extends`
and `implements` are captured, unlike TypeScript: a Java interface can
declare a default method with a real body (Java 8+), so it's a legitimate
place to find an inherited method, not just a type constraint.
"""

from __future__ import annotations

from tree_sitter import Node


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def extract_base_classes(class_decl: Node, source: bytes) -> list[str]:
    """Base class/interface names from `extends`/`implements` clauses."""
    bases: list[str] = []

    superclass = class_decl.child_by_field_name("superclass")
    if superclass is not None:
        name_node = next((c for c in superclass.children if c.type == "type_identifier"), None)
        name = _text(name_node, source)
        if name:
            bases.append(name)

    interfaces = class_decl.child_by_field_name("interfaces")
    if interfaces is not None:
        type_list = next((c for c in interfaces.children if c.type == "type_list"), None)
        if type_list is not None:
            for child in type_list.children:
                if child.type == "type_identifier":
                    name = _text(child, source)
                    if name:
                        bases.append(name)

    return bases
