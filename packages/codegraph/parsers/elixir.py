# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
"""Elixir parser — tree-sitter walk → UIREntity stream.

Everything in Elixir is a `call` node. We distinguish:

- `defmodule ModName do ... end` → MODULE entity; functions inside are emitted
  as children of that module.
- `def fn_name(args) do ... end`  → FUNCTION (public)
- `defp fn_name(args) do ... end` → FUNCTION (private, is_exported=False)
- `defmacro ...`                  → FUNCTION

Function names live as the target of the inner call in the first argument of
the def call: `def foo(x) do` → `(call def (arguments (call foo ...)))`.
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

_FN_KEYWORDS = frozenset({"def", "defp", "defmacro", "defmacrop"})


class ElixirParser:
    """Tree-sitter Elixir parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.ELIXIR
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("elixir")
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

        module_name = _stem(rel_path, ".ex", ".exs")
        module_id = make_entity_id(Language.ELIXIR, rel_path, module_name)
        entities.append(
            _module_entity(Language.ELIXIR, module_id, module_name, rel_path, root, source)
        )

        if root.has_error:
            errors.append("tree-sitter reported parse errors (entities still emitted)")

        for child in root.children:
            if child.type == "call":
                self._handle_call(child, source_bytes, rel_path, module_id, entities)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------

    def _handle_call(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        target = node.child_by_field_name("target")
        target_text = _text(target, source) or ""

        if target_text == "defmodule":
            self._emit_defmodule(node, source, file, parent_id, entities)
        elif target_text in _FN_KEYWORDS:
            self._emit_defn(node, source, file, parent_id, target_text, entities)

    def _emit_defmodule(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> str | None:
        # `arguments` and `do_block` have no field name — find by node type.
        args = next((c for c in node.children if c.type == "arguments"), None)
        mod_name: str | None = None
        if args is not None:
            for c in args.children:
                if c.type == "alias":
                    mod_name = _text(c, source)
                    break
        if not mod_name:
            return None

        qname = mod_name
        entity_id = make_entity_id(Language.ELIXIR, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.MODULE,
                name=mod_name,
                qualified_name=qname,
                language=Language.ELIXIR,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=f"defmodule {mod_name}",
                is_exported=True,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )

        # Walk do_block for function definitions.
        do_block = next((c for c in node.children if c.type == "do_block"), None)
        if do_block is not None:
            for c in do_block.children:
                if c.type == "call":
                    self._handle_call(c, source, file, entity_id, entities)

        return entity_id

    def _emit_defn(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        keyword: str,
        entities: list[UIREntity],
    ) -> None:
        fn_name = _elixir_fn_name(node, source)
        if not fn_name:
            return

        is_exported = keyword in ("def", "defmacro")
        qname = fn_name
        entity_id = make_entity_id(Language.ELIXIR, file, qname)
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=fn_name,
                qualified_name=qname,
                language=Language.ELIXIR,
                file=file,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
                raw_source=raw,
                docstring=None,
                signature=f"{keyword} {fn_name}",
                is_exported=is_exported,
                is_async=False,
                parent_id=parent_id,
                hash=hash_source(raw),
            )
        )


def _elixir_fn_name(def_call: Node, source: bytes) -> str | None:
    """Extract function name from `def foo(args)` or `defp foo, do: x` call.

    `arguments` has no field name in the Elixir grammar — find by node type.
    """
    args = next((c for c in def_call.children if c.type == "arguments"), None)
    if args is None:
        return None
    for c in args.children:
        if c.type == "call":
            target = c.child_by_field_name("target")
            if target is not None:
                return _text(target, source)
        elif c.type == "identifier":
            return _text(c, source)
    return None


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
