# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Python parser — tree-sitter recursive walk → UIREntity stream.

Emits one Module entity per file plus one Function/Class/Method per top-level
or class-scoped definition. Nested functions inside other functions are NOT
emitted (deliberate MVP scope; revisit in Phase 2+).

The accompanying `queries/python.scm` documents the node types we care about
but is not executed — recursive walk is cleaner for tracking the parent-class
scope chain that drives qualified-name and parent_id.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path
from typing import ClassVar

# tree-sitter-languages internally calls a deprecated tree-sitter API; the
# FutureWarning is noisy and unactionable until upstream migrates.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    from tree_sitter import Node, Parser
    from tree_sitter_languages import get_language

from codegraph.parsers.base import ParseResult
from codegraph.resolution.frameworks.django_urls import (
    extract_route_edges as extract_django_route_edges,
)
from codegraph.resolution.frameworks.python_web import extract_route_edges
from codegraph.resolution.inheritance.python import extract_base_classes
from codegraph.resolution.receiver_types.python import (
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


class PythonParser:
    """Tree-sitter Python parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.PYTHON
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("python")
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

        # Module entity for the whole file.
        module_name = self._module_name_from_path(rel_path)
        module_qname = module_name
        module_id = make_entity_id(Language.PYTHON, rel_path, module_qname)
        module_docstring = self._docstring_from_block(root, source_bytes)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_qname,
                language=Language.PYTHON,
                file=rel_path,
                start_line=1,
                end_line=max(root.end_point[0] + 1, 1),
                start_col=0,
                end_col=root.end_point[1],
                raw_source=source,
                docstring=module_docstring,
                signature=None,
                is_exported=not module_name.startswith("_"),
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
            entities=entities,
            edges=edges,
            self_attr_types={},
        )

        entities_by_name = {e.name: e.entity_id for e in entities}
        edges.extend(extract_django_route_edges(root, source_bytes, entities_by_name))

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
        entities: list[UIREntity],
        edges: list[Edge],
        self_attr_types: dict[str, str],
    ) -> None:
        """Walk top-level children. Descends into class bodies only — not function bodies."""
        for child in node.children:
            kind = child.type
            if kind == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner is None:
                    continue
                emitted_id = self._emit(
                    inner_def=inner,
                    span_node=child,
                    source=source,
                    file=file,
                    scope=scope,
                    parent_id=parent_id,
                    entities=entities,
                    edges=edges,
                    self_attr_types=self_attr_types,
                )
                if inner.type == "function_definition" and emitted_id is not None:
                    edges.extend(extract_route_edges(child, emitted_id, source))
                if inner.type == "class_definition" and emitted_id is not None:
                    self._descend_into_class(
                        inner, source, file, scope, emitted_id, module_id, entities, edges
                    )
            elif kind == "class_definition":
                emitted_id = self._emit(
                    inner_def=child,
                    span_node=child,
                    source=source,
                    file=file,
                    scope=scope,
                    parent_id=parent_id,
                    entities=entities,
                    edges=edges,
                    self_attr_types=self_attr_types,
                )
                if emitted_id is not None:
                    self._descend_into_class(
                        child, source, file, scope, emitted_id, module_id, entities, edges
                    )
            elif kind == "function_definition":
                self._emit(
                    inner_def=child,
                    span_node=child,
                    source=source,
                    file=file,
                    scope=scope,
                    parent_id=parent_id,
                    entities=entities,
                    edges=edges,
                    self_attr_types=self_attr_types,
                )
            elif kind == "block":
                # `block` wraps a class/function body's statements — recurse.
                self._walk(
                    child,
                    source,
                    file,
                    scope=scope,
                    parent_id=parent_id,
                    module_id=module_id,
                    entities=entities,
                    edges=edges,
                    self_attr_types=self_attr_types,
                )
            elif kind == "import_statement" and not scope:
                self._emit_bare_import(child, source, module_id, edges)
            elif kind == "import_from_statement" and not scope:
                self._emit_from_import(child, source, module_id, edges)
            # Other top-level statements (assignments, if-blocks, …) ignored at T2.1.
            # Call edges land at T4.1.

    def _descend_into_class(
        self,
        class_def: Node,
        source: bytes,
        file: str,
        scope: list[str],
        class_entity_id: str,
        module_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> None:
        name = self._text(class_def.child_by_field_name("name"), source)
        if not name:
            return
        new_scope = [*scope, name]
        body = class_def.child_by_field_name("body")
        if body is None:
            return
        self._walk(
            body,
            source,
            file,
            scope=new_scope,
            parent_id=class_entity_id,
            module_id=module_id,
            entities=entities,
            edges=edges,
            self_attr_types=infer_self_attr_types(body, source),
        )

    # ------------------------------------------------------------------
    # Import extraction: emit edges with provisional dst_ids
    # that the symbol resolver closes.
    #
    # Encoding:
    #   absolute  `from x.y import z`  → dst = "py:?:x.y.z"
    #   relative  `from . import z`    → dst = "py:?rel1:z"
    #   relative  `from ..pkg import z`→ dst = "py:?rel2:pkg.z"
    #   wildcard  `from x import *`    → dst = "py:?:x.*"
    #   bare      `import x.y`         → dst = "py:?:x.y"

    def _emit_bare_import(
        self,
        stmt: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        line = stmt.start_point[0] + 1
        for name_node in stmt.children_by_field_name("name"):
            target = name_node
            if name_node.type == "aliased_import":
                inner = name_node.child_by_field_name("name")
                if inner is None:
                    continue
                target = inner
            path = self._text(target, source)
            if not path:
                continue
            edges.append(
                Edge(
                    src_id=module_id,
                    dst_id=f"py:?:{path}",
                    type="imports",
                    line=line,
                )
            )

    def _emit_from_import(
        self,
        stmt: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        line = stmt.start_point[0] + 1
        module_name_node = stmt.child_by_field_name("module_name")
        if module_name_node is None:
            return
        rel_depth, module_part = self._parse_module_path(module_name_node, source)
        prefix = f"py:?rel{rel_depth}:" if rel_depth > 0 else "py:?:"

        # Wildcard import has no `name:` field — it's a plain child.
        for c in stmt.children:
            if c.type == "wildcard_import":
                dst = f"{prefix}{module_part}.*" if module_part else f"{prefix}*"
                edges.append(Edge(src_id=module_id, dst_id=dst, type="imports", line=line))
                return

        for name_node in stmt.children_by_field_name("name"):
            target = name_node
            if name_node.type == "aliased_import":
                inner = name_node.child_by_field_name("name")
                if inner is None:
                    continue
                target = inner
            name = self._text(target, source)
            if not name:
                continue
            dst = f"{prefix}{module_part}.{name}" if module_part else f"{prefix}{name}"
            edges.append(Edge(src_id=module_id, dst_id=dst, type="imports", line=line))

    @staticmethod
    def _parse_module_path(node: Node, source: bytes) -> tuple[int, str]:
        """Return (relative_depth, dotted_name_part) for the module_name node.

        Absolute imports → (0, "auth.login").
        Relative imports → (depth, optional_subpath).
        """
        if node.type == "dotted_name":
            return 0, source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        if node.type == "relative_import":
            depth = 0
            name_part = ""
            for c in node.children:
                if c.type == "import_prefix":
                    depth = (
                        source[c.start_byte : c.end_byte]
                        .decode("utf-8", errors="replace")
                        .count(".")
                    )
                elif c.type == "dotted_name":
                    name_part = source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
            return depth, name_part
        return 0, ""

    # ------------------------------------------------------------------
    # Emit one entity

    def _emit(
        self,
        *,
        inner_def: Node,
        span_node: Node,
        source: bytes,
        file: str,
        scope: list[str],
        parent_id: str | None,
        entities: list[UIREntity],
        edges: list[Edge] | None = None,
        self_attr_types: dict[str, str] | None = None,
    ) -> str | None:
        name = self._text(inner_def.child_by_field_name("name"), source)
        if not name:
            return None

        qname = ".".join([*scope, name]) if scope else name
        entity_type = self._entity_type(inner_def, scope)

        raw_source = source[span_node.start_byte : span_node.end_byte].decode(
            "utf-8", errors="replace"
        )
        signature = self._signature(inner_def, source)
        docstring = self._docstring_from_def(inner_def, source)
        is_async = self._is_async(inner_def)
        entity_id = make_entity_id(Language.PYTHON, file, qname)

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=entity_type,
                name=name,
                qualified_name=qname,
                language=Language.PYTHON,
                file=file,
                start_line=span_node.start_point[0] + 1,
                end_line=span_node.end_point[0] + 1,
                start_col=span_node.start_point[1],
                end_col=span_node.end_point[1],
                raw_source=raw_source,
                docstring=docstring,
                signature=signature,
                is_exported=not name.startswith("_"),
                is_async=is_async,
                parent_id=parent_id,
                hash=hash_source(raw_source),
            )
        )

        # Inheritance edges: `class Foo(Base, Mixin):` -> one provisional
        # edge per base, resolved by the resolver and used to walk
        # Type.method up to a base class when unresolved directly on Type.
        if edges is not None and inner_def.type == "class_definition":
            for i, base_name in enumerate(extract_base_classes(inner_def, source)):
                edges.append(
                    Edge(
                        src_id=entity_id,
                        dst_id=f"py:?inherits:{i}:{base_name}",
                        type="inherits",
                        line=inner_def.start_point[0] + 1,
                    )
                )

        # Call edges: scan a function/method body for call expressions.
        if edges is not None and inner_def.type == "function_definition":
            body = inner_def.child_by_field_name("body")
            if body is not None:
                class_name = scope[-1] if scope else None
                self._emit_calls(
                    inner_def,
                    body,
                    source,
                    src_id=entity_id,
                    edges=edges,
                    class_name=class_name,
                    self_attr_types=self_attr_types or {},
                )

        return entity_id

    def _emit_calls(
        self,
        func_node: Node,
        body: Node,
        source: bytes,
        *,
        src_id: str,
        edges: list[Edge],
        class_name: str | None,
        self_attr_types: dict[str, str],
    ) -> None:
        """Emit a provisional `calls` edge per call expression in `body`.

        When the receiver's type can be inferred (a local variable's
        constructor/annotation, a typed parameter, `self`, or a `self.attr`
        tracked elsewhere in the class), dst is `py:?methodcall:<Type>.<name>`,
        which the resolver tries to match to that exact type's method before
        falling back to plain-name resolution. Otherwise dst is `py:?call:<name>`
        (the callee's simple name), resolved against same-file entities and
        the file's imports same as before.
        """
        local_types = infer_param_types(func_node, source)
        local_types.update(infer_local_types(body, source))
        for call in self._iter_call_nodes(body):
            callee = self._callee_name(call, source)
            if not callee:
                continue
            receiver_type = receiver_type_for_call(
                call, source, class_name, local_types, self_attr_types
            )
            dst_id = (
                f"py:?methodcall:{receiver_type}.{callee}"
                if receiver_type
                else f"py:?call:{callee}"
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
        """Yield every `call` node under `node` (including nested in arguments)."""
        for child in node.children:
            if child.type == "call":
                yield child
            yield from self._iter_call_nodes(child)

    @staticmethod
    def _callee_name(call_node: Node, source: bytes) -> str | None:
        fn = call_node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return source[fn.start_byte : fn.end_byte].decode("utf-8", errors="replace")
        if fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            if attr is not None:
                return source[attr.start_byte : attr.end_byte].decode("utf-8", errors="replace")
        return None

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _entity_type(inner_def: Node, scope: list[str]) -> EntityType:
        if inner_def.type == "class_definition":
            return EntityType.CLASS
        # function_definition
        return EntityType.METHOD if scope else EntityType.FUNCTION

    @staticmethod
    def _is_async(inner_def: Node) -> bool:
        if inner_def.type != "function_definition":
            return False
        return any(c.type == "async" for c in inner_def.children)

    @staticmethod
    def _text(node: Node | None, source: bytes) -> str | None:
        if node is None:
            return None
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _signature(def_node: Node, source: bytes) -> str | None:
        """Signature = everything from the def keyword up to (but not including) the body."""
        body = def_node.child_by_field_name("body")
        if body is None:
            return None
        raw = source[def_node.start_byte : body.start_byte].decode("utf-8", errors="replace")
        return raw.rstrip().rstrip(":").rstrip()

    @classmethod
    def _docstring_from_def(cls, def_node: Node, source: bytes) -> str | None:
        body = def_node.child_by_field_name("body")
        return cls._docstring_from_block(body, source) if body is not None else None

    @staticmethod
    def _docstring_from_block(block: Node, source: bytes) -> str | None:
        """Return the docstring if the first statement of `block` is a bare string."""
        for child in block.children:
            if child.type == "expression_statement":
                for cc in child.children:
                    if cc.type == "string":
                        text = source[cc.start_byte : cc.end_byte].decode("utf-8", errors="replace")
                        return _strip_string_literal(text)
                return None
            if child.type in ("comment", "decorator"):
                continue
            # First non-comment statement is not a bare string → no docstring.
            return None
        return None

    @staticmethod
    def _module_name_from_path(rel_path: str) -> str:
        # Strip extension; convert path separators to dots.
        # e.g. "auth/login.py" → "auth.login"
        stem = rel_path.removesuffix(".py").removesuffix(".pyi")
        return stem.replace("/", ".")


def _strip_string_literal(text: str) -> str:
    """Remove Python string prefix + outer quotes, then dedent via inspect.cleandoc."""
    s = text.strip()
    # Strip string prefix (r, b, u, f, and combinations).
    i = 0
    while i < len(s) and s[i] in "rRbBuUfF":
        i += 1
    s = s[i:]
    for quote in ('"""', "'''", '"', "'"):
        if s.startswith(quote) and s.endswith(quote) and len(s) >= 2 * len(quote):
            inner = s[len(quote) : -len(quote)]
            return inspect.cleandoc(inner)
    return inspect.cleandoc(s)
