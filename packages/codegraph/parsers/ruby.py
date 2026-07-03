# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Ruby parser — tree-sitter recursive walk → UIREntity stream.

Emits one Module entity per file plus Function / Method / Class per
top-level declaration.

- Top-level `def`              → FUNCTION
- Top-level `class`            → CLASS
- Top-level `module`           → CLASS  (Ruby modules are namespace / mixin)
- `def` inside `class`/`module` body → METHOD, qualified as "Owner.method_name"
- `def self.foo` (singleton_method)  → METHOD, qualified as "Owner.foo"

`require` / `require_relative` calls are emitted as provisional `rb:?:<path>`
import edges; other call expressions as `rb:?call:<callee>`, resolved in T10.7.

Visibility tracking: a bare `private` (or `protected`) identifier in a class
body flips subsequent instance methods to `is_exported=False`; `public` resets
it.  Singleton methods (`def self.foo`) are always public.
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
from codegraph.resolution.frameworks.rails import extract_route_edges
from codegraph.resolution.receiver_types.ruby import (
    infer_local_types,
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

_REQUIRE_METHODS = frozenset({"require", "require_relative"})


class RubyParser:
    """Tree-sitter Ruby parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.RUBY
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("ruby")
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
        module_id = make_entity_id(Language.RUBY, rel_path, module_name)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_name,
                language=Language.RUBY,
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
            if kind in ("class", "module"):
                self._emit_class_or_module(
                    child, source_bytes, rel_path, module_id, entities, edges
                )
            elif kind == "method":
                self._emit_function(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "call":
                self._emit_require(child, source_bytes, module_id, edges)

        entities_by_name = {e.name: e.entity_id for e in entities}
        edges.extend(extract_route_edges(root, source_bytes, entities_by_name))

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------
    # Entity emitters

    def _emit_class_or_module(
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

        entity_id = make_entity_id(Language.RUBY, file, name)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        kind_word = node.type  # "class" or "module"
        signature = self._sig_before_body(node, source) or f"{kind_word} {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.CLASS,
                name=name,
                qualified_name=name,
                language=Language.RUBY,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            self_attr_types = infer_self_attr_types(body, source)
            is_private = False
            for child in body.children:
                if child.type == "identifier":
                    text = self._text(child, source)
                    if text == "private":
                        is_private = True
                    elif text == "public":
                        is_private = False
                elif child.type == "method":
                    self._emit_method(
                        child,
                        source,
                        file,
                        name,
                        entity_id,
                        entities,
                        edges,
                        is_exported=not is_private,
                        self_attr_types=self_attr_types,
                    )
                elif child.type == "singleton_method":
                    # def self.foo — always public
                    self._emit_method(
                        child,
                        source,
                        file,
                        name,
                        entity_id,
                        entities,
                        edges,
                        is_exported=True,
                        self_attr_types=self_attr_types,
                    )

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
        name = self._text(name_node, source)
        if not name:
            return None

        entity_id = make_entity_id(Language.RUBY, file, name)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source) or f"def {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=name,
                language=Language.RUBY,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw_source,
                docstring=None,
                signature=signature,
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            local_types = infer_local_types(body, source)
            self._emit_calls(
                body,
                source,
                src_id=entity_id,
                edges=edges,
                class_name=None,
                local_types=local_types,
                self_attr_types={},
            )

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
        is_exported: bool,
        self_attr_types: dict[str, str] | None = None,
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        qname = f"{owner_name}.{name}"
        entity_id = make_entity_id(Language.RUBY, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source) or f"def {name}"

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=Language.RUBY,
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
            local_types = infer_local_types(body, source)
            self._emit_calls(
                body,
                source,
                src_id=entity_id,
                edges=edges,
                class_name=owner_name,
                local_types=local_types,
                self_attr_types=self_attr_types or {},
            )

        return entity_id

    # ------------------------------------------------------------------
    # Import extraction — provisional `rb:?:<path>` edges

    def _emit_require(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        method_node = node.child_by_field_name("method")
        if self._text(method_node, source) not in _REQUIRE_METHODS:
            return
        args_node = node.child_by_field_name("arguments")
        path = self._require_path(args_node, source)
        if not path:
            return
        edges.append(
            Edge(
                src_id=module_id,
                dst_id=f"rb:?:{path}",
                type="imports",
                line=node.start_point[0] + 1,
            )
        )

    def _require_path(self, args_node: Node | None, source: bytes) -> str | None:
        if args_node is None:
            return None
        for c in args_node.children:
            if c.type == "string":
                for sub in c.children:
                    if sub.type == "string_content":
                        return self._text(sub, source)
        return None

    # ------------------------------------------------------------------
    # Call edge extraction — provisional `rb:?call:<callee>` edges

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
            callee = self._text(call.child_by_field_name("method"), source)
            if not callee or callee in _REQUIRE_METHODS:
                continue
            receiver_type = receiver_type_for_call(
                call, source, class_name, local_types or {}, self_attr_types or {}
            )
            dst_id = (
                f"rb:?methodcall:{receiver_type}.{callee}"
                if receiver_type
                else f"rb:?call:{callee}"
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
            if child.type == "call":
                yield child
            yield from self._iter_call_nodes(child)

    # ------------------------------------------------------------------
    # Helpers

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
        stem = rel_path.removesuffix(".rb")
        return stem.replace("/", ".")
