# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Spring MVC route-annotation detection (`@GetMapping`, `@RequestMapping`, ...).

Like Flask/FastAPI, the annotation sits directly on the handler method, so
this is a same-file, no-cross-file-lookup detector (the handler entity_id is
already known at parse time). Unlike Flask/FastAPI, Spring controllers
commonly carry a class-level `@RequestMapping("/api")` base path that
combines with each method's own mapping to form the full route -- so this
needs the owning class's node too, not just the method's.
"""

from __future__ import annotations

from tree_sitter import Node

from codegraph.resolution.frameworks._paths import normalize_path
from codegraph.uir import Edge

_MAPPING_TO_METHOD = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
_REQUEST_METHOD_BY_ENUM = {
    "GET": "GET",
    "POST": "POST",
    "PUT": "PUT",
    "DELETE": "DELETE",
    "PATCH": "PATCH",
}
_ROUTE_EDGE_CONFIDENCE = 0.7


def extract_class_base_path(class_node: Node, source: bytes) -> str:
    """Return the class-level `@RequestMapping("/prefix")` base path, or ""."""
    modifiers = _modifiers_node(class_node)
    if modifiers is None:
        return ""
    for ann in _annotation_nodes(modifiers):
        if _annotation_name(ann, source) == "RequestMapping":
            return _annotation_path_value(ann, source) or ""
    return ""


def extract_route_edges(
    method_node: Node, handler_entity_id: str, source: bytes, base_path: str
) -> list[Edge]:
    """Return synthetic `calls` edges for any mapping annotation on `method_node`."""
    modifiers = _modifiers_node(method_node)
    if modifiers is None:
        return []

    edges: list[Edge] = []
    for ann in _annotation_nodes(modifiers):
        name = _annotation_name(ann, source)
        if name is None:
            continue
        if name in _MAPPING_TO_METHOD:
            methods = [_MAPPING_TO_METHOD[name]]
        elif name == "RequestMapping":
            methods = _request_mapping_methods(ann, source) or ["ANY"]
        else:
            continue

        sub_path = _annotation_path_value(ann, source) or ""
        full_path = _combine_paths(base_path, sub_path)
        line = ann.start_point[0] + 1
        for method in methods:
            edges.append(
                Edge(
                    src_id=f"route:{method} {full_path}",
                    dst_id=handler_entity_id,
                    type="calls",
                    line=line,
                    confidence=_ROUTE_EDGE_CONFIDENCE,
                    is_dynamic=True,
                )
            )
    return edges


def _modifiers_node(node: Node) -> Node | None:
    """`modifiers` isn't a named field in tree-sitter-java's grammar -- find
    it positionally among the node's own children."""
    for c in node.children:
        if c.type == "modifiers":
            return c
    return None


def _annotation_nodes(modifiers: Node):
    for c in modifiers.children:
        if c.type in ("annotation", "marker_annotation"):
            yield c


def _annotation_name(ann: Node, source: bytes) -> str | None:
    name = ann.child_by_field_name("name")
    return _text(name, source)


def _annotation_path_value(ann: Node, source: bytes) -> str | None:
    """The mapping path: a bare positional string (`@GetMapping("/x")`) or a
    `value = "/x"` element-value pair (`@RequestMapping(value = "/x")`)."""
    args = ann.child_by_field_name("arguments")
    if args is None:
        return None
    for c in args.children:
        if c.type == "string_literal":
            return _string_content(c, source)
        if c.type == "element_value_pair":
            key = c.child_by_field_name("key")
            if key is not None and _text(key, source) == "value":
                value = c.child_by_field_name("value")
                if value is not None and value.type == "string_literal":
                    return _string_content(value, source)
    return None


def _request_mapping_methods(ann: Node, source: bytes) -> list[str] | None:
    """`method = RequestMethod.POST` on `@RequestMapping`. Multiple methods
    via an array initializer aren't handled -- rare enough to skip cleanly."""
    args = ann.child_by_field_name("arguments")
    if args is None:
        return None
    for c in args.children:
        if c.type != "element_value_pair":
            continue
        key = c.child_by_field_name("key")
        if key is None or _text(key, source) != "method":
            continue
        value = c.child_by_field_name("value")
        if value is None or value.type != "field_access":
            continue
        field = value.child_by_field_name("field")
        field_name = _text(field, source)
        if field_name in _REQUEST_METHOD_BY_ENUM:
            return [_REQUEST_METHOD_BY_ENUM[field_name]]
    return None


def _combine_paths(base: str, sub: str) -> str:
    return normalize_path(f"{base}/{sub}")


def _text(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_content(string_node: Node, source: bytes) -> str | None:
    for c in string_node.children:
        if c.type == "string_fragment":
            return source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
    return None
