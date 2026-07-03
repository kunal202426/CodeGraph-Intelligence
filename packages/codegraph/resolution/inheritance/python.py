# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Base-class extraction for Python classes.

`class Foo(Base, Mixin):` -- the parser emits one provisional
`py:?inherits:<BaseName>` edge per base so the resolver can later walk
`Type.method` up to a base class when `method` isn't declared directly on
`Type`. `metaclass=...` and other keyword arguments in the base-class list
aren't base classes and are skipped; a dotted base (`module.Base`) is
recorded by its last segment, matching how the rest of this project
resolves module-qualified names.
"""

from __future__ import annotations

from tree_sitter import Node


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def extract_base_classes(class_def: Node, source: bytes) -> list[str]:
    """Base class names from `class Foo(Base, Mixin, metaclass=Meta):`."""
    superclasses = class_def.child_by_field_name("superclasses")
    if superclasses is None:
        return []
    bases: list[str] = []
    for child in superclasses.children:
        if child.type == "identifier":
            bases.append(_text(child, source))
        elif child.type == "attribute":
            attr = child.child_by_field_name("attribute")
            name = _text(attr, source)
            if name:
                bases.append(name)
    return bases
