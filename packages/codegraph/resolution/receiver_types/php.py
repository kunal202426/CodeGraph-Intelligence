# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for PHP method calls.

Same idea as the Python/TypeScript/Java modules: infer a call's receiver type
from what's visible at parse time. `$this` works like `self`, and `new X()`
is unambiguous like TS's. A typed property (`private Service $svc;`, PHP
7.4+) is tracked as a receiver type on top of `$this->svc = new Service()`
assignments, same as TS's field-declaration source.
"""

from __future__ import annotations

from tree_sitter import Node

_NESTED_SCOPE_TYPES = frozenset(
    {
        "function_definition",
        "anonymous_function_creation_expression",
        "arrow_function",
        "method_declaration",
        "class_declaration",
    }
)


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _simple_type(type_node: Node | None, source: bytes) -> str | None:
    """`Service` -> `Service`; `Service|null` (a real union, 2+ named types) or
    an array/generic type isn't a single concrete receiver type -- don't
    guess."""
    if type_node is None:
        return None
    if type_node.type == "union_type":
        named = [c for c in type_node.children if c.type == "named_type"]
        if len(named) != 1:
            return None
        return _simple_type(named[0], source)
    if type_node.type == "named_type":
        name_node = next((c for c in type_node.children if c.type == "name"), None)
        return _text(name_node, source) if name_node else None
    if type_node.type == "name":
        return _text(type_node, source)
    return None


def _type_from_value_expr(value: Node | None, source: bytes) -> str | None:
    """`new Service(...)` -> `Service`; `new App\\Service(...)` -> `Service`
    (last segment of a qualified name)."""
    if value is None or value.type != "object_creation_expression":
        return None
    name_node = next((c for c in value.children if c.type in ("name", "qualified_name")), None)
    if name_node is None:
        return None
    text = _text(name_node, source)
    return text.rsplit("\\", 1)[-1] if text else None


def _var_name(node: Node | None, source: bytes) -> str | None:
    """`variable_name`'s bare name field, e.g. `lg` from `$lg`."""
    if node is None or node.type != "variable_name":
        return None
    name_node = next((c for c in node.children if c.type == "name"), None)
    return _text(name_node, source) or None


def infer_param_types(params: Node | None, source: bytes) -> dict[str, str]:
    """Typed parameters: `function f(Service $svc, $other) { ... }`."""
    if params is None:
        return {}
    result: dict[str, str] = {}
    for child in params.children:
        if child.type != "simple_parameter":
            continue
        name = _var_name(child.child_by_field_name("name"), source)
        type_name = _simple_type(child.child_by_field_name("type"), source)
        if name and type_name:
            result[name] = type_name
    return result


def _walk_assignments(node: Node, on_assignment) -> None:
    for child in node.children:
        if child.type == "assignment_expression":
            on_assignment(child)
        if child.type in _NESTED_SCOPE_TYPES:
            continue
        _walk_assignments(child, on_assignment)


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables: `$lg = new Logger();`."""
    result: dict[str, str] = {}

    def visit(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        name = _var_name(left, source)
        if not name:
            return
        type_name = _type_from_value_expr(assignment.child_by_field_name("right"), source)
        if type_name:
            result[name] = type_name

    _walk_assignments(body, visit)
    return result


def infer_self_attr_types(class_body: Node, source: bytes) -> dict[str, str]:
    """`$this->attr`'s type, from a typed property declaration or a
    `$this->attr = new Service();` assignment anywhere in the class."""
    result: dict[str, str] = {}

    for child in class_body.children:
        if child.type == "property_declaration":
            type_name = _simple_type(child.child_by_field_name("type"), source)
            if type_name is None:
                continue
            for elem in child.children:
                if elem.type != "property_element":
                    continue
                var_node = next((c for c in elem.children if c.type == "variable_name"), None)
                name = _var_name(var_node, source)
                if name:
                    result[name] = type_name

    def visit_assignment(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "member_access_expression":
            return
        obj = left.child_by_field_name("object")
        name_field = left.child_by_field_name("name")
        if obj is None or _var_name(obj, source) != "this" or name_field is None:
            return
        attr_name = _text(name_field, source)
        type_name = _type_from_value_expr(assignment.child_by_field_name("right"), source)
        if attr_name and type_name:
            result[attr_name] = type_name

    def walk_for_assignments(node: Node) -> None:
        for child in node.children:
            if child.type == "assignment_expression":
                visit_assignment(child)
            if child.type in _NESTED_SCOPE_TYPES:
                continue
            walk_for_assignments(child)

    for child in class_body.children:
        if child.type == "method_declaration":
            body = child.child_by_field_name("body")
            if body is not None:
                walk_for_assignments(body)

    return result


def receiver_type_for_call(
    call: Node,
    source: bytes,
    class_name: str | None,
    local_types: dict[str, str],
    self_attr_types: dict[str, str],
) -> str | None:
    """The inferred type of a `member_call_expression`'s receiver, or None
    when it's not a simple `$x->m()`/`$this->x->m()` shape, or the type
    wasn't tracked."""
    if call.type != "member_call_expression":
        return None
    obj = call.child_by_field_name("object")
    if obj is None:
        return None
    if obj.type == "variable_name":
        var_name = _var_name(obj, source)
        if var_name == "this":
            return class_name
        return local_types.get(var_name) if var_name else None
    if obj.type == "member_access_expression":
        obj_obj = obj.child_by_field_name("object")
        obj_name = obj.child_by_field_name("name")
        if obj_obj is None or _var_name(obj_obj, source) != "this" or obj_name is None:
            return None
        return self_attr_types.get(_text(obj_name, source))
    return None
