# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Flask/FastAPI route-decorator detection.

A function decorated `@app.get("/users")` (FastAPI, Flask 2+ shortcuts) or
`@app.route("/users", methods=[...])` (Flask) is invoked by the framework's
request router, not by any call site in the repo -- so without this, the
handler looks like dead code and `impact_analysis` reports zero callers.
This emits a synthetic `calls` edge from a `route:<METHOD> <path>`
pseudo-source (never a real entity -- existing edge queries already handle a
dangling `src_id` gracefully, same as unresolved `external:` targets) to the
handler's real entity_id, so the handler has a real inbound edge.

No type inference: matches purely on decorator *shape* (`x.get(...)`,
`x.post(...)`, `x.route(...)`) regardless of what `x` is bound to. A
coincidental unrelated `.get(...)`/`.post(...)` decorator would false-positive,
but that shape is rare outside routing frameworks -- the same
confidence/false-positive tradeoff already accepted for dynamic dict-dispatch
call resolution elsewhere in this codebase.
"""

from __future__ import annotations

from tree_sitter import Node

from codegraph.resolution.frameworks._paths import normalize_path
from codegraph.uir import Edge

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
_ROUTE_EDGE_CONFIDENCE = 0.7


def extract_route_edges(decorated_def: Node, handler_entity_id: str, source: bytes) -> list[Edge]:
    """Return synthetic `calls` edges for any route decorator on `decorated_def`.

    `decorated_def` is a `decorated_definition` node (decorator(s) + the
    function they decorate); `handler_entity_id` is the entity_id already
    assigned to that function.
    """
    edges: list[Edge] = []
    for child in decorated_def.children:
        if child.type != "decorator":
            continue
        call = _decorator_call(child)
        if call is None:
            continue
        method_name = _attribute_method_name(call, source)
        if method_name is None:
            continue

        path = _first_string_arg(call, source)
        if path is None:
            continue

        if method_name == "route":
            methods = _methods_kwarg(call, source) or ["GET"]
        elif method_name in _HTTP_METHODS:
            methods = [method_name.upper()]
        else:
            continue

        norm_path = normalize_path(path)
        line = child.start_point[0] + 1
        for method in methods:
            edges.append(
                Edge(
                    src_id=f"route:{method} {norm_path}",
                    dst_id=handler_entity_id,
                    type="calls",
                    line=line,
                    confidence=_ROUTE_EDGE_CONFIDENCE,
                    is_dynamic=True,
                )
            )
    return edges


def _decorator_call(decorator: Node) -> Node | None:
    """Return the `call` node inside a decorator, or None for a bare
    `@staticmethod`-style decorator with no call."""
    for c in decorator.children:
        if c.type == "call":
            return c
    return None


def _attribute_method_name(call: Node, source: bytes) -> str | None:
    """For `x.method(...)`, return `"method"`. None for a bare-name call."""
    func = call.child_by_field_name("function")
    if func is None or func.type != "attribute":
        return None
    attr = func.child_by_field_name("attribute")
    if attr is None:
        return None
    return source[attr.start_byte : attr.end_byte].decode("utf-8", errors="replace")


def _first_string_arg(call: Node, source: bytes) -> str | None:
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for a in args.children:
        if a.type == "string":
            return _string_content(a, source)
    return None


def _methods_kwarg(call: Node, source: bytes) -> list[str] | None:
    """Return the `methods=[...]` list for `@app.route(...)`, if present."""
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for a in args.children:
        if a.type != "keyword_argument":
            continue
        name = a.child_by_field_name("name")
        if name is None:
            continue
        if source[name.start_byte : name.end_byte].decode("utf-8", errors="replace") != "methods":
            continue
        value = a.child_by_field_name("value")
        if value is None or value.type != "list":
            continue
        methods = [
            _string_content(item, source).upper()
            for item in value.children
            if item.type == "string"
        ]
        return methods or None
    return None


def _string_content(string_node: Node, source: bytes) -> str:
    for c in string_node.children:
        if c.type == "string_content":
            return source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
    # Fallback for a grammar without a separate string_content child: strip quotes.
    raw = source[string_node.start_byte : string_node.end_byte].decode("utf-8", errors="replace")
    return raw.strip("'\"")
