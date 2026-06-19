# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""Haskell parser — tree-sitter walk → UIREntity stream.

Emits one Module entity per file plus Function / Class (data type) /
Interface (type class) per top-level declaration.

- `function` (binding)   → FUNCTION (name from `variable` field)
- `adt` (data/newtype)   → CLASS (name from `type` field)
- `class` (type class)   → INTERFACE (name from class_head.class_name)
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


class HaskellParser:
    """Tree-sitter Haskell parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.HASKELL
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("haskell")
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

        module_name = _stem(rel_path, ".hs")
        module_id = make_entity_id(Language.HASKELL, rel_path, module_name)
        entities.append(
            _module_entity(Language.HASKELL, module_id, module_name, rel_path, root, source)
        )

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        seen_fn_names: set[str] = set()
        for child in root.children:
            kind = child.type
            if kind == "function":
                self._emit_function(
                    child, source_bytes, rel_path, module_id, entities, seen_fn_names
                )
            elif kind == "adt":
                self._emit_adt(child, source_bytes, rel_path, module_id, entities)
            elif kind == "class":
                self._emit_class(child, source_bytes, rel_path, module_id, entities)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    def _emit_function(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
        seen: set[str],
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name or name in seen:
            return
        seen.add(name)

        entity_id = make_entity_id(Language.HASKELL, file, name)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=name,
                language=Language.HASKELL,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=None,
                is_exported=not name.startswith("_"),
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )

    def _emit_adt(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return

        entity_id = make_entity_id(Language.HASKELL, file, name)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.CLASS,
                name=name,
                qualified_name=name,
                language=Language.HASKELL,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=f"data {name}",
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )

    def _emit_class(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        # class_head.class_name (field="class") holds the typeclass name.
        head = None
        for c in node.children:
            if c.type == "class_head":
                head = c
                break
        if head is None:
            return
        class_name_node = head.child_by_field_name("class")
        name = _text(class_name_node, source)
        if not name:
            return

        entity_id = make_entity_id(Language.HASKELL, file, name)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.INTERFACE,
                name=name,
                qualified_name=name,
                language=Language.HASKELL,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=f"class {name}",
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )


def _module_entity(
    lang: Language, entity_id: str, name: str, file: str, root: Node, source: str
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
