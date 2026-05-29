"""PHP parser — tree-sitter recursive walk → UIREntity stream.

Emits one Module entity per file plus Function / Method / Class / Interface
per top-level declaration.

- Top-level `function_definition`            → FUNCTION
- `class_declaration` / `trait_declaration`  → CLASS
- `interface_declaration`                    → INTERFACE
- `method_declaration` inside a class /
  interface / trait body                     → METHOD, qualified as
                                               "ClassName.method_name"

`use` declarations are emitted as provisional `php:?:<path>` import edges;
`require` / `require_once` / `include` / `include_once` expressions likewise.
Method calls are emitted as `php:?call:<callee>`, resolved in T10.7.
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

_REQUIRE_NODE_TYPES = frozenset(
    {
        "require_once_expression",
        "require_expression",
        "include_once_expression",
        "include_expression",
    }
)

_CALL_NODE_TYPES = frozenset(
    {
        "member_call_expression",
        "function_call_expression",
        "scoped_call_expression",
    }
)


class PHPParser:
    """Tree-sitter PHP parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.PHP
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("php")
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
        module_id = make_entity_id(Language.PHP, rel_path, module_name)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_name,
                language=Language.PHP,
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
            if kind in ("class_declaration", "trait_declaration"):
                self._emit_class_like(
                    child, source_bytes, rel_path, module_id, EntityType.CLASS, entities, edges
                )
            elif kind == "interface_declaration":
                self._emit_class_like(
                    child, source_bytes, rel_path, module_id, EntityType.INTERFACE, entities, edges
                )
            elif kind == "function_definition":
                self._emit_function(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "namespace_use_declaration":
                self._emit_use(child, source_bytes, module_id, edges)
            elif kind == "expression_statement":
                for sub in child.children:
                    if sub.type in _REQUIRE_NODE_TYPES:
                        self._emit_require(sub, source_bytes, module_id, edges)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------
    # Entity emitters

    def _emit_class_like(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entity_type: EntityType,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        entity_id = make_entity_id(Language.PHP, file, name)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source) or f"class {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=name,
                language=Language.PHP,
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
            for child in body.children:
                if child.type == "method_declaration":
                    self._emit_method(child, source, file, name, entity_id, entities, edges)

        return entity_id

    def _emit_function(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        entity_id = make_entity_id(Language.PHP, file, name)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source) or f"function {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=name,
                language=Language.PHP,
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
            self._emit_calls(body, source, src_id=entity_id, edges=edges)

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
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = f"{owner_name}.{name}"
        entity_id = make_entity_id(Language.PHP, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_exported = self._is_pub(node)
        signature = self._sig_before_body(node, source)
        if signature is None:
            # Abstract / interface method ends with ';' — strip it
            signature = raw_source.strip().rstrip(";").strip()

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=Language.PHP,
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
            self._emit_calls(body, source, src_id=entity_id, edges=edges)

        return entity_id

    # ------------------------------------------------------------------
    # Import extraction

    def _emit_use(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        line = node.start_point[0] + 1
        for path in self._flatten_use(node, source):
            if path:
                edges.append(
                    Edge(
                        src_id=module_id,
                        dst_id=f"php:?:{path}",
                        type="imports",
                        line=line,
                    )
                )

    def _flatten_use(self, node: Node, source: bytes) -> list[str]:
        """Flatten a namespace_use_declaration into import paths."""
        paths: list[str] = []
        base: str | None = None

        for c in node.children:
            if c.type == "namespace_use_clause":
                # Simple or aliased import: first named child is qualified_name or namespace_name
                for sub in c.children:
                    if sub.type in ("qualified_name", "namespace_name"):
                        paths.append(self._text(sub, source) or "")
                        break
            elif c.type == "namespace_name":
                # Prefix for grouped import
                base = self._text(c, source)
            elif c.type == "namespace_use_group":
                for clause in c.children:
                    if clause.type == "namespace_use_group_clause":
                        for sub in clause.children:
                            if sub.type == "namespace_name":
                                suffix = self._text(sub, source) or ""
                                paths.append(f"{base}\\{suffix}" if base else suffix)
                                break

        return [p for p in paths if p]

    def _emit_require(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        path = self._require_path(node, source)
        if path:
            edges.append(
                Edge(
                    src_id=module_id,
                    dst_id=f"php:?:{path}",
                    type="imports",
                    line=node.start_point[0] + 1,
                )
            )

    def _require_path(self, node: Node, source: bytes) -> str | None:
        for c in node.children:
            if c.type == "string":
                for sub in c.children:
                    if sub.type == "string_value":
                        return self._text(sub, source)
        return None

    # ------------------------------------------------------------------
    # Call edge extraction

    def _emit_calls(self, body: Node, source: bytes, *, src_id: str, edges: list[Edge]) -> None:
        for call in self._iter_call_nodes(body):
            callee = self._callee_name(call, source)
            if callee:
                edges.append(
                    Edge(
                        src_id=src_id,
                        dst_id=f"php:?call:{callee}",
                        type="calls",
                        line=call.start_point[0] + 1,
                        confidence=0.7,
                    )
                )

    def _iter_call_nodes(self, node: Node):
        for child in node.children:
            if child.type in _CALL_NODE_TYPES:
                yield child
            yield from self._iter_call_nodes(child)

    @staticmethod
    def _callee_name(call_node: Node, source: bytes) -> str | None:
        kind = call_node.type
        if kind in ("member_call_expression", "scoped_call_expression"):
            name_node = call_node.child_by_field_name("name")
        elif kind == "function_call_expression":
            name_node = call_node.child_by_field_name("function")
        else:
            return None
        if name_node is None:
            return None
        return source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _is_pub(node: Node) -> bool:
        for c in node.children:
            if c.type == "visibility_modifier":
                return any(sub.type == "public" for sub in c.children)
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
        stem = rel_path.removesuffix(".php")
        return stem.replace("/", ".")
