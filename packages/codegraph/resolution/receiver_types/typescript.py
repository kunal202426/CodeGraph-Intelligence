# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for TypeScript/JavaScript method calls.

Same idea as `resolution/receiver_types/python.py`: infer the type of a call's
receiver from what's visible at parse time, so `obj.method()` can resolve to
the exact declared method instead of any same-named one. TS's `new X()` is an
unambiguous constructor-call marker (unlike Python, no capitalization
heuristic is needed), and a class field's own type annotation
(`private svc: Service;`) is an extra, more reliable source for `this.attr`
types on top of tracking `this.attr = new X()` assignments. Plain JS files
using this same parser just won't have type annotations to read -- the
`new X()` / `this.x = new X()` inference still applies unchanged.
"""

from __future__ import annotations

from tree_sitter import Node

# Node types that start a new scope for local-variable/`this` purposes --
# don't walk into a nested one when collecting the current scope's locals.
_NESTED_SCOPE_TYPES = frozenset(
    {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "class_body",
    }
)
_FIELD_DEFINITION_TYPES = frozenset({"public_field_definition", "field_definition"})


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _simple_type_from_annotation(type_annotation: Node | None, source: bytes) -> str | None:
    """`: Service` -> `Service`; `: Array<Service>` / `: Service | null` -> None
    (not a single concrete receiver type -- don't guess)."""
    if type_annotation is None:
        return None
    for child in type_annotation.children:
        if child.type == "type_identifier":
            return _text(child, source)
    return None


def _type_from_new_expression(value: Node | None, source: bytes) -> str | None:
    """`new Service(...)` -> `Service`. The `new` keyword makes this
    unambiguous, unlike Python's bare-call-plus-capitalization heuristic."""
    if value is None or value.type != "new_expression":
        return None
    ctor = value.child_by_field_name("constructor")
    if ctor is None or ctor.type != "identifier":
        return None
    name = _text(ctor, source)
    return name or None


def params_source_node(decl: Node) -> Node | None:
    """The node whose `parameters` field holds this declaration's parameter
    list -- itself for function_declaration/method_definition, or the arrow
    function value for a `const f = (...) => ...` declarator."""
    if decl.type == "variable_declarator":
        value = decl.child_by_field_name("value")
        return value if value is not None and value.type == "arrow_function" else None
    return decl


def infer_param_types(params_node: Node | None, source: bytes) -> dict[str, str]:
    """Typed parameters: `function f(svc: Service, other) { ... }`."""
    if params_node is None:
        return {}
    params = params_node.child_by_field_name("parameters")
    if params is None or params.type != "formal_parameters":
        return {}
    result: dict[str, str] = {}
    for child in params.children:
        if child.type not in ("required_parameter", "optional_parameter"):
            continue
        pattern = child.child_by_field_name("pattern")
        if pattern is None or pattern.type != "identifier":
            continue
        type_name = _simple_type_from_annotation(child.child_by_field_name("type"), source)
        name = _text(pattern, source)
        if name and type_name:
            result[name] = type_name
    return result


def _walk_declarators(node: Node, on_declarator) -> None:
    """Recurse into control-flow blocks but not into a nested function/class --
    those get their own scope."""
    for child in node.children:
        if child.type == "variable_declarator":
            on_declarator(child)
        if child.type in _NESTED_SCOPE_TYPES:
            continue
        _walk_declarators(child, on_declarator)


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables: `const lg: Logger = ...` or `let lg = new Logger()`.
    Last declaration wins on re-use of a name."""
    result: dict[str, str] = {}

    def visit(declarator: Node) -> None:
        name_node = declarator.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            return
        name = _text(name_node, source)
        if not name:
            return
        type_name = _simple_type_from_annotation(declarator.child_by_field_name("type"), source)
        if type_name is None:
            type_name = _type_from_new_expression(declarator.child_by_field_name("value"), source)
        if type_name:
            result[name] = type_name

    _walk_declarators(body, visit)
    return result


def infer_self_attr_types(class_body: Node, source: bytes) -> dict[str, str]:
    """`this.attr`'s type, from a class field's own type annotation
    (`private svc: Service;`) or a `this.attr = new Service()` assignment
    anywhere in the class -- so a constructor-assigned field resolves from
    every other method, not just the one that assigned it."""
    result: dict[str, str] = {}

    for child in class_body.children:
        if child.type in _FIELD_DEFINITION_TYPES:
            name = _text(child.child_by_field_name("name"), source)
            type_name = _simple_type_from_annotation(child.child_by_field_name("type"), source)
            if name and type_name:
                result[name] = type_name

    def visit_assignment(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "member_expression":
            return
        obj = left.child_by_field_name("object")
        prop = left.child_by_field_name("property")
        if obj is None or obj.type != "this" or prop is None:
            return
        attr_name = _text(prop, source)
        type_name = _type_from_new_expression(assignment.child_by_field_name("right"), source)
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
        if child.type == "method_definition":
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
    """The inferred type of a call's receiver -- `obj` in `obj.method()` -- or
    None when it's not a simple `x.m()`/`this.x.m()` shape, or the type wasn't
    tracked."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    obj = fn.child_by_field_name("object")
    if obj is None:
        return None
    if obj.type == "this":
        return class_name
    if obj.type == "identifier":
        return local_types.get(_text(obj, source))
    if obj.type == "member_expression":
        obj_obj = obj.child_by_field_name("object")
        obj_prop = obj.child_by_field_name("property")
        if obj_obj is None or obj_obj.type != "this" or obj_prop is None:
            return None
        return self_attr_types.get(_text(obj_prop, source))
    return None
