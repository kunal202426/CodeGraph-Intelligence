# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""CSS parser — tree-sitter walk → UIREntity stream.

Emits one Module entity per file plus named entities for CSS rule sets and
keyframes animations.

- `rule_set` (selector + block)  → FUNCTION (named by selector text)
- `keyframes_statement`          → FUNCTION (named by animation name)
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

_MAX_SELECTOR_LEN = 80


class CSSParser:
    """Tree-sitter CSS parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.CSS
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("css")
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

        module_name = _stem(rel_path, ".css")
        module_id = make_entity_id(Language.CSS, rel_path, module_name)
        entities.append(_module_entity(module_id, module_name, rel_path, root, source))

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        self._walk(root.children, source_bytes, rel_path, module_id, entities)

        return ParseResult(entities=entities, edges=edges, errors=errors)

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
            if kind == "rule_set":
                self._emit_rule(node, source, file, parent_id, entities)
            elif kind == "keyframes_statement":
                self._emit_keyframes(node, source, file, parent_id, entities)
            elif kind == "media_statement":
                # Walk nested rules inside @media blocks
                block = next((c for c in node.children if c.type == "block"), None)
                if block is not None:
                    self._walk(block.children, source, file, parent_id, entities)

    def _emit_rule(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        sel_node = next((c for c in node.children if c.type == "selectors"), None)
        if sel_node is None:
            return
        raw_sel = _text(sel_node, source) or ""
        name = raw_sel.strip()[:_MAX_SELECTOR_LEN]
        if not name:
            return

        entity_id = make_entity_id(Language.CSS, file, name)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=name,
                language=Language.CSS,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=name,
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )

    def _emit_keyframes(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        name_node = next((c for c in node.children if c.type == "keyframes_name"), None)
        name = _text(name_node, source)
        if not name:
            return

        entity_id = make_entity_id(Language.CSS, file, f"@keyframes {name}")
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=f"@keyframes {name}",
                language=Language.CSS,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=f"@keyframes {name}",
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
        language=Language.CSS,
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
