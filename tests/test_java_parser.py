"""Tests for the Java parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.java import JavaParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def parser() -> JavaParser:
    return JavaParser()


def _by_name(result: ParseResult, name: str):
    matches = [e for e in result.entities if e.name == name]
    if not matches:
        return None
    # Prefer non-module entities when the module name clashes with an entity name
    # (e.g. Server.java produces module "Server" and class "Server").
    non_module = [e for e in matches if e.type != EntityType.MODULE]
    return non_module[0] if non_module else matches[0]


def _import_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "imports"]


def _call_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "calls"]


# ---------- protocol conformance ----------


def test_parser_implements_iparser_protocol(parser: JavaParser) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == Language.JAVA


def test_empty_source_yields_only_module_entity(parser: JavaParser) -> None:
    result = parser.parse(Path("src/Main.java"), "")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []
    assert result.errors == []


# ---------- module entity ----------


def test_module_name_derived_from_path(parser: JavaParser) -> None:
    result = parser.parse(Path("src/server/Server.java"), "")
    m = result.entities[0]
    assert m.name == "src.server.Server"
    assert m.qualified_name == "src.server.Server"
    assert m.entity_id == "java:src/server/Server.java:src.server.Server"
    assert m.language == Language.JAVA


def test_module_flat_path(parser: JavaParser) -> None:
    result = parser.parse(Path("Main.java"), "")
    assert result.entities[0].name == "Main"
    assert result.entities[0].entity_id == "java:Main.java:Main"


# ---------- class ----------


def test_extracts_public_class(parser: JavaParser) -> None:
    src = "public class Server {}"
    result = parser.parse(Path("Server.java"), src)
    cls = _by_name(result, "Server")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.entity_id == "java:Server.java:Server"
    assert cls.is_exported is True
    assert "Server" in (cls.signature or "")


def test_private_class_not_exported(parser: JavaParser) -> None:
    src = "class Config {}"
    result = parser.parse(Path("Config.java"), src)
    cls = _by_name(result, "Config")
    assert cls is not None
    assert cls.is_exported is False


def test_extracts_enum_as_class(parser: JavaParser) -> None:
    src = "public enum Status { ACTIVE, INACTIVE }"
    result = parser.parse(Path("Status.java"), src)
    e = _by_name(result, "Status")
    assert e is not None
    assert e.type == EntityType.CLASS
    assert e.is_exported is True


def test_is_async_always_false(parser: JavaParser) -> None:
    src = "public class Foo { public void bar() {} }"
    result = parser.parse(Path("Foo.java"), src)
    for entity in result.entities:
        assert entity.is_async is False


# ---------- interface ----------


def test_extracts_public_interface(parser: JavaParser) -> None:
    src = "public interface Handler { String handle(String req); }"
    result = parser.parse(Path("Handler.java"), src)
    iface = _by_name(result, "Handler")
    assert iface is not None
    assert iface.type == EntityType.INTERFACE
    assert iface.is_exported is True
    assert "Handler" in (iface.signature or "")


def test_private_interface_not_exported(parser: JavaParser) -> None:
    src = "interface Internal {}"
    result = parser.parse(Path("Internal.java"), src)
    iface = _by_name(result, "Internal")
    assert iface is not None
    assert iface.is_exported is False


# ---------- methods ----------


def test_methods_emitted_with_qualified_names(parser: JavaParser) -> None:
    src = "public class Server {\n    public void start() {}\n    private void listen() {}\n}"
    result = parser.parse(Path("Server.java"), src)
    start = _by_name(result, "start")
    assert start is not None
    assert start.type == EntityType.METHOD
    assert start.qualified_name == "Server.start"
    assert start.entity_id == "java:Server.java:Server.start"
    assert start.is_exported is True

    listen = _by_name(result, "listen")
    assert listen is not None
    assert listen.qualified_name == "Server.listen"
    assert listen.is_exported is False


def test_method_parent_id_points_to_class(parser: JavaParser) -> None:
    src = "public class Foo {\n    public void bar() {}\n}"
    result = parser.parse(Path("Foo.java"), src)
    method = _by_name(result, "bar")
    cls = _by_name(result, "Foo")
    assert method is not None and cls is not None
    assert method.parent_id == cls.entity_id


def test_constructor_emitted_as_method(parser: JavaParser) -> None:
    src = "public class Server {\n    public Server(String host) {}\n}"
    result = parser.parse(Path("Server.java"), src)
    # There are two entities named Server: the class and the constructor.
    # Find the METHOD one.
    methods = [e for e in result.entities if e.name == "Server" and e.type == EntityType.METHOD]
    assert len(methods) == 1
    assert methods[0].qualified_name == "Server.Server"


def test_static_method_emitted(parser: JavaParser) -> None:
    src = "public class Server {\n    public static Server create(String h) { return null; }\n}"
    result = parser.parse(Path("Server.java"), src)
    m = _by_name(result, "create")
    assert m is not None
    assert m.type == EntityType.METHOD
    assert m.is_exported is True


def test_interface_method_emitted(parser: JavaParser) -> None:
    src = "public interface Handler {\n    String handle(String req);\n}"
    result = parser.parse(Path("Handler.java"), src)
    m = _by_name(result, "handle")
    assert m is not None
    assert m.type == EntityType.METHOD
    assert m.qualified_name == "Handler.handle"


def test_interface_method_parent_id_points_to_interface(parser: JavaParser) -> None:
    src = "public interface Handler {\n    String handle(String req);\n}"
    result = parser.parse(Path("Handler.java"), src)
    iface = _by_name(result, "Handler")
    method = _by_name(result, "handle")
    assert iface is not None and method is not None
    assert method.parent_id == iface.entity_id


# ---------- imports ----------


def test_simple_import(parser: JavaParser) -> None:
    src = "import java.util.List;\n"
    result = parser.parse(Path("Main.java"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "java:?:java.util.List" for e in edges)


def test_wildcard_import(parser: JavaParser) -> None:
    src = "import java.io.*;\n"
    result = parser.parse(Path("Main.java"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "java:?:java.io.*" for e in edges)


def test_multiple_imports(parser: JavaParser) -> None:
    src = "import java.util.List;\nimport java.util.Map;\n"
    result = parser.parse(Path("Main.java"), src)
    dst_ids = {e.dst_id for e in _import_edges(result)}
    assert "java:?:java.util.List" in dst_ids
    assert "java:?:java.util.Map" in dst_ids


def test_import_src_is_module_entity(parser: JavaParser) -> None:
    src = "import java.util.List;\n"
    result = parser.parse(Path("Main.java"), src)
    edges = _import_edges(result)
    assert edges[0].src_id == "java:Main.java:Main"


# ---------- calls ----------


def test_simple_call_edge(parser: JavaParser) -> None:
    src = 'public class T {\n    public void run() { greet("world"); }\n}'
    result = parser.parse(Path("T.java"), src)
    assert any(e.dst_id == "java:?call:greet" for e in _call_edges(result))


def test_method_call_extracts_method_name(parser: JavaParser) -> None:
    src = "public class T {\n    public void run() { this.listen(); }\n}"
    result = parser.parse(Path("T.java"), src)
    # `this.listen()` -- receiver type is inferred as the enclosing class T.
    assert any(e.dst_id == "java:?methodcall:T.listen" for e in _call_edges(result))


def test_static_call_extracts_method_name(parser: JavaParser) -> None:
    src = 'public class T {\n    public void run() { Server.create("x"); }\n}'
    result = parser.parse(Path("T.java"), src)
    assert any(e.dst_id == "java:?call:create" for e in _call_edges(result))


def test_constructor_call_emits_edge(parser: JavaParser) -> None:
    """`new Foo()` constructs Foo -- a call to its constructor -- even though
    it's a structurally different node from a method_invocation. Regression
    test: a class only ever instantiated via `new` (never called as a
    method) used to show zero callers / look like dead code."""
    src = "public class T {\n    public void run() { WelfordStats s = new WelfordStats(); }\n}"
    result = parser.parse(Path("T.java"), src)
    assert any(e.dst_id == "java:?call:WelfordStats" for e in _call_edges(result))


def test_constructor_call_with_generic_type_uses_base_name(parser: JavaParser) -> None:
    src = "public class T {\n    public void run() { var m = new HashMap<String, Integer>(); }\n}"
    result = parser.parse(Path("T.java"), src)
    assert any(e.dst_id == "java:?call:HashMap" for e in _call_edges(result))


def test_constructor_call_nested_in_arguments_captured(parser: JavaParser) -> None:
    src = "public class T {\n    public void run() { foo(new Bar()); }\n}"
    result = parser.parse(Path("T.java"), src)
    dsts = {e.dst_id for e in _call_edges(result)}
    assert "java:?call:foo" in dsts
    assert "java:?call:Bar" in dsts


def test_field_initializer_call_attributed_to_class(parser: JavaParser) -> None:
    """A field initializer (`private final X x = new X();`) runs as part of
    every instance's construction, but there's no per-field entity -- the
    class itself is the natural owner. Regression test: field initializers
    weren't scanned for calls at all, so a class only ever instantiated via
    a field initializer (found live in a real Java codebase) was invisible
    to every caller."""
    src = "public class AnomalyScorer {\n    private final WelfordStats baseline = new WelfordStats();\n}\n"
    result = parser.parse(Path("AnomalyScorer.java"), src)
    edges = _call_edges(result)
    assert any(
        e.dst_id == "java:?call:WelfordStats" and e.src_id == "java:AnomalyScorer.java:AnomalyScorer"
        for e in edges
    )


def test_field_declaration_without_initializer_is_fine(parser: JavaParser) -> None:
    """A field with no initializer (`private int x;`) has no `value` node --
    must not crash."""
    src = "public class T {\n    private int x;\n}\n"
    result = parser.parse(Path("T.java"), src)
    assert _call_edges(result) == []


# ---------- fixture end-to-end ----------


def test_fixture_server_emits_expected_entities(parser: JavaParser) -> None:
    src = Path("tests/fixtures/sample_repo_java/src/server/Server.java").read_text(encoding="utf-8")
    result = parser.parse(Path("src/server/Server.java"), src)
    names = {e.name for e in result.entities}

    assert "Server" in names
    assert "Status" in names
    assert "Handler" in names
    assert "start" in names
    assert "listen" in names
    assert "create" in names
    assert "handle" in names

    server = _by_name(result, "Server")
    assert server is not None and server.type == EntityType.CLASS
    assert server.is_exported is True

    status = _by_name(result, "Status")
    assert status is not None and status.type == EntityType.CLASS
    assert status.is_exported is False

    handler = _by_name(result, "Handler")
    assert handler is not None and handler.type == EntityType.INTERFACE

    start = _by_name(result, "start")
    assert start is not None
    assert start.qualified_name == "Server.start"
    assert start.is_exported is True
    assert start.parent_id == server.entity_id

    listen = _by_name(result, "listen")
    assert listen is not None and listen.is_exported is False

    # start() calls this.listen() — call edge, receiver type inferred as Server
    call_edges = _call_edges(result)
    assert any(e.dst_id == "java:?methodcall:Server.listen" for e in call_edges)

    # imports: java.util.List and java.io.*
    import_edges = _import_edges(result)
    assert any("java.util.List" in e.dst_id for e in import_edges)
    assert any("java.io.*" in e.dst_id for e in import_edges)
