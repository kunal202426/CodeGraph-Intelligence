# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
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

Import edges land in T2.5; call edges in T4.2 — both emit provisional
dst_ids the resolver closes.
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
from codegraph.resolution.frameworks.express import extract_route_edges
from codegraph.resolution.frameworks.http_client import extract_http_edges
from codegraph.resolution.inheritance.typescript import extract_base_classes
from codegraph.resolution.receiver_types.typescript import (
    infer_local_types,
    infer_param_types,
    infer_self_attr_types,
    params_source_node,
    receiver_type_for_call,
)
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
            self_attr_types={},
        )

        entities_by_name = {e.name: e.entity_id for e in entities}
        edges.extend(extract_route_edges(root, source_bytes, entities_by_name))
        edges.extend(extract_http_edges(root, source_bytes, entities, module_id))

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
        self_attr_types: dict[str, str],
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
                    self_attr_types=self_attr_types,
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
                    self_attr_types=self_attr_types,
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
                    self_attr_types=self_attr_types,
                )

            elif kind == "import_statement" and not scope:
                self._emit_import(child, source, module_id, edges)

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
        self_attr_types: dict[str, str],
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
                edges=edges,
                entity_type=EntityType.METHOD if scope else EntityType.FUNCTION,
                is_async=any(c.type == "async" for c in decl.children),
                is_exported=is_exported,
                self_attr_types=self_attr_types,
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
                edges=edges,
                entity_type=EntityType.CLASS,
                is_async=False,
                is_exported=is_exported,
                self_attr_types=self_attr_types,
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
                edges=edges,
                entity_type=EntityType.INTERFACE,
                is_async=False,
                is_exported=is_exported,
                self_attr_types=self_attr_types,
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
                    edges=edges,
                    entity_type=EntityType.METHOD if scope else EntityType.FUNCTION,
                    is_async=any(c.type == "async" for c in value.children),
                    is_exported=is_exported,
                    self_attr_types=self_attr_types,
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
        self_attr_types: dict[str, str],
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
                    edges=edges,
                    entity_type=EntityType.METHOD,
                    is_async=any(c.type == "async" for c in child.children),
                    is_exported=True,  # all instance/static methods are part of the class API
                    self_attr_types=self_attr_types,
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
            self_attr_types=infer_self_attr_types(body, source),
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
        edges: list[Edge],
        entity_type: EntityType,
        is_async: bool,
        is_exported: bool,
        self_attr_types: dict[str, str],
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

        # Inheritance edges: `class Foo extends Base { ... }`.
        if entity_type == EntityType.CLASS:
            for i, base_name in enumerate(extract_base_classes(decl, source)):
                edges.append(
                    Edge(
                        src_id=entity_id,
                        dst_id=f"ts:?inherits:{i}:{base_name}",
                        type="inherits",
                        line=decl.start_point[0] + 1,
                    )
                )

        # Call edges: scan a function/method/arrow body for call expressions.
        if entity_type in (EntityType.FUNCTION, EntityType.METHOD):
            body = self._call_body_node(decl)
            if body is not None:
                class_name = scope[-1] if scope else None
                local_types = infer_param_types(params_source_node(decl), source)
                local_types.update(infer_local_types(body, source))
                self._emit_calls(
                    body,
                    source,
                    src_id=entity_id,
                    edges=edges,
                    class_name=class_name,
                    local_types=local_types,
                    self_attr_types=self_attr_types,
                )

        return entity_id

    # ------------------------------------------------------------------
    # Call extraction: emit a provisional `calls` edge per call
    # expression in a body. When the receiver's type was inferred (a local
    # variable's `new X()`/annotation, a typed parameter, `this`, or a
    # `this.attr` tracked elsewhere in the class), dst is
    # `ts:?methodcall:<Type>.<name>`; the resolver tries an exact match on
    # that before falling back to plain-name resolution. Otherwise dst is
    # `ts:?call:<name>`, resolved against same-file entities and imports.
    #
    #   foo()           → ts:?call:foo
    #   obj.method()    → ts:?call:method | ts:?methodcall:<Type>.method
    #   a.b.process()   → ts:?call:process

    def _emit_calls(
        self,
        body: Node,
        source: bytes,
        *,
        src_id: str,
        edges: list[Edge],
        class_name: str | None,
        local_types: dict[str, str],
        self_attr_types: dict[str, str],
    ) -> None:
        for call in self._iter_call_nodes(body):
            callee = self._callee_name(call, source)
            if not callee:
                continue
            receiver_type = receiver_type_for_call(
                call, source, class_name, local_types, self_attr_types
            )
            dst_id = (
                f"ts:?methodcall:{receiver_type}.{callee}"
                if receiver_type
                else f"ts:?call:{callee}"
            )
            edges.append(
                Edge(
                    src_id=src_id,
                    dst_id=dst_id,
                    type="calls",
                    line=call.start_point[0] + 1,
                    confidence=0.7,
                )
            )

    def _iter_call_nodes(self, node: Node):
        """Yield every `call_expression` at/under `node` (incl. nested in args),
        plus every JSX tag (`<Foo ... />` or `<Foo>...</Foo>`) -- a JSX tag is
        effectively a call to the component function it names, so treating it
        as one gives React/JSX components real caller edges instead of
        looking like unreachable dead code just because they're never invoked
        as a plain function."""
        if node.type in ("call_expression", "jsx_self_closing_element"):
            yield node
        elif node.type == "jsx_element":
            opening = node.child_by_field_name("open_tag")
            if opening is not None:
                yield opening
        for child in node.children:
            yield from self._iter_call_nodes(child)

    @staticmethod
    def _call_body_node(decl: Node) -> Node | None:
        """Body subtree to scan for calls, by declaration kind.

        function_declaration / method_definition expose a `body` field directly;
        an arrow declarator (`const f = () => ...`) carries its body on the
        arrow_function value (which may be a statement block or a bare expression).
        """
        if decl.type == "variable_declarator":
            value = decl.child_by_field_name("value")
            if value is not None and value.type == "arrow_function":
                return value.child_by_field_name("body")
            return None
        return decl.child_by_field_name("body")

    @staticmethod
    def _callee_name(call_node: Node, source: bytes) -> str | None:
        if call_node.type in ("jsx_opening_element", "jsx_self_closing_element"):
            name = call_node.child_by_field_name("name")
            if name is None:
                return None
            if name.type == "identifier":
                text = source[name.start_byte : name.end_byte].decode("utf-8", errors="replace")
                # JSX convention: a capitalized tag is a component reference
                # (`<ScoreBadge />`); lowercase is a host element (`<div>`),
                # never a call to anything in the indexed codebase.
                return text if text[:1].isupper() else None
            if name.type == "member_expression":
                # The `<Foo.Bar />` production doesn't carry the ordinary
                # expression's "property" field binding, so fall back to the
                # last named child (the `property_identifier`, e.g. `Bar`).
                named = [c for c in name.children if c.is_named]
                if named:
                    prop = named[-1]
                    return source[prop.start_byte : prop.end_byte].decode("utf-8", errors="replace")
            return None

        fn = call_node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return source[fn.start_byte : fn.end_byte].decode("utf-8", errors="replace")
        if fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            if prop is not None:
                return source[prop.start_byte : prop.end_byte].decode("utf-8", errors="replace")
        return None

    # ------------------------------------------------------------------
    # Import extraction: emits provisional dst_ids the resolver closes.
    #
    # Encoding:
    #   import { x }    from "./mod"    → "ts:?:./mod::x"
    #   import { x as y } from "./mod"  → "ts:?:./mod::x"     (target name, not alias)
    #   import x        from "./mod"    → "ts:?:./mod::default"
    #   import * as A   from "./mod"    → "ts:?:./mod::*"
    #   import           "./mod"        → "ts:?:./mod"        (side-effect only)
    #   import { x }    from "react"    → "ts:?:react::x"     (bare; → external)

    def _emit_import(
        self,
        stmt: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        line = stmt.start_point[0] + 1
        source_node = stmt.child_by_field_name("source")
        if source_node is None:
            return
        specifier = self._text(source_node, source)
        if specifier is None:
            return
        # Strip the surrounding quotes from the string literal.
        specifier = specifier.strip().strip('"').strip("'").strip("`")
        if not specifier:
            return

        prefix = f"ts:?:{specifier}"

        clause = None
        for c in stmt.children:
            if c.type == "import_clause":
                clause = c
                break

        if clause is None:
            # Side-effect import: `import "./mod"`. Single edge to the module.
            edges.append(Edge(src_id=module_id, dst_id=prefix, type="imports", line=line))
            return

        for c in clause.children:
            kind = c.type
            if kind == "identifier":
                # Default import: `import auth from "./mod"`.
                edges.append(
                    Edge(src_id=module_id, dst_id=f"{prefix}::default", type="imports", line=line)
                )
            elif kind == "namespace_import":
                # `import * as A from "./mod"`.
                edges.append(
                    Edge(src_id=module_id, dst_id=f"{prefix}::*", type="imports", line=line)
                )
            elif kind == "named_imports":
                # `import { a, b as c } from "./mod"`.
                for spec in c.children:
                    if spec.type != "import_specifier":
                        continue
                    name_node = spec.child_by_field_name("name")
                    name = self._text(name_node, source)
                    if not name:
                        continue
                    edges.append(
                        Edge(
                            src_id=module_id,
                            dst_id=f"{prefix}::{name}",
                            type="imports",
                            line=line,
                        )
                    )

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
