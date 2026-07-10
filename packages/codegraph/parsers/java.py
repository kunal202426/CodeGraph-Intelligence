# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Java parser — tree-sitter recursive walk → UIREntity stream.

Emits one Module entity per file plus Method / Class (class, enum) /
Interface per top-level type declaration.

- Top-level `class` / `enum` → CLASS
- Top-level `interface`      → INTERFACE
- `method_declaration` / `constructor_declaration` inside a class or interface
  body → METHOD, qualified as "ClassName.method_name"

Import edges (`import_declaration`) are emitted as provisional `java:?:<path>`
and call edges as `java:?call:<callee>`, resolved in T10.7.
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
from codegraph.resolution.frameworks.spring import (
    extract_class_base_path,
    extract_route_edges,
)
from codegraph.resolution.inheritance.java import (
    extract_base_classes as extract_java_base_classes,
)
from codegraph.resolution.receiver_types.java import (
    infer_local_types,
    infer_param_types,
    infer_self_attr_types,
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


class JavaParser:
    """Tree-sitter Java parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.JAVA
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("java")
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
        module_id = make_entity_id(Language.JAVA, rel_path, module_name)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_name,
                language=Language.JAVA,
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
            if kind in ("class_declaration", "enum_declaration"):
                self._emit_class(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "interface_declaration":
                self._emit_interface(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "import_declaration":
                self._emit_import(child, source_bytes, module_id, edges)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------
    # Entity emitters

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
        name = self._text(name_node, source)
        if not name:
            return None

        qname = name
        entity_id = make_entity_id(Language.JAVA, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_exported = self._is_pub(node)
        signature = self._sig_before_body(node, source)
        kind_word = "class" if node.type == "class_declaration" else "enum"
        if signature is None:
            signature = f"{kind_word} {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.CLASS,
                name=name,
                qualified_name=qname,
                language=Language.JAVA,
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
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        for i, base_name in enumerate(extract_java_base_classes(node, source)):
            edges.append(
                Edge(
                    src_id=entity_id,
                    dst_id=f"java:?inherits:{i}:{base_name}",
                    type="inherits",
                    line=node.start_point[0] + 1,
                )
            )

        body = node.child_by_field_name("body")
        if body is not None:
            base_path = extract_class_base_path(node, source)
            self_attr_types = infer_self_attr_types(body, source)
            for child in body.children:
                if child.type in ("method_declaration", "constructor_declaration"):
                    self._emit_method(
                        child,
                        source,
                        file,
                        name,
                        entity_id,
                        entities,
                        edges,
                        base_path=base_path,
                        self_attr_types=self_attr_types,
                    )
                elif child.type == "field_declaration":
                    # A field initializer (`private final X x = new X();`) runs
                    # as part of every instance's construction, but there's no
                    # per-field entity to attribute a call to -- the class
                    # itself is the natural owner. Without this, a class only
                    # ever instantiated via a field initializer (a very common
                    # Java pattern) is invisible to every caller, the same way
                    # an unscanned method body would be.
                    self._emit_calls(
                        child,
                        source,
                        src_id=entity_id,
                        edges=edges,
                        class_name=name,
                        local_types={},
                        self_attr_types=self_attr_types,
                    )

        return entity_id

    def _emit_interface(
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

        qname = name
        entity_id = make_entity_id(Language.JAVA, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_exported = self._is_pub(node)
        signature = self._sig_before_body(node, source)
        if signature is None:
            signature = f"interface {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.INTERFACE,
                name=name,
                qualified_name=qname,
                language=Language.JAVA,
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

    def _emit_method(
        self,
        node: Node,
        source: bytes,
        file: str,
        owner_name: str,
        owner_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
        *,
        base_path: str = "",
        self_attr_types: dict[str, str] | None = None,
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = f"{owner_name}.{name}"
        entity_id = make_entity_id(Language.JAVA, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_exported = self._is_pub(node)
        signature = self._sig_before_body(node, source) or raw_source.strip().rstrip(";").strip()

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=Language.JAVA,
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
            local_types = infer_param_types(node, source)
            local_types.update(infer_local_types(body, source))
            self._emit_calls(
                body,
                source,
                src_id=entity_id,
                edges=edges,
                class_name=owner_name,
                local_types=local_types,
                self_attr_types=self_attr_types or {},
            )

        edges.extend(extract_route_edges(node, entity_id, source, base_path))

        return entity_id

    # ------------------------------------------------------------------
    # Import extraction — provisional `java:?:<path>` edges

    def _emit_import(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        line = node.start_point[0] + 1
        named = [c for c in node.children if c.is_named]
        path_text: str | None = None
        is_wildcard = False
        for c in named:
            if c.type in ("scoped_identifier", "identifier"):
                path_text = self._text(c, source)
            elif c.type == "asterisk":
                is_wildcard = True
        if not path_text:
            return
        dst = f"{path_text}.*" if is_wildcard else path_text
        edges.append(
            Edge(
                src_id=module_id,
                dst_id=f"java:?:{dst}",
                type="imports",
                line=line,
            )
        )

    # ------------------------------------------------------------------
    # Call edge extraction — provisional `java:?call:<callee>` edges

    def _emit_calls(
        self,
        body: Node,
        source: bytes,
        *,
        src_id: str,
        edges: list[Edge],
        class_name: str | None = None,
        local_types: dict[str, str] | None = None,
        self_attr_types: dict[str, str] | None = None,
    ) -> None:
        for call in self._iter_call_nodes(body):
            callee = self._callee_name(call, source)
            if not callee:
                continue
            receiver_type = receiver_type_for_call(
                call, source, class_name, local_types or {}, self_attr_types or {}
            )
            dst_id = (
                f"java:?methodcall:{receiver_type}.{callee}"
                if receiver_type
                else f"java:?call:{callee}"
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
        for child in node.children:
            # `new Foo(...)` constructs `Foo` -- a semantic call to its
            # constructor -- but is a structurally different node from a
            # `method_invocation`, so without this a class only ever
            # instantiated via `new` (never called as a method) looks like
            # dead code with zero callers.
            if child.type in ("method_invocation", "object_creation_expression"):
                yield child
            yield from self._iter_call_nodes(child)

    @staticmethod
    def _callee_name(call_node: Node, source: bytes) -> str | None:
        if call_node.type == "object_creation_expression":
            type_node = call_node.child_by_field_name("type")
            if type_node is None:
                return None
            # A parameterized type (`new HashMap<>()`) is `generic_type`
            # wrapping the real class name as its first (unnamed-field)
            # child; a plain `new Foo()` is the identifier directly.
            if type_node.type == "generic_type":
                if not type_node.children:
                    return None
                type_node = type_node.children[0]
            return source[type_node.start_byte : type_node.end_byte].decode(
                "utf-8", errors="replace"
            )

        name_node = call_node.child_by_field_name("name")
        if name_node is not None:
            return source[name_node.start_byte : name_node.end_byte].decode(
                "utf-8", errors="replace"
            )
        return None

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _is_pub(node: Node) -> bool:
        for c in node.children:
            if c.type == "modifiers":
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
        stem = rel_path.removesuffix(".java")
        return stem.replace("/", ".")
