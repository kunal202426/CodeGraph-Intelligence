# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for Rust method calls.

`self` works like Python's -- a call's receiver is the enclosing `impl`
block's type, passed in as `class_name`. Struct fields are always explicitly
typed (Rust has no assignment-inferred fields), so, like Go,
`infer_struct_field_types` reads a whole-file table straight from each
`struct_item` rather than scanning assignments -- and because it's
whole-file, `x.field.method()` resolves for any local of a known struct
type, not just `self`. Rust's `Type::assoc_fn()` (the usual constructor
idiom, no `new` keyword to key off) shares its call-expression shape with a
plain namespaced function call (`std::mem::swap()`), so, like Python,
a capitalization check on the leading path segment decides which is which.
"""

from __future__ import annotations

from tree_sitter import Node

_NESTED_SCOPE_TYPES = frozenset({"closure_expression", "function_item", "impl_item"})


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _simple_type(type_node: Node | None, source: bytes) -> str | None:
    """`Logger` -> `Logger`; `&Logger` / `&mut Logger` -> `Logger`; a generic
    (`Vec<Logger>`) or a path type isn't a single local concrete type -- don't
    guess."""
    if type_node is None:
        return None
    if type_node.type == "type_identifier":
        return _text(type_node, source)
    if type_node.type == "reference_type":
        for child in type_node.children:
            if child.type == "type_identifier":
                return _text(child, source)
    return None


def _type_from_value_expr(value: Node | None, source: bytes) -> str | None:
    """`OtherThing::new(...)` -> `OtherThing`, only when the leading path
    segment looks like a Type (capitalized) -- `Type::f()` and `mod::f()`
    share the same grammar shape in Rust."""
    if value is None or value.type != "call_expression":
        return None
    fn = value.child_by_field_name("function")
    if fn is None or fn.type != "scoped_identifier":
        return None
    path = fn.child_by_field_name("path")
    if path is None or path.type != "identifier":
        return None
    name = _text(path, source)
    return name if name and name[0].isupper() else None


def infer_param_types(func_node: Node, source: bytes) -> dict[str, str]:
    """Typed parameters: `fn new(logger: Logger, other: i32) { ... }`."""
    params = func_node.child_by_field_name("parameters")
    if params is None:
        return {}
    result: dict[str, str] = {}
    for child in params.children:
        if child.type != "parameter":
            continue
        pattern = child.child_by_field_name("pattern")
        if pattern is None or pattern.type != "identifier":
            continue
        type_name = _simple_type(child.child_by_field_name("type"), source)
        name = _text(pattern, source)
        if name and type_name:
            result[name] = type_name
    return result


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables: `let lg: Logger = ...` or `let y = OtherThing::new();`."""
    result: dict[str, str] = {}

    def visit(decl: Node) -> None:
        pattern = decl.child_by_field_name("pattern")
        if pattern is None or pattern.type != "identifier":
            return
        name = _text(pattern, source)
        if not name:
            return
        type_name = _simple_type(decl.child_by_field_name("type"), source)
        if type_name is None:
            type_name = _type_from_value_expr(decl.child_by_field_name("value"), source)
        if type_name:
            result[name] = type_name

    def walk(node: Node) -> None:
        for child in node.children:
            if child.type == "let_declaration":
                visit(child)
            if child.type in _NESTED_SCOPE_TYPES:
                continue
            walk(child)

    walk(body)
    return result


def infer_struct_field_types(root: Node, source: bytes) -> dict[str, dict[str, str]]:
    """`{StructName: {field_name: TypeName}}` for every top-level struct in the file."""
    result: dict[str, dict[str, str]] = {}
    for child in root.children:
        if child.type != "struct_item":
            continue
        name = _text(child.child_by_field_name("name"), source)
        body = child.child_by_field_name("body")
        if not name or body is None or body.type != "field_declaration_list":
            continue
        fields: dict[str, str] = {}
        for f in body.children:
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
    class_name: str | None,
    local_types: dict[str, str],
    struct_field_types: dict[str, dict[str, str]],
) -> str | None:
    """The inferred type of a call's receiver -- `x` in `x.method()` -- or
    None when it's not a simple `x.m()`/`x.field.m()` shape, or the type
    wasn't tracked."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "field_expression":
        return None
    obj = fn.child_by_field_name("value")
    if obj is None:
        return None
    if obj.type == "self":
        return class_name
    if obj.type == "identifier":
        return local_types.get(_text(obj, source))
    if obj.type == "field_expression":
        obj_obj = obj.child_by_field_name("value")
        obj_field = obj.child_by_field_name("field")
        if obj_obj is None or obj_field is None:
            return None
        if obj_obj.type == "self":
            obj_type = class_name
        elif obj_obj.type == "identifier":
            obj_type = local_types.get(_text(obj_obj, source))
        else:
            return None
        if obj_type is None:
            return None
        return struct_field_types.get(obj_type, {}).get(_text(obj_field, source))
    return None
