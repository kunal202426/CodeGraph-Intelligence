# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""C and C++ parser — tree-sitter recursive walk → UIREntity stream.

A single shared implementation (`_CCppMixin`) drives two public parser
classes so that both languages can be registered independently in the CLI
while reusing all extraction logic.

C  (`CParser`,   Language.C,   grammar "c"):
    - `function_definition`              → FUNCTION
    - `struct_specifier` (named)         → CLASS
    - `type_definition` (typedef struct) → CLASS
    - `#include`                         → provisional `c:?:<path>` import edges
    - `call_expression`                  → provisional `c:?call:<callee>` edges

C++ (`CppParser`, Language.CPP, grammar "cpp"):
    - top-level `function_definition`    → FUNCTION
    - `class_specifier` / `struct_specifier` (named) → CLASS
    - `function_definition` inside class body → METHOD, qualified as
      "ClassName.method_name"; visibility tracks `access_specifier` nodes
    - `#include`                         → `cpp:?:<path>` import edges
    - `call_expression`                  → `cpp:?call:<callee>` edges

Both: `is_async=False` (neither language has a native async keyword in the
C/C++ sense that maps to our model), `is_exported` reflects `static`
linkage for free functions and access-modifier visibility for C++ methods.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import ClassVar

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    from tree_sitter import Node, Parser
    from tree_sitter_languages import get_language

from codegraph.parsers.base import ParseResult
from codegraph.resolution.receiver_types.c_cpp import (
    infer_class_field_types,
    infer_local_types,
    infer_param_types,
    params_node_from_decl,
    receiver_type_for_call,
)
from codegraph.uir import (
    Edge,
    EntityType,
    Language,
    UIREntity,
    hash_source,
    make_entity_id,
)

# node types whose `function` child is a scoped call (e.g. Server::create)
_FIELD_EXPR = "field_expression"
_QUAL_ID = "qualified_identifier"


class _CCppMixin:
    """Shared tree-sitter logic for CParser and CppParser."""

    # Subclasses must declare these ClassVars
    language: ClassVar[Language]
    _lang_name: ClassVar[str]
    _parser: ClassVar[Parser | None]

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language(cls._lang_name)
            p = Parser()
            p.set_language(lang)
            cls._parser = p
        return cls._parser

    # ------------------------------------------------------------------
    # Public API

    def parse(self, path: Path, source: str) -> ParseResult:
        rel_path = str(path).replace("\\", "/")
        source_bytes = source.encode("utf-8")
        tree = self._ts_parser().parse(source_bytes)
        root = tree.root_node
        lang = self.language

        entities: list[UIREntity] = []
        edges: list[Edge] = []
        errors: list[str] = []

        module_name = _module_name(rel_path)
        module_id = make_entity_id(lang, rel_path, module_name)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_name,
                language=lang,
                file=rel_path,
                start_line=1,
                end_line=max(root.end_point[0] + 1, 1),
                start_col=0,
                end_col=root.end_point[1],
                raw_source=source,
                docstring=None,
                signature=None,
                is_exported=True,
                is_async=False,
                parent_id=None,
                hash=hash_source(source),
            )
        )

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        class_field_types = infer_class_field_types(root, source_bytes)
        for child in root.children:
            kind = child.type
            if kind == "function_definition":
                self._emit_function(
                    child,
                    source_bytes,
                    rel_path,
                    module_id,
                    entities,
                    edges,
                    class_field_types=class_field_types,
                )
            elif kind in ("class_specifier", "struct_specifier"):
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    self._emit_class(
                        child,
                        source_bytes,
                        rel_path,
                        module_id,
                        entities,
                        edges,
                        class_field_types=class_field_types,
                    )
            elif kind == "type_definition":
                self._emit_typedef(child, source_bytes, rel_path, module_id, entities)
            elif kind == "preproc_include":
                self._emit_include(child, source_bytes, module_id, edges)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------
    # Entity emitters

    def _emit_function(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
        *,
        class_field_types: dict[str, dict[str, str]] | None = None,
    ) -> str | None:
        decl = node.child_by_field_name("declarator")
        name = _func_name_from_decl(decl, source)
        if not name:
            return None

        lang = self.language
        entity_id = make_entity_id(lang, file, name)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_exported = not _is_static(node)
        signature = _sig_before_body(node, source)

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=name,
                language=lang,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=is_exported,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            local_types = infer_param_types(params_node_from_decl(decl), source)
            local_types.update(infer_local_types(body, source))
            self._emit_calls(
                body,
                source,
                src_id=entity_id,
                edges=edges,
                class_name=None,
                local_types=local_types,
                class_field_types=class_field_types or {},
            )

        return entity_id

    def _emit_class(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
        *,
        class_field_types: dict[str, dict[str, str]] | None = None,
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return None

        lang = self.language
        entity_id = make_entity_id(lang, file, name)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = _sig_before_body(node, source) or f"class {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.CLASS,
                name=name,
                qualified_name=name,
                language=lang,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            # C++ class/struct: iterate for methods with access specifier tracking
            # class_specifier default = private; struct_specifier default = public
            is_private = node.type == "class_specifier"
            for child in body.children:
                if child.type == "access_specifier":
                    txt = _text(child, source) or ""
                    if "public" in txt:
                        is_private = False
                    elif "private" in txt or "protected" in txt:
                        is_private = True
                elif child.type == "function_definition":
                    self._emit_method(
                        child,
                        source,
                        file,
                        name,
                        entity_id,
                        entities,
                        edges,
                        is_exported=not is_private,
                        class_field_types=class_field_types,
                    )

        return entity_id

    def _emit_method(
        self,
        node: Node,
        source: bytes,
        file: str,
        owner_name: str,
        owner_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
        *,
        is_exported: bool,
        class_field_types: dict[str, dict[str, str]] | None = None,
    ) -> str | None:
        decl = node.child_by_field_name("declarator")
        name = _func_name_from_decl(decl, source)
        if not name:
            return None

        lang = self.language
        qname = f"{owner_name}.{name}"
        entity_id = make_entity_id(lang, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = _sig_before_body(node, source)

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=lang,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=is_exported,
                is_async=False,
                parent_id=owner_id,
                hash=hash_source(raw_source),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            local_types = infer_param_types(params_node_from_decl(decl), source)
            local_types.update(infer_local_types(body, source))
            self._emit_calls(
                body,
                source,
                src_id=entity_id,
                edges=edges,
                class_name=owner_name,
                local_types=local_types,
                class_field_types=class_field_types or {},
            )

        return entity_id

    def _emit_typedef(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        """Emit a CLASS entity for `typedef struct { ... } Name;`."""
        type_node = node.child_by_field_name("type")
        if type_node is None or type_node.type != "struct_specifier":
            return
        # Only emit for structs that have a body (not forward-declaration typedefs)
        if (
            type_node.child_by_field_name("body") is None
            and type_node.child_by_field_name("name") is not None
        ):
            # This is `typedef struct Named Named;` — the struct itself will be caught separately
            return

        decl_node = node.child_by_field_name("declarator")
        name = _text(decl_node, source)
        if not name:
            return

        lang = self.language
        entity_id = make_entity_id(lang, file, name)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.CLASS,
                name=name,
                qualified_name=name,
                language=lang,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=f"typedef struct {name}",
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

    # ------------------------------------------------------------------
    # Import extraction

    def _emit_include(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return
        if path_node.type == "string_literal":
            # Local include: "server.h" — extract string_content child
            path = None
            for c in path_node.children:
                if c.type == "string_content":
                    path = _text(c, source)
                    break
            if path is None:
                raw = _text(path_node, source) or ""
                path = raw.strip('"')
        elif path_node.type == "system_lib_string":
            raw = _text(path_node, source) or ""
            path = raw.strip("<>")
        else:
            path = _text(path_node, source)

        if path:
            lang_prefix = self.language.value  # "c" or "cpp"
            edges.append(
                Edge(
                    src_id=module_id,
                    dst_id=f"{lang_prefix}:?:{path}",
                    type="imports",
                    line=node.start_point[0] + 1,
                )
            )

    # ------------------------------------------------------------------
    # Call edge extraction

    def _emit_calls(
        self,
        body: Node,
        source: bytes,
        *,
        src_id: str,
        edges: list[Edge],
        class_name: str | None = None,
        local_types: dict[str, str] | None = None,
        class_field_types: dict[str, dict[str, str]] | None = None,
    ) -> None:
        lang_prefix = self.language.value
        for call in _iter_call_nodes(body):
            callee = _callee_name(call, source)
            if not callee:
                continue
            receiver_type = receiver_type_for_call(
                call, source, class_name, local_types or {}, class_field_types or {}
            )
            dst_id = (
                f"{lang_prefix}:?methodcall:{receiver_type}.{callee}"
                if receiver_type
                else f"{lang_prefix}:?call:{callee}"
            )
            edges.append(
                Edge(
                    src_id=src_id,
                    dst_id=dst_id,
                    type="calls",
                    line=call.start_point[0] + 1,
                    confidence=0.7,
                )
            )


# ------------------------------------------------------------------
# Public parser classes


class CParser(_CCppMixin):
    """Tree-sitter C parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.C
    _lang_name: ClassVar[str] = "c"
    _parser: ClassVar[Parser | None] = None


class CppParser(_CCppMixin):
    """Tree-sitter C++ parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.CPP
    _lang_name: ClassVar[str] = "cpp"
    _parser: ClassVar[Parser | None] = None


# ------------------------------------------------------------------
# Module-level helpers (no self needed)


def _module_name(rel_path: str) -> str:
    stem = rel_path.rsplit(".", 1)[0] if "." in rel_path else rel_path
    return stem.replace("/", ".")


def _text(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _func_name_from_decl(node: Node | None, source: bytes) -> str | None:
    """Recursively unwrap pointer/reference declarators to find the function name."""
    if node is None:
        return None
    if node.type == "function_declarator":
        inner = node.child_by_field_name("declarator")
        if inner is not None:
            return source[inner.start_byte : inner.end_byte].decode("utf-8", errors="replace")
    elif node.type in ("pointer_declarator", "reference_declarator", "abstract_pointer_declarator"):
        return _func_name_from_decl(node.child_by_field_name("declarator"), source)
    return None


def _is_static(node: Node) -> bool:
    for c in node.children:
        if c.type == "storage_class_specifier":
            return any(sub.type == "static" for sub in c.children)
    return False


def _sig_before_body(node: Node, source: bytes) -> str | None:
    body = node.child_by_field_name("body")
    if body is None:
        return None
    raw = source[node.start_byte : body.start_byte].decode("utf-8", errors="replace")
    return raw.strip()


def _iter_call_nodes(node: Node):
    for child in node.children:
        if child.type == "call_expression":
            yield child
        yield from _iter_call_nodes(child)


def _callee_name(call_node: Node, source: bytes) -> str | None:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return source[fn.start_byte : fn.end_byte].decode("utf-8", errors="replace")
    if fn.type == _FIELD_EXPR:
        field = fn.child_by_field_name("field")
        if field is not None:
            return source[field.start_byte : field.end_byte].decode("utf-8", errors="replace")
    if fn.type == _QUAL_ID:
        name_node = fn.child_by_field_name("name")
        if name_node is not None:
            return source[name_node.start_byte : name_node.end_byte].decode(
                "utf-8", errors="replace"
            )
    return None
