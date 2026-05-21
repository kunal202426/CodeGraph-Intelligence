"""TypeScript / TSX / JS / JSX parser via tree-sitter.

Picks the right tree-sitter grammar per file extension:

    .ts        → typescript     Language.TYPESCRIPT
    .tsx       → tsx            Language.TYPESCRIPT
    .js / .mjs / .cjs → javascript  Language.JAVASCRIPT
    .jsx       → tsx            Language.JAVASCRIPT  (tsx grammar handles JSX)

Emits Module per file plus Function, Class, Method, Interface entities.
`export ...` declarations mark the inner entity as `is_exported=True`; plain
declarations without `export` are `is_exported=False`. Arrow functions are
captured only when assigned to a `const`/`let` at module or class scope —
expression-level arrows are out of MVP scope.

Imports / call edges land in T2.5 + T4.2 respectively.
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

_GRAMMAR_BY_EXT: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "tsx",  # tsx grammar handles plain JSX too
}
_LANG_BY_EXT: dict[str, Language] = {
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
}


class TypeScriptParser:
    """Tree-sitter TS/TSX/JS parser. Stateless; safe to reuse across files.

    Note: `language` is the *default* declared by IParser; the actual Language
    used for emitted entities is decided per-file from the extension.
    """

    language: ClassVar[Language] = Language.TYPESCRIPT
    _parsers: ClassVar[dict[str, Parser]] = {}

    @classmethod
    def _ts_parser(cls, grammar: str) -> Parser:
        if grammar not in cls._parsers:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language(grammar)
            p = Parser()
            p.set_language(lang)
            cls._parsers[grammar] = p
        return cls._parsers[grammar]

    # ------------------------------------------------------------------
    # Public API

    def parse(self, path: Path, source: str) -> ParseResult:
        rel_path = str(path).replace("\\", "/")
        ext = Path(rel_path).suffix.lower()
        grammar = _GRAMMAR_BY_EXT.get(ext, "typescript")
        emit_lang = _LANG_BY_EXT.get(ext, Language.TYPESCRIPT)

        source_bytes = source.encode("utf-8")
        tree = self._ts_parser(grammar).parse(source_bytes)
        root = tree.root_node

        entities: list[UIREntity] = []
        edges: list[Edge] = []
        errors: list[str] = []

        # Module entity for the whole file.
        module_name = self._module_name_from_path(rel_path)
        module_id = make_entity_id(emit_lang, rel_path, module_name)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_name,
                language=emit_lang,
                file=rel_path,
                start_line=1,
                end_line=max(root.end_point[0] + 1, 1),
                start_col=0,
                end_col=root.end_point[1],
                raw_source=source,
                docstring=None,  # TS uses /** */ JSDoc — extracted in a later pass if needed
                signature=None,
                is_exported=True,
                is_async=False,
                parent_id=None,
                hash=hash_source(source),
            )
        )

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        self._walk(
            root,
            source_bytes,
            rel_path,
            scope=[],
            parent_id=module_id,
            module_id=module_id,
            emit_lang=emit_lang,
            entities=entities,
            edges=edges,
            is_exported=False,
        )

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------
    # Tree walk

    def _walk(
        self,
        node: Node,
        source: bytes,
        file: str,
        *,
        scope: list[str],
        parent_id: str | None,
        module_id: str,
        emit_lang: Language,
        entities: list[UIREntity],
        edges: list[Edge],
        is_exported: bool,
    ) -> None:
        """Walk top-level children. Recurses into class bodies + statement blocks."""
        for child in node.children:
            kind = child.type

            if kind == "export_statement":
                inner = child.child_by_field_name("declaration")
                if inner is None:
                    continue
                # Emit using the inner node for type/name but the export_statement's
                # span (so raw_source includes "export").
                self._handle_declaration(
                    inner,
                    span_node=child,
                    source=source,
                    file=file,
                    scope=scope,
                    parent_id=parent_id,
                    module_id=module_id,
                    emit_lang=emit_lang,
                    entities=entities,
                    edges=edges,
                    is_exported=True,
                )

            elif kind in (
                "function_declaration",
                "class_declaration",
                "interface_declaration",
                "lexical_declaration",
                "variable_declaration",  # `var x = ...`
            ):
                self._handle_declaration(
                    child,
                    span_node=child,
                    source=source,
                    file=file,
                    scope=scope,
                    parent_id=parent_id,
                    module_id=module_id,
                    emit_lang=emit_lang,
                    entities=entities,
                    edges=edges,
                    is_exported=is_exported,
                )

            elif kind == "class_body":
                self._walk_class_body(
                    child,
                    source,
                    file,
                    scope=scope,
                    parent_id=parent_id,
                    module_id=module_id,
                    emit_lang=emit_lang,
                    entities=entities,
                    edges=edges,
                )

            elif kind == "statement_block":
                # Don't descend into function bodies (we don't emit nested funcs).
                continue

    def _handle_declaration(
        self,
        decl: Node,
        *,
        span_node: Node,
        source: bytes,
        file: str,
        scope: list[str],
        parent_id: str | None,
        module_id: str,
        emit_lang: Language,
        entities: list[UIREntity],
        edges: list[Edge],
        is_exported: bool,
    ) -> None:
        kind = decl.type

        if kind == "function_declaration":
            self._emit_decl(
                decl=decl,
                span_node=span_node,
                source=source,
                file=file,
                scope=scope,
                parent_id=parent_id,
                emit_lang=emit_lang,
                entities=entities,
                entity_type=EntityType.METHOD if scope else EntityType.FUNCTION,
                is_async=any(c.type == "async" for c in decl.children),
                is_exported=is_exported,
            )

        elif kind == "class_declaration":
            class_id = self._emit_decl(
                decl=decl,
                span_node=span_node,
                source=source,
                file=file,
                scope=scope,
                parent_id=parent_id,
                emit_lang=emit_lang,
                entities=entities,
                entity_type=EntityType.CLASS,
                is_async=False,
                is_exported=is_exported,
            )
            if class_id is not None:
                self._descend_into_class(
                    decl,
                    source,
                    file,
                    scope,
                    class_id,
                    module_id,
                    emit_lang,
                    entities,
                    edges,
                )

        elif kind == "interface_declaration":
            self._emit_decl(
                decl=decl,
                span_node=span_node,
                source=source,
                file=file,
                scope=scope,
                parent_id=parent_id,
                emit_lang=emit_lang,
                entities=entities,
                entity_type=EntityType.INTERFACE,
                is_async=False,
                is_exported=is_exported,
            )

        elif kind in ("lexical_declaration", "variable_declaration"):
            # `const f = (x) => ...` — emit one Function per declarator with an arrow.
            for vd in decl.children:
                if vd.type != "variable_declarator":
                    continue
                value = vd.child_by_field_name("value")
                if value is None or value.type != "arrow_function":
                    continue
                self._emit_decl(
                    decl=vd,
                    span_node=span_node,  # whole `[export] const ... = ... =>` span
                    source=source,
                    file=file,
                    scope=scope,
                    parent_id=parent_id,
                    emit_lang=emit_lang,
                    entities=entities,
                    entity_type=EntityType.METHOD if scope else EntityType.FUNCTION,
                    is_async=any(c.type == "async" for c in value.children),
                    is_exported=is_exported,
                )

    def _walk_class_body(
        self,
        body: Node,
        source: bytes,
        file: str,
        *,
        scope: list[str],
        parent_id: str | None,
        module_id: str,
        emit_lang: Language,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> None:
        for child in body.children:
            if child.type == "method_definition":
                self._emit_decl(
                    decl=child,
                    span_node=child,
                    source=source,
                    file=file,
                    scope=scope,
                    parent_id=parent_id,
                    emit_lang=emit_lang,
                    entities=entities,
                    entity_type=EntityType.METHOD,
                    is_async=any(c.type == "async" for c in child.children),
                    is_exported=True,  # all instance/static methods are part of the class API
                )

    def _descend_into_class(
        self,
        class_decl: Node,
        source: bytes,
        file: str,
        scope: list[str],
        class_entity_id: str,
        module_id: str,
        emit_lang: Language,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> None:
        name_node = class_decl.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return
        body = class_decl.child_by_field_name("body")
        if body is None:
            return
        self._walk_class_body(
            body,
            source,
            file,
            scope=[*scope, name],
            parent_id=class_entity_id,
            module_id=module_id,
            emit_lang=emit_lang,
            entities=entities,
            edges=edges,
        )

    # ------------------------------------------------------------------
    # Emit

    def _emit_decl(
        self,
        *,
        decl: Node,
        span_node: Node,
        source: bytes,
        file: str,
        scope: list[str],
        parent_id: str | None,
        emit_lang: Language,
        entities: list[UIREntity],
        entity_type: EntityType,
        is_async: bool,
        is_exported: bool,
    ) -> str | None:
        name_node = decl.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = ".".join([*scope, name]) if scope else name
        raw_source = source[span_node.start_byte : span_node.end_byte].decode(
            "utf-8", errors="replace"
        )
        signature = self._signature(decl, source)
        entity_id = make_entity_id(emit_lang, file, qname)

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=qname,
                language=emit_lang,
                file=file,
                start_line=span_node.start_point[0] + 1,
                end_line=span_node.end_point[0] + 1,
                start_col=span_node.start_point[1],
                end_col=span_node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=is_exported,
                is_async=is_async,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )
        return entity_id

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _text(node: Node | None, source: bytes) -> str | None:
        if node is None:
            return None
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _signature(decl: Node, source: bytes) -> str | None:
        """Signature = source from the declaration start up to (but not including) the body."""
        body = decl.child_by_field_name("body")
        if body is None:
            # arrow_function decls don't have a `body` field directly; use the value node
            value = decl.child_by_field_name("value")
            if value is None or value.type != "arrow_function":
                return None
            arrow_body = value.child_by_field_name("body")
            if arrow_body is None:
                return None
            raw = source[decl.start_byte : arrow_body.start_byte].decode("utf-8", errors="replace")
            return raw.rstrip().rstrip("=>").rstrip()
        raw = source[decl.start_byte : body.start_byte].decode("utf-8", errors="replace")
        return raw.rstrip()

    @staticmethod
    def _module_name_from_path(rel_path: str) -> str:
        stem = rel_path
        for ext in (".tsx", ".ts", ".jsx", ".mjs", ".cjs", ".js"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break
        return stem.replace("/", ".")
