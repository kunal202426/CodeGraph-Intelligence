# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Embedded-struct extraction for Go -- Go's closest equivalent to inheritance.

Go has no `extends`/superclass syntax. Instead, a struct field declared with
no name -- just a type (`type Derived struct { Base }`) -- is an *embedded*
field, and Go promotes the embedded type's methods onto the containing
struct: `d.Save()` works directly if `Base` declares `Save()`, with no
`d.Base.Save()` needed. That's exactly the shape method-call resolution's
inheritance walk was built for, so an embedded field is treated as a base
class the same way `extends`/`< Base`/`base_class_clause` are in the other
languages.
"""

from __future__ import annotations

from tree_sitter import Node


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _embedded_type_name(field_decl: Node, source: bytes) -> str | None:
    """A `field_declaration` with no `name` field is embedded -- its `type`
    field is the promoted type. `*Other` (a pointer-embedded field) unwraps
    the same way a pointer field/parameter type already does elsewhere."""
    if field_decl.child_by_field_name("name") is not None:
        return None
    type_node = field_decl.child_by_field_name("type")
    if type_node is None:
        return None
    if type_node.type == "type_identifier":
        return _text(type_node, source) or None
    if type_node.type == "pointer_type":
        inner = next((c for c in type_node.children if c.type == "type_identifier"), None)
        return _text(inner, source) or None
    return None


def extract_embedded_types(struct_type: Node, source: bytes) -> list[str]:
    """Embedded field type names from a `struct_type` node's
    `field_declaration_list`, or `[]` if it has none."""
    field_list = next((c for c in struct_type.children if c.type == "field_declaration_list"), None)
    if field_list is None:
        return []
    names: list[str] = []
    for child in field_list.children:
        if child.type != "field_declaration":
            continue
        name = _embedded_type_name(child, source)
        if name:
            names.append(name)
    return names
