# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Rust parser — tree-sitter recursive walk → UIREntity stream.

Emits one Module entity per file plus Function / Method / Class (struct,
enum) / Interface (trait) per top-level declaration.

- Top-level `fn` → FUNCTION
- `struct` / `enum` → CLASS
- `trait` → INTERFACE
- `fn` inside `impl TypeName` or `impl Trait for TypeName` → METHOD,
  qualified as "TypeName.method_name"

Import edges (`use_declaration`) are emitted as provisional `rs:?:<path>`
and call edges as `rs:?call:<callee>`, resolved in T10.7.
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
from codegraph.uir import (
    Edge,
    EntityType,
    Language,
    UIREntity,
    hash_source,
    make_entity_id,
)


class RustParser:
    """Tree-sitter Rust parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.RUST
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("rust")
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

        entities: list[UIREntity] = []
        edges: list[Edge] = []
        errors: list[str] = []

        module_name = self._module_name_from_path(rel_path)
        module_id = make_entity_id(Language.RUST, rel_path, module_name)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_name,
                language=Language.RUST,
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

        for child in root.children:
            kind = child.type
            if kind == "function_item":
                self._emit_function(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind in ("struct_item", "enum_item"):
                self._emit_type_item(child, source_bytes, rel_path, module_id, entities)
            elif kind == "trait_item":
                self._emit_trait(child, source_bytes, rel_path, module_id, entities)
            elif kind == "impl_item":
                self._emit_impl(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "use_declaration":
                self._emit_use(child, source_bytes, module_id, edges)

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
        entity_type: EntityType = EntityType.FUNCTION,
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = name
        entity_id = make_entity_id(Language.RUST, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source)
        is_exported = self._is_pub(node)
        is_async = self._is_async(node)

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=qname,
                language=Language.RUST,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=is_exported,
                is_async=is_async,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            self._emit_calls(body, source, src_id=entity_id, edges=edges)

        return entity_id

    def _emit_method(
        self,
        node: Node,
        source: bytes,
        file: str,
        receiver_type: str,
        module_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = f"{receiver_type}.{name}"
        entity_id = make_entity_id(Language.RUST, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source)
        is_exported = self._is_pub(node)
        is_async = self._is_async(node)

        parent_id = make_entity_id(Language.RUST, file, receiver_type)

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=Language.RUST,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=is_exported,
                is_async=is_async,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            self._emit_calls(body, source, src_id=entity_id, edges=edges)

        return entity_id

    def _emit_type_item(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = name
        entity_id = make_entity_id(Language.RUST, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_exported = self._is_pub(node)
        kind_word = "struct" if node.type == "struct_item" else "enum"
        pub_prefix = "pub " if is_exported else ""

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.CLASS,
                name=name,
                qualified_name=qname,
                language=Language.RUST,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=f"{pub_prefix}{kind_word} {name}",
                is_exported=is_exported,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )
        return entity_id

    def _emit_trait(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = name
        entity_id = make_entity_id(Language.RUST, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_exported = self._is_pub(node)
        pub_prefix = "pub " if is_exported else ""

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.INTERFACE,
                name=name,
                qualified_name=qname,
                language=Language.RUST,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=f"{pub_prefix}trait {name}",
                is_exported=is_exported,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )
        return entity_id

    def _emit_impl(
        self,
        node: Node,
        source: bytes,
        file: str,
        module_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> None:
        type_node = node.child_by_field_name("type")
        receiver_type = self._text(type_node, source)
        if not receiver_type:
            return

        body = node.child_by_field_name("body")
        if body is None:
            return

        for child in body.children:
            if child.type == "function_item":
                self._emit_method(child, source, file, receiver_type, module_id, entities, edges)

    # ------------------------------------------------------------------
    # Import extraction — provisional `rs:?:<path>` edges

    def _emit_use(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        arg = node.child_by_field_name("argument")
        if arg is None:
            return
        line = node.start_point[0] + 1
        for path_str in self._flatten_use(arg, source):
            if path_str:
                edges.append(
                    Edge(
                        src_id=module_id,
                        dst_id=f"rs:?:{path_str}",
                        type="imports",
                        line=line,
                    )
                )

    def _flatten_use(self, node: Node, source: bytes) -> list[str]:
        """Flatten a use argument tree into a list of dotted import paths."""
        kind = node.type
        if kind in ("scoped_identifier", "identifier"):
            text = self._text(node, source)
            return [text] if text else []
        if kind == "scoped_use_list":
            path_node = node.child_by_field_name("path")
            list_node = node.child_by_field_name("list")
            base = self._text(path_node, source) or ""
            paths: list[str] = []
            if list_node:
                for child in list_node.children:
                    if not child.is_named:
                        continue
                    if child.type == "self":
                        paths.append(base)
                    else:
                        for sub in self._flatten_use(child, source):
                            paths.append(f"{base}::{sub}" if base else sub)
            return paths
        if kind == "use_as_clause":
            path_node = node.child_by_field_name("path")
            text = self._text(path_node, source)
            return [text] if text else []
        if kind == "use_wildcard":
            text = self._text(node, source)
            return [text] if text else []
        return []

    # ------------------------------------------------------------------
    # Call edge extraction — provisional `rs:?call:<callee>` edges

    def _emit_calls(self, body: Node, source: bytes, *, src_id: str, edges: list[Edge]) -> None:
        for call in self._iter_call_nodes(body):
            callee = self._callee_name(call, source)
            if not callee:
                continue
            edges.append(
                Edge(
                    src_id=src_id,
                    dst_id=f"rs:?call:{callee}",
                    type="calls",
                    line=call.start_point[0] + 1,
                    confidence=0.7,
                )
            )

    def _iter_call_nodes(self, node: Node):
        for child in node.children:
            if child.type == "call_expression":
                yield child
            yield from self._iter_call_nodes(child)

    @staticmethod
    def _callee_name(call_node: Node, source: bytes) -> str | None:
        fn = call_node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return source[fn.start_byte : fn.end_byte].decode("utf-8", errors="replace")
        if fn.type == "field_expression":
            # self.method() — "field" is the method name in tree-sitter-rust
            field = fn.child_by_field_name("field")
            if field is not None:
                return source[field.start_byte : field.end_byte].decode("utf-8", errors="replace")
        if fn.type == "scoped_identifier":
            # Type::method() — "name" is the last segment
            name = fn.child_by_field_name("name")
            if name is not None:
                return source[name.start_byte : name.end_byte].decode("utf-8", errors="replace")
        return None

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _is_pub(node: Node) -> bool:
        return any(c.type == "visibility_modifier" for c in node.children)

    @staticmethod
    def _is_async(node: Node) -> bool:
        for c in node.children:
            if c.type == "async":
                return True
            if c.type == "function_modifiers":
                for sub in c.children:
                    if sub.type == "async":
                        return True
        return False

    def _sig_before_body(self, node: Node, source: bytes) -> str | None:
        body = node.child_by_field_name("body")
        if body is None:
            return None
        raw = source[node.start_byte : body.start_byte].decode("utf-8", errors="replace")
        return raw.strip()

    @staticmethod
    def _text(node: Node | None, source: bytes) -> str | None:
        if node is None:
            return None
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _module_name_from_path(rel_path: str) -> str:
        stem = rel_path.removesuffix(".rs")
        return stem.replace("/", ".")
