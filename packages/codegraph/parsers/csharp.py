# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""C# parser — tree-sitter walk → UIREntity stream.

Emits one Module entity per file plus Class / Interface / Method per
top-level and namespace-scoped declarations.

- `class_declaration` / `struct_declaration`  → CLASS
- `interface_declaration`                     → INTERFACE
- `method_declaration` inside a class body    → METHOD (qualified ClassName.method)
- `constructor_declaration` inside a class    → METHOD
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


class CSharpParser:
    """Tree-sitter C# parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.CSHARP
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("c_sharp")
            p = Parser()
            p.set_language(lang)
            cls._parser = p
        return cls._parser

    def parse(self, path: Path, source: str) -> ParseResult:
        rel_path = str(path).replace("\\", "/")
        source_bytes = source.encode("utf-8")
        tree = self._ts_parser().parse(source_bytes)
        root = tree.root_node

        entities: list[UIREntity] = []
        edges: list[Edge] = []
        errors: list[str] = []

        module_name = _stem(rel_path, ".cs")
        module_id = make_entity_id(Language.CSHARP, rel_path, module_name)
        entities.append(_module_entity(Language.CSHARP, module_id, module_name, rel_path, root, source))

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        self._walk(root.children, source_bytes, rel_path, module_id, entities)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------

    def _walk(
        self,
        nodes: list[Node],
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        for node in nodes:
            kind = node.type
            if kind == "namespace_declaration":
                body = node.child_by_field_name("body")
                if body is not None:
                    self._walk(body.children, source, file, parent_id, entities)
            elif kind in ("class_declaration", "struct_declaration"):
                self._emit_class(node, source, file, parent_id, EntityType.CLASS, entities)
            elif kind == "interface_declaration":
                self._emit_class(node, source, file, parent_id, EntityType.INTERFACE, entities)

    def _emit_class(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entity_type: EntityType,
        entities: list[UIREntity],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return None

        qname = name
        entity_id = make_entity_id(Language.CSHARP, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        kw = "interface" if entity_type == EntityType.INTERFACE else "class"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=qname,
                language=Language.CSHARP,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=f"{kw} {name}",
                is_exported=not name.startswith("_"),
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            for member in body.children:
                if member.type in ("method_declaration", "constructor_declaration"):
                    self._emit_method(member, source, file, entity_id, name, entities)

        return entity_id

    def _emit_method(
        self,
        node: Node,
        source: bytes,
        file: str,
        class_id: str,
        class_name: str,
        entities: list[UIREntity],
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return

        qname = f"{class_name}.{name}"
        entity_id = make_entity_id(Language.CSHARP, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=Language.CSHARP,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=_sig_before_body(node, source),
                is_exported=not name.startswith("_"),
                is_async=False,
                parent_id=class_id,
                hash=hash_source(raw),
            )
        )


def _module_entity(
    lang: Language,
    entity_id: str,
    name: str,
    file: str,
    root: Node,
    source: str,
) -> UIREntity:
    return UIREntity(
        entity_id=entity_id,
        type=EntityType.MODULE,
        name=name,
        qualified_name=name,
        language=lang,
        file=file,
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


def _stem(rel_path: str, *suffixes: str) -> str:
    stem = rel_path
    for s in suffixes:
        stem = stem.removesuffix(s)
    return stem.replace("/", ".")


def _text(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _sig_before_body(node: Node, source: bytes) -> str | None:
    body = node.child_by_field_name("body")
    if body is None:
        return None
    raw = source[node.start_byte : body.start_byte].decode("utf-8", errors="replace")
    return raw.strip()
