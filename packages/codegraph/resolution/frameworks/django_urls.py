# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Django URLconf detection (`path("route/", view)` / `re_path(...)`).

Django registers a view through a plain function call inside a `urlpatterns`
list -- there's no decorator on the view itself (unlike Flask/FastAPI) and no
inline handler argument shape to match against a single file the way Express
does (unlike Express, real Django apps near-universally split `urls.py` from
`views.py`). This still resolves same-file references (a urls.py that also
defines its own view functions, common in small apps/tests) and extracts the
(path, view_name) pair either way; the cross-file case -- the far more common
one in real Django projects -- needs a repo-wide symbol table, which is
exactly what Phase 21's cross-file resolution pass builds for the HTTP-edge
work, so that pass is the natural place to close this gap for cross-file
matches too.

No HTTP method is embedded in the synthetic edge's route label: Django's
URLconf doesn't discriminate by verb (a view function branches on
`request.method` itself), so callers see `route:ANY <path>` rather than a
specific method.
"""

from __future__ import annotations

from tree_sitter import Node

from codegraph.uir import Edge

_PATH_FUNCS = {"path", "re_path", "url"}
_ROUTE_EDGE_CONFIDENCE = 0.6


def extract_route_edges(root: Node, source: bytes, entities_by_name: dict[str, str]) -> list[Edge]:
    """Return synthetic `calls` edges for `path()`/`re_path()`/`url()` calls
    in `root` whose view argument resolves to a same-file entity."""
    edges: list[Edge] = []
    for call in _iter_call_expressions(root):
        info = _path_call_info(call, source)
        if info is None:
            continue
        path, view_name = info
        target = entities_by_name.get(view_name)
        if target is None:
            continue
        edges.append(
            Edge(
                src_id=f"route:ANY {path}",
                dst_id=target,
                type="calls",
                line=call.start_point[0] + 1,
                confidence=_ROUTE_EDGE_CONFIDENCE,
                is_dynamic=True,
            )
        )
    return edges


def _iter_call_expressions(node: Node):
    if node.type == "call":
        yield node
    for c in node.children:
        yield from _iter_call_expressions(c)


def _path_call_info(call: Node, source: bytes) -> tuple[str, str] | None:
    """Return (path, view_name) for `path("x/", view, ...)`, or None."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "identifier":
        return None
    if _text(fn, source) not in _PATH_FUNCS:
        return None

    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    positional = [c for c in args.children if c.is_named and c.type != "keyword_argument"]
    if len(positional) < 2:
        return None

    path_node = positional[0]
    if path_node.type != "string":
        return None
    path = _string_content(path_node, source)
    if path is None:
        return None

    view_name = _view_name(positional[1], source)
    if view_name is None:
        return None

    return path, view_name


def _view_name(node: Node, source: bytes) -> str | None:
    """Extract a usable name from a view argument.

    `view_func` -> "view_func"; `views.view_func` -> "view_func" (last
    segment); `SomeView.as_view()` -> "SomeView" (the class, since there's no
    single dispatch-method entity to point at instead).
    """
    if node.type == "identifier":
        return _text(node, source)
    if node.type == "attribute":
        attr = node.child_by_field_name("attribute")
        return _text(attr, source) if attr is not None else None
    if node.type == "call":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "attribute":
            obj = fn.child_by_field_name("object")
            if obj is not None and obj.type == "identifier":
                return _text(obj, source)
    return None


def _text(node: Node, source: bytes) -> str | None:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_content(string_node: Node, source: bytes) -> str | None:
    for c in string_node.children:
        if c.type == "string_content":
            return source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
    return None
