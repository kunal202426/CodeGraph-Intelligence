# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for Python method calls.

`obj.method()` is only resolvable to the right `method` if we know `obj`'s
type. This module infers that type from what's visible at parse time --
a local variable's constructor call or type annotation, a typed parameter,
or a `self.attr` assignment anywhere in the class -- without any cross-file
work or real type checking. The inference is deliberately conservative: an
expression it can't confidently type (a function call's return value, a
subscript, a chained method call) is left untyped rather than guessed at,
so the caller falls back to today's plain-name resolution instead of
manufacturing a wrong edge.

Docs on the two entry points parsers/python.py uses:

    infer_param_types(func_node, source)   -> {param_name: TypeName}
    infer_local_types(body, source)        -> {var_name: TypeName}
    infer_self_attr_types(class_body, src) -> {attr_name: TypeName}
    receiver_type_for_call(call, source, class_name, local_types, self_attr_types) -> TypeName | None
"""

from __future__ import annotations

from tree_sitter import Node

_SELF_NAMES = frozenset({"self", "cls"})


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _simple_type_name(raw: str) -> str | None:
    """`Service` -> `Service`; `List[Service]` -> None (generics aren't a receiver
    type); `models.Logger` -> `Logger` (module-qualified, common in Python)."""
    raw = raw.strip()
    if not raw or "[" in raw or raw in {"None", "Any", "object"}:
        return None
    name = raw.rsplit(".", 1)[-1]
    return name if name and name[0].isalpha() else None


def _type_from_annotation(type_node: Node | None, source: bytes) -> str | None:
    if type_node is None:
        return None
    return _simple_type_name(_text(type_node, source))


def _type_from_constructor_call(value_node: Node | None, source: bytes) -> str | None:
    """`Service(...)` or `pkg.Service(...)` -> `Service`. Only a bare/dotted
    identifier callee counts as a constructor; anything else (a factory
    function, a chained call) is left untyped."""
    if value_node is None or value_node.type != "call":
        return None
    fn = value_node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        name = _text(fn, source)
    elif fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        name = _text(attr, source)
    else:
        return None
    return name if name and name[0].isupper() else None


def infer_param_types(func_node: Node, source: bytes) -> dict[str, str]:
    """Typed parameters of a function/method: `def f(x: Service, y: Logger = None)`."""
    params = func_node.child_by_field_name("parameters")
    if params is None:
        return {}
    result: dict[str, str] = {}
    for child in params.children:
        if child.type not in ("typed_parameter", "typed_default_parameter"):
            continue
        # typed_parameter wraps a bare identifier as its first identifier child
        # (no field name); typed_default_parameter names it via the `name` field.
        name_node = child.child_by_field_name("name")
        if name_node is None:
            for sub in child.children:
                if sub.type == "identifier":
                    name_node = sub
                    break
        type_node = child.child_by_field_name("type")
        name = _text(name_node, source)
        type_name = _type_from_annotation(type_node, source)
        if name and type_name:
            result[name] = type_name
    return result


def _walk_assignments(node: Node, on_assignment) -> None:
    """Recurse into control-flow blocks (if/for/while/try/with) but not into
    nested function/class/lambda definitions -- those get their own scope."""
    for child in node.children:
        if child.type == "assignment":
            on_assignment(child)
        if child.type in ("function_definition", "class_definition", "lambda"):
            continue
        _walk_assignments(child, on_assignment)


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables in a function body: `x: Service = ...` or `x = Service()`.
    Last assignment wins on re-use of a name (rare, and re-typing a variable
    mid-function is itself rare enough this is an acceptable simplification)."""
    result: dict[str, str] = {}

    def visit(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return
        name = _text(left, source)
        if not name:
            return
        type_name = _type_from_annotation(assignment.child_by_field_name("type"), source)
        if type_name is None:
            type_name = _type_from_constructor_call(assignment.child_by_field_name("right"), source)
        if type_name:
            result[name] = type_name

    _walk_assignments(body, visit)
    return result


def infer_self_attr_types(class_body: Node, source: bytes) -> dict[str, str]:
    """`self.attr` types, gathered across every method in the class: `self.svc = Service()`
    in `__init__` is what makes `self.svc.save()` resolvable from any other method."""
    result: dict[str, str] = {}

    def visit(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "attribute":
            return
        obj = left.child_by_field_name("object")
        attr = left.child_by_field_name("attribute")
        if obj is None or obj.type != "identifier" or _text(obj, source) not in _SELF_NAMES:
            return
        attr_name = _text(attr, source)
        if not attr_name:
            return
        type_name = _type_from_annotation(assignment.child_by_field_name("type"), source)
        if type_name is None:
            type_name = _type_from_constructor_call(assignment.child_by_field_name("right"), source)
        if type_name:
            result[attr_name] = type_name

    # Walk every method body in the class (not just __init__ -- attributes are
    # sometimes set in a setup/configure method instead).
    for child in class_body.children:
        if child.type == "function_definition":
            fn_body = child.child_by_field_name("body")
            if fn_body is not None:
                _walk_assignments(fn_body, visit)
        elif child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is not None and inner.type == "function_definition":
                fn_body = inner.child_by_field_name("body")
                if fn_body is not None:
                    _walk_assignments(fn_body, visit)
    return result


def receiver_type_for_call(
    call: Node,
    source: bytes,
    class_name: str | None,
    local_types: dict[str, str],
    self_attr_types: dict[str, str],
) -> str | None:
    """The inferred type of a call's receiver -- `obj` in `obj.method()` -- or
    None when it can't be determined confidently (not a simple `x.m()`/`self.x.m()`
    shape, or the type wasn't tracked)."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "attribute":
        return None
    obj = fn.child_by_field_name("object")
    if obj is None:
        return None
    if obj.type == "identifier":
        name = _text(obj, source)
        if name in _SELF_NAMES:
            return class_name
        return local_types.get(name)
    if obj.type == "attribute":
        obj_obj = obj.child_by_field_name("object")
        obj_attr = obj.child_by_field_name("attribute")
        if obj_obj is None or obj_obj.type != "identifier":
            return None
        if _text(obj_obj, source) not in _SELF_NAMES:
            return None
        return self_attr_types.get(_text(obj_attr, source))
    return None
