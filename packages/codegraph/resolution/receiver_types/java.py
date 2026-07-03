# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for Java method calls.

Same idea as the Python/TypeScript modules: infer a call's receiver type from
what's visible at parse time, so `obj.method()` resolves to the exact
declared method instead of any same-named one. Java's grammar makes this
easier than either -- `new X()` is unambiguous like TS, and a
`method_invocation` node carries its receiver directly in an `object` field
(no attribute-expression unwrapping needed).
"""

from __future__ import annotations

from tree_sitter import Node

_NESTED_SCOPE_TYPES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "class_declaration",
        "class_body",
        "lambda_expression",
        "interface_declaration",
    }
)


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _simple_type(type_node: Node | None, source: bytes) -> str | None:
    """`Service` -> `Service`; `var`, a generic (`List<Service>`), an array, or
    a primitive isn't a single concrete receiver type -- don't guess."""
    if type_node is None or type_node.type != "type_identifier":
        return None
    name = _text(type_node, source)
    return name if name and name != "var" else None


def _type_from_object_creation(value: Node | None, source: bytes) -> str | None:
    """`new Service(...)` -> `Service`."""
    if value is None or value.type != "object_creation_expression":
        return None
    return _simple_type(value.child_by_field_name("type"), source)


def infer_param_types(method_node: Node, source: bytes) -> dict[str, str]:
    """Typed parameters: `Foo(Logger logger, int other) { ... }`."""
    params = method_node.child_by_field_name("parameters")
    if params is None:
        return {}
    result: dict[str, str] = {}
    for child in params.children:
        if child.type != "formal_parameter":
            continue
        name = _text(child.child_by_field_name("name"), source)
        type_name = _simple_type(child.child_by_field_name("type"), source)
        if name and type_name:
            result[name] = type_name
    return result


def _walk_local_declarations(node: Node, on_declaration) -> None:
    for child in node.children:
        if child.type == "local_variable_declaration":
            on_declaration(child)
        if child.type in _NESTED_SCOPE_TYPES:
            continue
        _walk_local_declarations(child, on_declaration)


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables: `Logger lg = ...;` or `var lg = new Logger();`."""
    result: dict[str, str] = {}

    def visit(decl: Node) -> None:
        declared_type = _simple_type(decl.child_by_field_name("type"), source)
        for declarator in decl.children_by_field_name("declarator"):
            name = _text(declarator.child_by_field_name("name"), source)
            if not name:
                continue
            type_name = declared_type or _type_from_object_creation(
                declarator.child_by_field_name("value"), source
            )
            if type_name:
                result[name] = type_name

    _walk_local_declarations(body, visit)
    return result


def infer_self_attr_types(class_body: Node, source: bytes) -> dict[str, str]:
    """`this.attr`'s type, from a field's own declared type or a
    `this.attr = new Service();` assignment anywhere in the class."""
    result: dict[str, str] = {}

    for child in class_body.children:
        if child.type == "field_declaration":
            declared_type = _simple_type(child.child_by_field_name("type"), source)
            if declared_type is None:
                continue
            for declarator in child.children_by_field_name("declarator"):
                name = _text(declarator.child_by_field_name("name"), source)
                if name:
                    result[name] = declared_type

    def visit_assignment(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "field_access":
            return
        obj = left.child_by_field_name("object")
        field = left.child_by_field_name("field")
        if obj is None or obj.type != "this" or field is None:
            return
        attr_name = _text(field, source)
        type_name = _type_from_object_creation(assignment.child_by_field_name("right"), source)
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
        if child.type in ("method_declaration", "constructor_declaration"):
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
    """The inferred type of a `method_invocation`'s receiver, or None when
    it's implicit (no `object` field -- an unqualified call), not a simple
    `x.m()`/`this.x.m()` shape, or the type wasn't tracked."""
    obj = call.child_by_field_name("object")
    if obj is None:
        return None
    if obj.type == "this":
        return class_name
    if obj.type == "identifier":
        return local_types.get(_text(obj, source))
    if obj.type == "field_access":
        obj_obj = obj.child_by_field_name("object")
        obj_field = obj.child_by_field_name("field")
        if obj_obj is None or obj_obj.type != "this" or obj_field is None:
            return None
        return self_attr_types.get(_text(obj_field, source))
    return None
