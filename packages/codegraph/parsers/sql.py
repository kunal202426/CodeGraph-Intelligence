# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""SQL parser — tree-sitter walk → UIREntity stream.

Emits one Module entity per file plus Class / Function per DDL statements.

- `create_table_statement`    → CLASS (table name)
- `create_view_statement`     → CLASS (view name)
- `create_function_statement` → FUNCTION (function name)
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


class SQLParser:
    """Tree-sitter SQL parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.SQL
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("sql")
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

        module_name = _stem(rel_path, ".sql")
        module_id = make_entity_id(Language.SQL, rel_path, module_name)
        entities.append(_module_entity(module_id, module_name, rel_path, root, source))

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        for child in root.children:
            kind = child.type
            if kind in ("create_table_statement", "create_view_statement"):
                self._emit_ddl(child, source_bytes, rel_path, module_id, EntityType.CLASS, entities)
            elif kind == "create_function_statement":
                self._emit_ddl(
                    child, source_bytes, rel_path, module_id, EntityType.FUNCTION, entities
                )

        return ParseResult(entities=entities, edges=edges, errors=errors)

    def _emit_ddl(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entity_type: EntityType,
        entities: list[UIREntity],
    ) -> None:
        # Name is the first `identifier` direct child (after CREATE TABLE/VIEW/FUNCTION keywords).
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        name = _text(name_node, source)
        if not name:
            return

        entity_id = make_entity_id(Language.SQL, file, name)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=name,
                language=Language.SQL,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=None,
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )


def _module_entity(entity_id: str, name: str, file: str, root: Node, source: str) -> UIREntity:
    return UIREntity(
        entity_id=entity_id,
        type=EntityType.MODULE,
        name=name,
        qualified_name=name,
        language=Language.SQL,
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
