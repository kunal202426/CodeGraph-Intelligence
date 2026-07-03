# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Express route-registration detection (`app.get('/path', handler)`).

Unlike Flask/FastAPI's decorator, Express registers a handler via a plain
function call whose *argument* references the handler -- there is no
decorator sitting directly on the handler's own definition. So this walks
the whole file for `x.method(path, ..., handler)` call shapes and resolves
`handler` against the entities already extracted for *this same file* (a
same-file lookup, done once the whole file's entities are known -- matching
the parser's single-pass, per-file scope).

Only resolves a same-file identifier reference. A handler imported from
another module (`app.get('/x', controllers.getUsers)`, or `app.get('/x',
importedHandler)`) is not (yet) resolved here -- cross-file resolution is a
natural extension alongside the cross-language HTTP-edge work, not this pass.
An inline handler (`app.get('/x', (req, res) => {...})`) has no separate
entity to link to either way, so it's skipped rather than mis-parsed.
"""

from __future__ import annotations

from tree_sitter import Node

from codegraph.uir import Edge

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "all"}
_ROUTE_EDGE_CONFIDENCE = 0.7


def extract_route_edges(root: Node, source: bytes, entities_by_name: dict[str, str]) -> list[Edge]:
    """Return synthetic `calls` edges for Express-style route registrations
    in `root` whose handler argument resolves to a same-file entity."""
    edges: list[Edge] = []
    for call in _iter_call_expressions(root):
        info = _route_call_info(call, source)
        if info is None:
            continue
        method, path, handler_name = info
        target = entities_by_name.get(handler_name)
        if target is None:
            continue
        edges.append(
            Edge(
                src_id=f"route:{method} {path}",
                dst_id=target,
                type="calls",
                line=call.start_point[0] + 1,
                confidence=_ROUTE_EDGE_CONFIDENCE,
                is_dynamic=True,
            )
        )
    return edges


def _iter_call_expressions(node: Node):
    """Yield every `call_expression` anywhere under `node` (module top-level
    included, unlike the parser's own call-edge pass which only scans
    function bodies -- route registration is typically top-level)."""
    if node.type == "call_expression":
        yield node
    for c in node.children:
        yield from _iter_call_expressions(c)


def _route_call_info(call: Node, source: bytes) -> tuple[str, str, str] | None:
    """Return (METHOD, path, handler_identifier_name) for a route-shaped
    call, or None if `call` doesn't match `x.method(path, ..., handler)`."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    prop = fn.child_by_field_name("property")
    if prop is None:
        return None
    method_name = _text(prop, source)
    if method_name not in _HTTP_METHODS:
        return None

    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    arg_nodes = [c for c in args.children if c.is_named]
    if len(arg_nodes) < 2:
        return None

    path_node = arg_nodes[0]
    if path_node.type != "string":
        return None
    path = _string_content(path_node, source)
    if path is None:
        return None

    handler_node = arg_nodes[-1]
    if handler_node.type != "identifier":
        return None  # inline arrow/anonymous handler -- nothing to link to
    handler_name = _text(handler_node, source)
    if handler_name is None:
        return None

    return method_name.upper(), path, handler_name


def _text(node: Node, source: bytes) -> str | None:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_content(string_node: Node, source: bytes) -> str | None:
    for c in string_node.children:
        if c.type == "string_fragment":
            return source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
    return None
