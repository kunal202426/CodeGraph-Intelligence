# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for Go method calls.

Go has no `self`/`this` -- a method's receiver is just another named,
typed parameter (`func (c *Caller) Use() { ... }`), so it's tracked the same
way as any other local: `infer_param_types` already covers it by being called
on the method's `receiver` field the same as its `parameters` field. Struct
fields are always explicitly typed (Go has no inferred-from-assignment struct
fields), so `infer_struct_field_types` reads the type declarations directly
instead of scanning assignments for a constructor-call pattern the way the
other languages do -- more reliable, and it's a whole-file table rather than
"the enclosing class's fields" since Go doesn't scope struct field access to
a single receiver variable name.
"""

from __future__ import annotations

from tree_sitter import Node

_NESTED_SCOPE_TYPES = frozenset({"func_literal"})


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _simple_type(type_node: Node | None, source: bytes) -> str | None:
    """`Service` -> `Service`; `*Service` -> `Service` (pointer receivers and
    fields are the common case); a slice/map/generic/qualified `pkg.Type` isn't
    a single local concrete type here -- don't guess."""
    if type_node is None:
        return None
    if type_node.type == "type_identifier":
        return _text(type_node, source)
    if type_node.type == "pointer_type":
        for child in type_node.children:
            if child.type == "type_identifier":
                return _text(child, source)
    return None


def _first_named(node: Node | None) -> Node | None:
    if node is None:
        return None
    for child in node.children:
        if child.is_named:
            return child
    return None


def _type_from_value_expr(value: Node | None, source: bytes) -> str | None:
    """`&Service{...}` or `Service{...}` -> `Service`."""
    if value is None:
        return None
    if value.type == "unary_expression":
        return _type_from_value_expr(value.child_by_field_name("operand"), source)
    if value.type == "composite_literal":
        return _simple_type(value.child_by_field_name("type"), source)
    return None


def infer_param_types(params_list: Node | None, source: bytes) -> dict[str, str]:
    """Typed parameters -- also used for a method's `receiver` field, which is
    grammatically the same `parameter_list` shape: `(c *Caller)`."""
    if params_list is None:
        return {}
    result: dict[str, str] = {}
    for child in params_list.children:
        if child.type != "parameter_declaration":
            continue
        name = _text(child.child_by_field_name("name"), source)
        type_name = _simple_type(child.child_by_field_name("type"), source)
        if name and type_name:
            result[name] = type_name
    return result


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables: `var lg *Logger = ...` or `y := &OtherThing{}`."""
    result: dict[str, str] = {}

    def visit_var_spec(var_spec: Node) -> None:
        name = _text(var_spec.child_by_field_name("name"), source)
        if not name:
            return
        type_name = _simple_type(var_spec.child_by_field_name("type"), source)
        if type_name is None:
            type_name = _type_from_value_expr(
                _first_named(var_spec.child_by_field_name("value")), source
            )
        if type_name:
            result[name] = type_name

    def visit_short_var(stmt: Node) -> None:
        left = stmt.child_by_field_name("left")
        right = stmt.child_by_field_name("right")
        if left is None or right is None:
            return
        left_names = [c for c in left.children if c.is_named]
        right_values = [c for c in right.children if c.is_named]
        for name_node, value_node in zip(left_names, right_values, strict=False):
            if name_node.type != "identifier":
                continue
            name = _text(name_node, source)
            type_name = _type_from_value_expr(value_node, source)
            if name and type_name:
                result[name] = type_name

    def walk(node: Node) -> None:
        for child in node.children:
            if child.type == "var_declaration":
                for spec in child.children:
                    if spec.type == "var_spec":
                        visit_var_spec(spec)
            elif child.type == "short_var_declaration":
                visit_short_var(child)
            if child.type in _NESTED_SCOPE_TYPES:
                continue
            walk(child)

    walk(body)
    return result


def infer_struct_field_types(root: Node, source: bytes) -> dict[str, dict[str, str]]:
    """`{StructName: {field_name: TypeName}}` for every top-level struct type
    declaration in the file."""
    result: dict[str, dict[str, str]] = {}
    for child in root.children:
        if child.type != "type_declaration":
            continue
        for spec in child.children:
            if spec.type != "type_spec":
                continue
            name = _text(spec.child_by_field_name("name"), source)
            type_node = spec.child_by_field_name("type")
            if not name or type_node is None or type_node.type != "struct_type":
                continue
            field_list = next(
                (c for c in type_node.children if c.type == "field_declaration_list"), None
            )
            if field_list is None:
                continue
            fields: dict[str, str] = {}
            for f in field_list.children:
                if f.type != "field_declaration":
                    continue
                fname = _text(f.child_by_field_name("name"), source)
                ftype = _simple_type(f.child_by_field_name("type"), source)
                if fname and ftype:
                    fields[fname] = ftype
            if fields:
                result[name] = fields
    return result


def receiver_type_for_call(
    call: Node,
    source: bytes,
    local_types: dict[str, str],
    struct_field_types: dict[str, dict[str, str]],
) -> str | None:
    """The inferred type of a call's receiver -- `x` in `x.Method()` -- or None
    when it's not a simple `x.M()`/`x.field.M()` shape, or the type wasn't
    tracked. `x.field.M()` needs `x`'s type to be known so `field`'s type can
    be looked up on it -- any local of a known struct type, not just the
    method's own receiver variable."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "selector_expression":
        return None
    operand = fn.child_by_field_name("operand")
    if operand is None:
        return None
    if operand.type == "identifier":
        return local_types.get(_text(operand, source))
    if operand.type == "selector_expression":
        obj_operand = operand.child_by_field_name("operand")
        obj_field = operand.child_by_field_name("field")
        if obj_operand is None or obj_operand.type != "identifier" or obj_field is None:
            return None
        obj_type = local_types.get(_text(obj_operand, source))
        if obj_type is None:
            return None
        return struct_field_types.get(obj_type, {}).get(_text(obj_field, source))
    return None
