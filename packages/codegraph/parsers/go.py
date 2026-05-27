"""Go parser — tree-sitter recursive walk → UIREntity stream.

Emits one Module entity per file plus Function / Method / Class (struct) /
Interface per top-level declaration. Methods are qualified as
"ReceiverType.methodName" to mirror how Go defines behaviour on types.

Import extraction emits provisional `go:?:<import_path>` edges. Call
extraction emits provisional `go:?call:<callee>` edges. Both sets are
resolved cross-file in T10.7; until then they remain as unresolved leaves
visible in `deps` output.
"""

from __future__ import annotations

import re
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

# Matches the receiver type name from patterns like "(sl *Server)" or "(s Server)".
# Searches for an uppercase-or-lowercase identifier followed by ")" — the last
# identifier before closing paren is always the type name (pointer or plain).
_RECEIVER_RE = re.compile(r"\*?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")


class GoParser:
    """Tree-sitter Go parser. Stateless; safe to reuse across files."""

    language: ClassVar[Language] = Language.GO
    _parser: ClassVar[Parser | None] = None

    @classmethod
    def _ts_parser(cls) -> Parser:
        if cls._parser is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                lang = get_language("go")
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
        module_id = make_entity_id(Language.GO, rel_path, module_name)
        entities.append(
            UIREntity(
                entity_id=module_id,
                type=EntityType.MODULE,
                name=module_name,
                qualified_name=module_name,
                language=Language.GO,
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
            if kind == "function_declaration":
                self._emit_function(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "method_declaration":
                self._emit_method(child, source_bytes, rel_path, module_id, entities, edges)
            elif kind == "type_declaration":
                self._emit_type_decl(child, source_bytes, rel_path, module_id, entities)
            elif kind == "import_declaration":
                self._emit_imports(child, source_bytes, module_id, edges)

        return ParseResult(entities=entities, edges=edges, errors=errors)

    # ------------------------------------------------------------------
    # Entity emitters

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

        qname = name
        entity_id = make_entity_id(Language.GO, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source)
        is_exported = name[0].isupper()

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.FUNCTION,
                name=name,
                qualified_name=qname,
                language=Language.GO,
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
            self._emit_calls(body, source, src_id=entity_id, edges=edges)

        return entity_id

    def _emit_method(
        self,
        node: Node,
        source: bytes,
        file: str,
        module_id: str,
        entities: list[UIREntity],
        edges: list[Edge],
    ) -> str | None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, source)
        if not name:
            return None

        receiver_type = self._receiver_type(node, source)
        qname = f"{receiver_type}.{name}" if receiver_type else name
        entity_id = make_entity_id(Language.GO, file, qname)
        raw_source = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        signature = self._sig_before_body(node, source)
        is_exported = name[0].isupper()

        parent_id = make_entity_id(Language.GO, file, receiver_type) if receiver_type else module_id

        entities.append(
            UIREntity(
                entity_id=entity_id,
                type=EntityType.METHOD,
                name=name,
                qualified_name=qname,
                language=Language.GO,
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
            self._emit_calls(body, source, src_id=entity_id, edges=edges)

        return entity_id

    def _emit_type_decl(
        self,
        node: Node,
        source: bytes,
        file: str,
        parent_id: str,
        entities: list[UIREntity],
    ) -> None:
        """Emit CLASS (struct) or INTERFACE entities from a type declaration."""
        for child in node.children:
            if child.type != "type_spec":
                continue
            name_node = child.child_by_field_name("name")
            name = self._text(name_node, source)
            if not name:
                continue

            type_child = child.child_by_field_name("type")
            if type_child is None:
                continue

            if type_child.type == "struct_type":
                entity_type = EntityType.CLASS
            elif type_child.type == "interface_type":
                entity_type = EntityType.INTERFACE
            else:
                continue  # type aliases, generics, plain type refs — skip at MVP

            qname = name
            entity_id = make_entity_id(Language.GO, file, qname)
            raw_source = child.text.decode("utf-8", errors="replace")
            is_exported = name[0].isupper()
            kind_word = "struct" if entity_type == EntityType.CLASS else "interface"

            entities.append(
                UIREntity(
                    entity_id=entity_id,
                    type=entity_type,
                    name=name,
                    qualified_name=qname,
                    language=Language.GO,
                    file=file,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    start_col=child.start_point[1],
                    end_col=child.end_point[1],
                    raw_source=raw_source,
                    docstring=None,
                    signature=f"type {name} {kind_word}",
                    is_exported=is_exported,
                    is_async=False,
                    parent_id=parent_id,
                    hash=hash_source(raw_source),
                )
            )

    # ------------------------------------------------------------------
    # Import extraction — provisional `go:?:<import_path>` edges

    def _emit_imports(
        self,
        node: Node,
        source: bytes,
        module_id: str,
        edges: list[Edge],
    ) -> None:
        for child in node.children:
            if child.type == "import_spec":
                self._emit_import_spec(child, source, module_id, child.start_point[0] + 1, edges)
            elif child.type == "import_spec_list":
                for spec in child.children:
                    if spec.type == "import_spec":
                        self._emit_import_spec(
                            spec, source, module_id, spec.start_point[0] + 1, edges
                        )

    def _emit_import_spec(
        self,
        spec: Node,
        source: bytes,
        module_id: str,
        line: int,
        edges: list[Edge],
    ) -> None:
        path_node = spec.child_by_field_name("path")
        if path_node is None:
            return
        raw = self._text(path_node, source)
        if not raw:
            return
        import_path = raw.strip('"').strip("`")
        if not import_path:
            return
        edges.append(
            Edge(
                src_id=module_id,
                dst_id=f"go:?:{import_path}",
                type="imports",
                line=line,
            )
        )

    # ------------------------------------------------------------------
    # Call edge extraction — provisional `go:?call:<callee>` edges

    def _emit_calls(self, body: Node, source: bytes, *, src_id: str, edges: list[Edge]) -> None:
        for call in self._iter_call_nodes(body):
            callee = self._callee_name(call, source)
            if not callee:
                continue
            edges.append(
                Edge(
                    src_id=src_id,
                    dst_id=f"go:?call:{callee}",
                    type="calls",
                    line=call.start_point[0] + 1,
                    confidence=0.7,
                )
            )

    def _iter_call_nodes(self, node: Node):
        for child in node.children:
            if child.type == "call_expression":
                yield child
            yield from self._iter_call_nodes(child)

    @staticmethod
    def _callee_name(call_node: Node, source: bytes) -> str | None:
        fn = call_node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return source[fn.start_byte : fn.end_byte].decode("utf-8", errors="replace")
        if fn.type == "selector_expression":
            # "field" is the tree-sitter-go field name for the selected identifier.
            sel = fn.child_by_field_name("field")
            if sel is not None:
                return source[sel.start_byte : sel.end_byte].decode("utf-8", errors="replace")
        return None

    # ------------------------------------------------------------------
    # Helpers

    def _receiver_type(self, method_node: Node, source: bytes) -> str | None:
        receiver = method_node.child_by_field_name("receiver")
        if receiver is None:
            return None
        text = self._text(receiver, source) or ""
        m = _RECEIVER_RE.search(text)
        return m.group(1) if m else None

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
        stem = rel_path.removesuffix(".go")
        return stem.replace("/", ".")
