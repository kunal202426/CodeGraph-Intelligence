# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Rails routes DSL detection (`get '/users', to: 'users#index'`).

Rails routes live in `config/routes.rb`, referencing a controller action by
`"controller#action"` string -- the action method itself is defined in a
*different* file (`app/controllers/users_controller.rb`), which is the
overwhelmingly common real-world shape (unlike Express, where an inline or
same-file handler is routine). So this same-file pass will rarely find a
match in a real Rails app; it exists to extract the (method, path, action)
triple correctly and handle the rare same-file case (small apps, tests)
consistently with the other frameworks here. The cross-file match --
routes.rb's action name against every controller file's methods -- is the
one that actually matters for Rails, and belongs with Phase 21's cross-file
resolution pass rather than duplicated here.

`resources :users` (which expands to 7 conventional RESTful routes) isn't
expanded -- only explicit `get`/`post`/`put`/`patch`/`delete` calls with a
`to:` pair are recognized.
"""

from __future__ import annotations

from tree_sitter import Node

from codegraph.uir import Edge

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_ROUTE_EDGE_CONFIDENCE = 0.6


def extract_route_edges(root: Node, source: bytes, entities_by_name: dict[str, str]) -> list[Edge]:
    """Return synthetic `calls` edges for `get/post/put/patch/delete(path,
    to: "controller#action")` calls whose action resolves to a same-file
    entity."""
    edges: list[Edge] = []
    for call in _iter_call_nodes(root):
        info = _route_call_info(call, source)
        if info is None:
            continue
        method, path, action_name = info
        target = entities_by_name.get(action_name)
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


def _iter_call_nodes(node: Node):
    if node.type == "call":
        yield node
    for c in node.children:
        yield from _iter_call_nodes(c)


def _route_call_info(call: Node, source: bytes) -> tuple[str, str, str] | None:
    method_node = call.child_by_field_name("method")
    if method_node is None or method_node.type != "identifier":
        return None
    method_name = _text(method_node, source)
    if method_name not in _HTTP_METHODS:
        return None

    args = call.child_by_field_name("arguments")
    if args is None:
        return None

    path: str | None = None
    action_name: str | None = None
    for c in args.children:
        if c.type == "string" and path is None:
            path = _string_content(c, source)
        elif c.type == "pair":
            key = c.child_by_field_name("key")
            if key is None or _text(key, source) != "to":
                continue
            value = c.child_by_field_name("value")
            if value is None or value.type != "string":
                continue
            to_value = _string_content(value, source)
            if to_value and "#" in to_value:
                action_name = to_value.split("#", 1)[1]

    if path is None or action_name is None:
        return None
    return method_name.upper(), path, action_name


def _text(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_content(string_node: Node, source: bytes) -> str | None:
    for c in string_node.children:
        if c.type == "string_content":
            return source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
    return None
