# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for Ruby method calls.

Ruby has no type annotations at all, so unlike the statically-typed
languages this only has one signal to work with: a `Type.new` constructor
call. `self` works like Python's, and an instance variable (`@svc`) is
Ruby's own directly-addressable receiver -- no `self.` prefix needed to read
it, so `@svc.save` is already single-level, unlike `self.attr.method()` in
the other languages.
"""

from __future__ import annotations

from tree_sitter import Node

_NESTED_SCOPE_TYPES = frozenset(
    {"method", "singleton_method", "class", "module", "lambda", "block", "do_block"}
)


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _type_from_value_expr(value: Node | None, source: bytes) -> str | None:
    """`Service.new` -> `Service`. Only a `.new` call on a bare constant
    receiver counts -- anything else (a factory method, a chained call)
    is left untyped."""
    if value is None or value.type != "call":
        return None
    receiver = value.child_by_field_name("receiver")
    method = value.child_by_field_name("method")
    if receiver is None or receiver.type != "constant" or method is None:
        return None
    if _text(method, source) != "new":
        return None
    return _text(receiver, source) or None


def _walk_assignments(node: Node, on_assignment) -> None:
    for child in node.children:
        if child.type == "assignment":
            on_assignment(child)
        if child.type in _NESTED_SCOPE_TYPES:
            continue
        _walk_assignments(child, on_assignment)


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables: `lg = Logger.new`."""
    result: dict[str, str] = {}

    def visit(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return
        name = _text(left, source)
        if not name:
            return
        type_name = _type_from_value_expr(assignment.child_by_field_name("right"), source)
        if type_name:
            result[name] = type_name

    _walk_assignments(body, visit)
    return result


def infer_self_attr_types(class_body: Node, source: bytes) -> dict[str, str]:
    """`@attr`'s type, from a `@attr = Service.new` assignment anywhere in the
    class -- so an instance variable set in `initialize` resolves from every
    other method. Keys keep the `@` prefix so lookups match the call site's
    receiver text directly."""
    result: dict[str, str] = {}

    def visit(assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "instance_variable":
            return
        name = _text(left, source)
        type_name = _type_from_value_expr(assignment.child_by_field_name("right"), source)
        if name and type_name:
            result[name] = type_name

    def walk_for_assignments(node: Node) -> None:
        for child in node.children:
            if child.type == "assignment":
                visit(child)
            if child.type in _NESTED_SCOPE_TYPES:
                continue
            walk_for_assignments(child)

    for child in class_body.children:
        if child.type in ("method", "singleton_method"):
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
    """The inferred type of a call's receiver, or None when it's a bare call
    (no receiver), a chained/complex receiver, or the type wasn't tracked."""
    receiver = call.child_by_field_name("receiver")
    if receiver is None:
        return None
    if receiver.type == "self":
        return class_name
    if receiver.type == "identifier":
        return local_types.get(_text(receiver, source))
    if receiver.type == "instance_variable":
        return self_attr_types.get(_text(receiver, source))
    return None
