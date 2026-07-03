# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Frontend HTTP call detection (`fetch(...)`, `axios.get(...)`).

The other half of the cross-language HTTP edge: a TS/JS call site that hits a
URL registered as a route by one of the backend framework resolvers (Flask,
FastAPI, Express, Django, Spring, Rails -- all of which emit a
`route:<METHOD> <path>` edge source, normalized the same way via
`_paths.normalize_path`). This extracts (method, path) from the call site and
emits a provisional `route:?http:<METHOD>:<path>` edge that the cross-language
resolution pass in resolver.py matches against those route sources -- turning
"frontend calls this URL" and "backend handles this URL" into one edge,
frontend call site straight through to the backend handler, regardless of
language.

Only a statically-known URL is usable: a plain string or a template literal
with no `${...}` interpolation. A dynamic URL (`` `/api/users/${id}` ``,
`` fetch(apiBase + "/users") ``) can't be matched against a fixed route
string, so it's skipped rather than guessed at.
"""

from __future__ import annotations

from tree_sitter import Node

from codegraph.resolution.frameworks._paths import normalize_path
from codegraph.uir import Edge, EntityType, UIREntity

_AXIOS_METHODS = {"get", "post", "put", "delete", "patch"}
_ROUTE_EDGE_CONFIDENCE = 0.6


def extract_http_edges(
    root: Node, source: bytes, entities: list[UIREntity], module_id: str
) -> list[Edge]:
    """Return provisional `route:?http:METHOD:path` edges for `fetch`/`axios`
    call sites with a statically-known URL, attributed to the smallest
    function/method entity containing the call site (module-level if none)."""
    ranges = sorted(
        (
            (e.start_line, e.end_line, e.entity_id)
            for e in entities
            if e.type in (EntityType.FUNCTION, EntityType.METHOD)
        ),
        key=lambda t: t[1] - t[0],
    )

    edges: list[Edge] = []
    for call in _iter_call_expressions(root):
        info = _http_call_info(call, source)
        if info is None:
            continue
        method, path = info
        line = call.start_point[0] + 1
        src_id = _containing_entity(line, ranges) or module_id
        edges.append(
            Edge(
                src_id=src_id,
                dst_id=f"route:?http:{method}:{normalize_path(path)}",
                type="calls",
                line=line,
                confidence=_ROUTE_EDGE_CONFIDENCE,
                is_dynamic=True,
            )
        )
    return edges


def _containing_entity(line: int, ranges: list[tuple[int, int, str]]) -> str | None:
    for start, end, entity_id in ranges:
        if start <= line <= end:
            return entity_id
    return None


def _iter_call_expressions(node: Node):
    if node.type == "call_expression":
        yield node
    for c in node.children:
        yield from _iter_call_expressions(c)


def _http_call_info(call: Node, source: bytes) -> tuple[str, str] | None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return None

    if fn.type == "identifier" and _text(fn, source) == "fetch":
        return _fetch_info(call, source)

    if fn.type == "member_expression":
        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        if obj is not None and prop is not None and _text(obj, source) == "axios":
            method_name = _text(prop, source)
            if method_name in _AXIOS_METHODS:
                return _axios_info(call, method_name, source)
    return None


def _fetch_info(call: Node, source: bytes) -> tuple[str, str] | None:
    args = _positional_args(call)
    if not args:
        return None
    url = _url_from_node(args[0], source)
    if url is None:
        return None
    method = "GET"
    if len(args) > 1 and args[1].type == "object":
        found = _method_from_options(args[1], source)
        if found is not None:
            method = found
    return method, url


def _axios_info(call: Node, method_name: str, source: bytes) -> tuple[str, str] | None:
    args = _positional_args(call)
    if not args:
        return None
    url = _url_from_node(args[0], source)
    if url is None:
        return None
    return method_name.upper(), url


def _positional_args(call: Node) -> list[Node]:
    args = call.child_by_field_name("arguments")
    if args is None:
        return []
    return [c for c in args.children if c.is_named]


def _url_from_node(node: Node, source: bytes) -> str | None:
    if node.type == "string":
        return _string_content(node, source)
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.children):
            return None  # dynamic URL, can't match a fixed route string
        raw = _text(node, source) or ""
        return raw.strip("`")
    return None


def _method_from_options(obj_node: Node, source: bytes) -> str | None:
    for pair in obj_node.children:
        if pair.type != "pair":
            continue
        key = pair.child_by_field_name("key")
        if key is None:
            continue
        key_text = _text(key, source)
        if key_text is None or key_text.strip("'\"") != "method":
            continue
        value = pair.child_by_field_name("value")
        if value is not None and value.type == "string":
            content = _string_content(value, source)
            return content.upper() if content else None
    return None


def _text(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_content(string_node: Node, source: bytes) -> str | None:
    for c in string_node.children:
        if c.type == "string_fragment":
            return source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
    return None
