# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""Scala parser — tree-sitter walk → UIREntity stream.

Emits one Module entity per file plus Class / Interface (trait) / Function /
Method per top-level and class-scoped declarations.

- `class_definition`    → CLASS
- `object_definition`   → CLASS (singleton / companion object)
- `trait_definition`    → INTERFACE
- `function_definition` → FUNCTION or METHOD (inside a template body)
- `function_declaration`→ abstract METHOD (inside trait body, no body)
- `import_declaration`  → provisional `scala:?:<path>` edges
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

# Node types that hold members inside a Scala type body.
_MEMBER_DEF_TYPES = ("function_definition", "function_declaration")
_TYPE_DEF_TYPES = ("class_definition", "object_definition", "trait_definition")


class ScalaParser:
    """Tree-sitter Scala parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.SCALA
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("scala")
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

        module_name = _stem(rel_path, ".scala")
        module_id = make_entity_id(Language.SCALA, rel_path, module_name)
        entities.append(
            _module_entity(Language.SCALA, module_id, module_name, rel_path, root, source)
        )

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        for child in root.children:
            kind = child.type
            if kind in _TYPE_DEF_TYPES:
                self._emit_type(child, source_bytes, rel_path, module_id, entities)
            elif kind in _MEMBER_DEF_TYPES:
                self._emit_fn(child, source_bytes, rel_path, module_id, None, entities)
            elif kind == "import_declaration":
                self._emit_import(child, source_bytes, module_id, edges)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------

    def _emit_type(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return None

        entity_type = EntityType.INTERFACE if node.type == "trait_definition" else EntityType.CLASS
        kw = {
            "class_definition": "class",
            "object_definition": "object",
            "trait_definition": "trait",
        }.get(node.type, "class")
        qname = name
        entity_id = make_entity_id(Language.SCALA, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=qname,
                language=Language.SCALA,
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
                if member.type in _MEMBER_DEF_TYPES:
                    self._emit_fn(member, source, file, entity_id, name, entities)

        return entity_id

    def _emit_fn(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        class_name: str | None,
        entities: list[UIREntity],
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return

        qname = f"{class_name}.{name}" if class_name else name
        entity_type = EntityType.METHOD if class_name else EntityType.FUNCTION
        entity_id = make_entity_id(Language.SCALA, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=qname,
                language=Language.SCALA,
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
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )

    def _emit_import(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        line = node.start_point[0] + 1
        # Gather path segments from the import_declaration children.
        parts: list[str] = []
        for c in node.children:
            if c.type == "identifier":
                parts.append(_text(c, source) or "")
        if parts:
            edges.append(
                Edge(
                    src_id=module_id, dst_id=f"scala:?:{'.'.join(parts)}", type="imports", line=line
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


def _sig_before_body(node: Node, source: bytes) -> str | None:
    body = node.child_by_field_name("body")
    if body is None:
        return None
    raw = source[node.start_byte : body.start_byte].decode("utf-8", errors="replace")
    return raw.strip()
