"""Tests for the Go parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.go import GoParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def parser() -> GoParser:
    return GoParser()


def _by_name(result: ParseResult, name: str):
    return next((e for e in result.entities if e.name == name), None)


def _import_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "imports"]


def _call_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "calls"]


# ---------- protocol conformance ----------


def test_parser_implements_iparser_protocol(parser: GoParser) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == Language.GO


def test_empty_source_yields_only_module_entity(parser: GoParser) -> None:
    result = parser.parse(Path("pkg/main.go"), "package main\n")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []
    assert result.errors == []


# ---------- module entity ----------


def test_module_name_derived_from_path(parser: GoParser) -> None:
    result = parser.parse(Path("cmd/main.go"), "package main\n")
    module = result.entities[0]
    assert module.type == EntityType.MODULE
    assert module.name == "cmd.main"
    assert module.qualified_name == "cmd.main"
    assert module.entity_id == "go:cmd/main.go:cmd.main"
    assert module.language == Language.GO


def test_module_flat_path(parser: GoParser) -> None:
    result = parser.parse(Path("server.go"), "package server\n")
    module = result.entities[0]
    assert module.name == "server"
    assert module.entity_id == "go:server.go:server"


# ---------- top-level function ----------


def test_extracts_top_level_function(parser: GoParser) -> None:
    src = "package main\n\nfunc greet(name string) string {\n\treturn name\n}\n"
    result = parser.parse(Path("main.go"), src)
    fn = _by_name(result, "greet")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.qualified_name == "greet"
    assert fn.entity_id == "go:main.go:greet"
    assert fn.start_line == 3
    assert fn.is_exported is False
    assert fn.signature is not None and "greet" in fn.signature
    assert fn.is_async is False


def test_exported_function_marked_exported(parser: GoParser) -> None:
    src = "package main\n\nfunc NewServer() *Server {\n\treturn nil\n}\n"
    result = parser.parse(Path("server.go"), src)
    fn = _by_name(result, "NewServer")
    assert fn is not None
    assert fn.is_exported is True


def test_function_parent_id_is_module(parser: GoParser) -> None:
    src = "package main\n\nfunc run() {}\n"
    result = parser.parse(Path("main.go"), src)
    fn = _by_name(result, "run")
    module = next(e for e in result.entities if e.type == EntityType.MODULE)
    assert fn is not None
    assert fn.parent_id == module.entity_id


# ---------- method ----------


def test_extracts_method_with_pointer_receiver(parser: GoParser) -> None:
    src = "package server\n\ntype Server struct{}\n\nfunc (s *Server) Start() {\n}\n"
    result = parser.parse(Path("server/server.go"), src)
    method = _by_name(result, "Start")
    assert method is not None
    assert method.type == EntityType.METHOD
    assert method.qualified_name == "Server.Start"
    assert method.entity_id == "go:server/server.go:Server.Start"
    assert method.is_exported is True


def test_method_with_value_receiver(parser: GoParser) -> None:
    src = 'package server\n\nfunc (s Server) String() string {\n\treturn ""\n}\n'
    result = parser.parse(Path("s.go"), src)
    method = _by_name(result, "String")
    assert method is not None
    assert method.qualified_name == "Server.String"


def test_method_parent_id_points_to_receiver_type(parser: GoParser) -> None:
    src = "package server\n\ntype Server struct{}\n\nfunc (s *Server) Stop() {\n}\n"
    result = parser.parse(Path("srv.go"), src)
    method = _by_name(result, "Stop")
    struct_type = _by_name(result, "Server")
    assert method is not None and struct_type is not None
    assert method.parent_id == struct_type.entity_id


def test_unexported_method(parser: GoParser) -> None:
    src = "package srv\n\nfunc (s *Server) listen() {\n}\n"
    result = parser.parse(Path("s.go"), src)
    method = _by_name(result, "listen")
    assert method is not None
    assert method.is_exported is False
    assert method.type == EntityType.METHOD


# ---------- types ----------


def test_extracts_struct_as_class(parser: GoParser) -> None:
    src = "package pkg\n\ntype Server struct {\n\thost string\n\tport int\n}\n"
    result = parser.parse(Path("pkg.go"), src)
    cls = _by_name(result, "Server")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.qualified_name == "Server"
    assert cls.is_exported is True
    assert cls.signature == "type Server struct"


def test_extracts_interface(parser: GoParser) -> None:
    src = "package pkg\n\ntype Handler interface {\n\tHandle(req string) string\n}\n"
    result = parser.parse(Path("pkg.go"), src)
    iface = _by_name(result, "Handler")
    assert iface is not None
    assert iface.type == EntityType.INTERFACE
    assert iface.is_exported is True
    assert iface.signature == "type Handler interface"


def test_unexported_struct_not_exported(parser: GoParser) -> None:
    src = "package pkg\n\ntype config struct {\n\tport int\n}\n"
    result = parser.parse(Path("pkg.go"), src)
    cls = _by_name(result, "config")
    assert cls is not None
    assert cls.is_exported is False


# ---------- imports ----------


def test_single_import(parser: GoParser) -> None:
    src = 'package main\n\nimport "fmt"\n'
    result = parser.parse(Path("main.go"), src)
    edges = _import_edges(result)
    assert len(edges) == 1
    assert edges[0].dst_id == "go:?:fmt"
    assert edges[0].line == 3


def test_grouped_imports(parser: GoParser) -> None:
    src = 'package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n'
    result = parser.parse(Path("main.go"), src)
    edges = _import_edges(result)
    assert {e.dst_id for e in edges} == {"go:?:fmt", "go:?:os"}


def test_import_src_is_module_entity(parser: GoParser) -> None:
    src = 'package main\n\nimport "fmt"\n'
    result = parser.parse(Path("main.go"), src)
    edges = _import_edges(result)
    assert edges[0].src_id == "go:main.go:main"


def test_deep_import_path(parser: GoParser) -> None:
    src = 'package main\n\nimport "github.com/pkg/errors"\n'
    result = parser.parse(Path("main.go"), src)
    edges = _import_edges(result)
    assert edges[0].dst_id == "go:?:github.com/pkg/errors"


def test_no_import_edges_when_no_imports(parser: GoParser) -> None:
    src = "package main\n\nfunc f() {}\n"
    result = parser.parse(Path("main.go"), src)
    assert _import_edges(result) == []


# ---------- calls ----------


def test_simple_call_edge_emitted(parser: GoParser) -> None:
    src = 'package main\n\nfunc run() {\n\tgreet("world")\n}\n'
    result = parser.parse(Path("main.go"), src)
    edges = _call_edges(result)
    assert any(e.dst_id == "go:?call:greet" for e in edges)


def test_selector_call_extracts_method_name(parser: GoParser) -> None:
    src = "package main\n\nfunc run(s *Server) {\n\ts.Start()\n}\n"
    result = parser.parse(Path("main.go"), src)
    edges = _call_edges(result)
    assert any(e.dst_id == "go:?call:Start" for e in edges)


def test_call_edges_have_low_confidence(parser: GoParser) -> None:
    src = "package main\n\nfunc run() {\n\tfoo()\n}\n"
    result = parser.parse(Path("main.go"), src)
    for e in _call_edges(result):
        assert e.confidence == 0.7


# ---------- fixture end-to-end ----------


def test_fixture_server_emits_expected_entities(parser: GoParser) -> None:
    src = Path("tests/fixtures/sample_repo_go/server/server.go").read_text(encoding="utf-8")
    result = parser.parse(Path("server/server.go"), src)
    names = {e.name for e in result.entities}

    assert "Server" in names
    assert "Handler" in names
    assert "New" in names
    assert "Start" in names
    assert "listen" in names

    server_type = _by_name(result, "Server")
    assert server_type is not None and server_type.type == EntityType.CLASS

    handler_type = _by_name(result, "Handler")
    assert handler_type is not None and handler_type.type == EntityType.INTERFACE

    start = _by_name(result, "Start")
    assert start is not None
    assert start.qualified_name == "Server.Start"
    assert start.is_exported is True
    assert start.parent_id == server_type.entity_id

    listen = _by_name(result, "listen")
    assert listen is not None
    assert listen.is_exported is False

    new_fn = _by_name(result, "New")
    assert new_fn is not None and new_fn.type == EntityType.FUNCTION

    import_edges = _import_edges(result)
    assert any("fmt" in e.dst_id for e in import_edges)


def test_fixture_main_emits_expected_entities(parser: GoParser) -> None:
    src = Path("tests/fixtures/sample_repo_go/cmd/main.go").read_text(encoding="utf-8")
    result = parser.parse(Path("cmd/main.go"), src)
    names = {e.name for e in result.entities}

    assert "main" in names
    assert "greet" in names

    import_edges = _import_edges(result)
    import_paths = {e.dst_id for e in import_edges}
    assert "go:?:fmt" in import_paths
    assert "go:?:sample/server" in import_paths

    # main() calls server.New(), s.Start(), greet()
    call_edges = _call_edges(result)
    call_targets = {e.dst_id for e in call_edges}
    assert "go:?call:New" in call_targets
    assert "go:?call:Start" in call_targets
    assert "go:?call:greet" in call_targets
