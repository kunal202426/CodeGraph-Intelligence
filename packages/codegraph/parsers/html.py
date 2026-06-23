# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""HTML parser — tree-sitter walk → UIREntity stream.

Emits one Module entity per file plus named entities for elements with `id`
attributes, and import edges for `<script src>` references.

- element with id="..." attribute  → FUNCTION (named by id)
- script_element with src="..."   → provisional html:?:<src> import edge
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import ClassVar

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    from tree_sitter import Node, Parser
    from tree_sitter_languages import get_language

from codegraph.parsers._nodes import first_child, node_text
from codegraph.parsers.base import ParseResult
from codegraph.uir import (
    Edge,
    EntityType,
    Language,
    UIREntity,
    hash_source,
    make_entity_id,
)


class HTMLParser:
    """Tree-sitter HTML parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.HTML
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("html")
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

        module_name = _stem(rel_path, ".html", ".htm")
        module_id = make_entity_id(Language.HTML, rel_path, module_name)
        entities.append(_module_entity(module_id, module_name, rel_path, root, source))

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        self._walk(root.children, source_bytes, rel_path, module_id, entities, edges)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    def _walk(
        self,
        nodes: list[Node],
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> None:
        for node in nodes:
            kind = node.type
            if kind == "element":
                self._handle_element(node, source, file, parent_id, entities, edges)
            elif kind == "script_element":
                self._handle_script(node, source, file, parent_id, edges)

    def _handle_element(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> None:
        start_tag = first_child(node, "start_tag")
        if start_tag is None:
            return

        tag_name = node_text(first_child(start_tag, "tag_name"), source)

        if tag_name == "link":
            href = _attr_value(start_tag, "href", source)
            rel = _attr_value(start_tag, "rel", source)
            if href and rel == "stylesheet":
                line = node.start_point[0] + 1
                edges.append(
                    Edge(
                        src_id=parent_id,
                        dst_id=f"html:?:{href}",
                        type="imports",
                        line=line,
                    )
                )

        elem_id = _attr_value(start_tag, "id", source)
        if elem_id:
            entity_id = make_entity_id(Language.HTML, file, elem_id)
            raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            entities.append(
                UIREntity(
                    entity_id=entity_id,
                    type=EntityType.FUNCTION,
                    name=elem_id,
                    qualified_name=elem_id,
                    language=Language.HTML,
                    file=file,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                    raw_source=raw,
                    docstring=None,
                    signature=f'<{tag_name} id="{elem_id}">',
                    is_exported=True,
                    is_async=False,
                    parent_id=parent_id,
                    hash=hash_source(raw),
                )
            )
            self._walk(node.children, source, file, entity_id, entities, edges)
        else:
            self._walk(node.children, source, file, parent_id, entities, edges)

    def _handle_script(
        self,
        node: Node,
        source: bytes,
        file: str,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        start_tag = first_child(node, "start_tag")
        if start_tag is None:
            return
        src = _attr_value(start_tag, "src", source)
        if src:
            line = node.start_point[0] + 1
            edges.append(
                Edge(
                    src_id=module_id,
                    dst_id=f"html:?:{src}",
                    type="imports",
                    line=line,
                )
            )


def _attr_value(start_tag: Node, attr_name: str, source: bytes) -> str | None:
    for child in start_tag.children:
        if child.type != "attribute":
            continue
        if node_text(first_child(child, "attribute_name"), source) != attr_name:
            continue
        qv = first_child(child, "quoted_attribute_value")
        if qv is None:
            continue
        return node_text(first_child(qv, "attribute_value"), source)
    return None


def _module_entity(entity_id: str, name: str, file: str, root: Node, source: str) -> UIREntity:
    return UIREntity(
        entity_id=entity_id,
        type=EntityType.MODULE,
        name=name,
        qualified_name=name,
        language=Language.HTML,
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
