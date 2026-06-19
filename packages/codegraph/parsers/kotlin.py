# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""Kotlin parser — tree-sitter walk → UIREntity stream.

Emits one Module entity per file plus Class / Interface / Function / Method
per top-level or class-scoped declaration.

- `class_declaration` (kind=class/object) → CLASS
- `class_declaration` (kind=interface)     → INTERFACE
- `function_declaration` at top level      → FUNCTION
- `function_declaration` inside class_body → METHOD (qualified as ClassName.method)
- `import_list`/`import_header`            → provisional `kt:?:<path>` edges
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


class KotlinParser:
    """Tree-sitter Kotlin parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.KOTLIN
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("kotlin")
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

        module_name = _stem(rel_path, ".kt", ".kts")
        module_id = make_entity_id(Language.KOTLIN, rel_path, module_name)
        entities.append(_module_entity(Language.KOTLIN, module_id, module_name, rel_path, root, source))

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        for child in root.children:
            kind = child.type
            if kind == "class_declaration":
                self._emit_class(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "function_declaration":
                self._emit_function(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "import_list":
                self._emit_imports(child, source_bytes, module_id, edges)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------

    def _emit_class(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return None

        kind_node = node.child_by_field_name("kind")
        kind_text = _text(kind_node, source) or "class"
        entity_type = EntityType.INTERFACE if kind_text == "interface" else EntityType.CLASS

        qname = name
        entity_id = make_entity_id(Language.KOTLIN, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=qname,
                language=Language.KOTLIN,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=f"{kind_text} {name}",
                is_exported=not name.startswith("_"),
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            for member in body.children:
                if member.type == "function_declaration":
                    self._emit_method(member, source, file, entity_id, name, entities, edges)

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
        name = _text(name_node, source)
        if not name:
            return None

        qname = name
        entity_id = make_entity_id(Language.KOTLIN, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=qname,
                language=Language.KOTLIN,
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
        return entity_id

    def _emit_method(
        self,
        node: Node,
        source: bytes,
        file: str,
        class_id: str,
        class_name: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return

        qname = f"{class_name}.{name}"
        entity_id = make_entity_id(Language.KOTLIN, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=Language.KOTLIN,
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

    def _emit_imports(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        for child in node.children:
            if child.type != "import_header":
                continue
            line = child.start_point[0] + 1
            for c in child.children:
                if c.type == "identifier":
                    path = _text(c, source) or ""
                    # Check if wildcard follows
                    idx = child.children.index(c)
                    rest = child.children[idx + 1 :]
                    star = any(r.type == ".*" for r in rest)
                    dst = f"kt:?:{path}.*" if star else f"kt:?:{path}"
                    edges.append(Edge(src_id=module_id, dst_id=dst, type="imports", line=line))
                    break


# ------------------------------------------------------------------
# Shared helpers


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
