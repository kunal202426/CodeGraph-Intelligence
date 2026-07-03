# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Receiver-type inference for C/C++ method calls.

Same idea as the other slices, adapted to C/C++'s declarator grammar: a
type's pointer/reference-ness lives on the *declarator* (`pointer_declarator`
wrapping an identifier), not the type field itself, so unwrapping that is
the one piece of extra work here. `this` works like Python's `self`. Class
fields are always explicitly typed (no assignment-inferred members), so,
like the Go/Rust slices, `infer_class_field_types` reads a whole-file table
straight from each `class_specifier`/`struct_specifier` instead of scanning
assignments -- which also means `x->field->method()` resolves for any local
of a known class type, not just `this`. Shared by both `CParser` and
`CppParser` (the C grammar just never produces a `this` node or nested
classes, so those code paths simply don't fire).
"""

from __future__ import annotations

from tree_sitter import Node

_NESTED_SCOPE_TYPES = frozenset({"function_definition", "lambda_expression"})


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _simple_type(type_node: Node | None, source: bytes) -> str | None:
    """`Service` -> `Service`. Pointer/reference-ness lives on the declarator,
    not here, so this only needs to reject non-concrete type nodes: a
    primitive (`int`), `auto`, a template, or a qualified/scoped type."""
    if type_node is None or type_node.type != "type_identifier":
        return None
    return _text(type_node, source)


def _declarator_name(node: Node | None, source: bytes) -> str | None:
    """Unwrap `pointer_declarator`/`reference_declarator`/`init_declarator`
    to the leaf identifier -- `svc` from `* svc` or `& svc`."""
    if node is None:
        return None
    if node.type in ("identifier", "field_identifier"):
        return _text(node, source)
    if node.type in ("pointer_declarator", "reference_declarator", "init_declarator"):
        return _declarator_name(node.child_by_field_name("declarator"), source)
    return None


def _type_from_new_expression(value: Node | None, source: bytes) -> str | None:
    """`new Service(...)` -> `Service`."""
    if value is None or value.type != "new_expression":
        return None
    return _simple_type(value.child_by_field_name("type"), source)


def params_node_from_decl(node: Node | None) -> Node | None:
    """The `parameter_list` for a function/method's declarator, unwrapping a
    pointer/reference return type the same way `_func_name_from_decl` does."""
    if node is None:
        return None
    if node.type == "function_declarator":
        return node.child_by_field_name("parameters")
    if node.type in ("pointer_declarator", "reference_declarator"):
        inner = node.child_by_field_name("declarator")
        if inner is None:
            # reference_declarator wraps its function_declarator as an
            # unnamed child, not via the `declarator` field.
            inner = next((c for c in node.children if c.is_named), None)
        return params_node_from_decl(inner)
    return None


def infer_param_types(params: Node | None, source: bytes) -> dict[str, str]:
    """Typed parameters: `void Run(Logger* logger, int other) { ... }`."""
    if params is None:
        return {}
    result: dict[str, str] = {}
    for child in params.children:
        if child.type != "parameter_declaration":
            continue
        type_name = _simple_type(child.child_by_field_name("type"), source)
        name = _declarator_name(child.child_by_field_name("declarator"), source)
        if name and type_name:
            result[name] = type_name
    return result


def infer_local_types(body: Node, source: bytes) -> dict[str, str]:
    """Local variables: `Logger* lg = logger;` or `auto y = new OtherThing();`."""
    result: dict[str, str] = {}

    def visit(decl_stmt: Node) -> None:
        type_name = _simple_type(decl_stmt.child_by_field_name("type"), source)
        for declarator in decl_stmt.children_by_field_name("declarator"):
            name = _declarator_name(declarator, source)
            if not name:
                continue
            value = (
                declarator.child_by_field_name("value")
                if declarator.type == "init_declarator"
                else None
            )
            resolved_type = type_name or _type_from_new_expression(value, source)
            if resolved_type:
                result[name] = resolved_type

    def walk(node: Node) -> None:
        for child in node.children:
            if child.type == "declaration":
                visit(child)
            if child.type in _NESTED_SCOPE_TYPES:
                continue
            walk(child)

    walk(body)
    return result


def infer_class_field_types(root: Node, source: bytes) -> dict[str, dict[str, str]]:
    """`{ClassName: {field_name: TypeName}}` for every named class/struct in
    the file, found anywhere (including inside namespaces)."""
    result: dict[str, dict[str, str]] = {}

    def visit_class(node: Node) -> None:
        name = _text(node.child_by_field_name("name"), source)
        body = node.child_by_field_name("body")
        if not name or body is None:
            return
        fields: dict[str, str] = {}
        for child in body.children:
            if child.type != "field_declaration":
                continue
            type_name = _simple_type(child.child_by_field_name("type"), source)
            if type_name is None:
                continue
            for declarator in child.children_by_field_name("declarator"):
                fname = _declarator_name(declarator, source)
                if fname:
                    fields[fname] = type_name
        if fields:
            result[name] = fields

    def walk(node: Node) -> None:
        for child in node.children:
            if child.type in ("class_specifier", "struct_specifier"):
                visit_class(child)
            walk(child)

    walk(root)
    return result


def receiver_type_for_call(
    call: Node,
    source: bytes,
    class_name: str | None,
    local_types: dict[str, str],
    class_field_types: dict[str, dict[str, str]],
) -> str | None:
    """The inferred type of a call's receiver -- `x` in `x->method()` -- or
    None when it's not a simple `x->m()`/`x->field->m()` shape, or the type
    wasn't tracked."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "field_expression":
        return None
    obj = fn.child_by_field_name("argument")
    if obj is None:
        return None
    if obj.type == "this":
        return class_name
    if obj.type == "identifier":
        return local_types.get(_text(obj, source))
    if obj.type == "field_expression":
        obj_obj = obj.child_by_field_name("argument")
        obj_field = obj.child_by_field_name("field")
        if obj_obj is None or obj_field is None:
            return None
        if obj_obj.type == "this":
            obj_type = class_name
        elif obj_obj.type == "identifier":
            obj_type = local_types.get(_text(obj_obj, source))
        else:
            return None
        if obj_type is None:
            return None
        return class_field_types.get(obj_type, {}).get(_text(obj_field, source))
    return None
