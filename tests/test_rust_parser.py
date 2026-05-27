"""Tests for the Rust parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.rust import RustParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def parser() -> RustParser:
    return RustParser()


def _by_name(result: ParseResult, name: str):
    return next((e for e in result.entities if e.name == name), None)


def _import_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "imports"]


def _call_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "calls"]


# ---------- protocol conformance ----------


def test_parser_implements_iparser_protocol(parser: RustParser) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == Language.RUST


def test_empty_source_yields_only_module_entity(parser: RustParser) -> None:
    result = parser.parse(Path("src/main.rs"), "")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []
    assert result.errors == []


# ---------- module entity ----------


def test_module_name_derived_from_path(parser: RustParser) -> None:
    result = parser.parse(Path("src/main.rs"), "")
    m = result.entities[0]
    assert m.name == "src.main"
    assert m.qualified_name == "src.main"
    assert m.entity_id == "rs:src/main.rs:src.main"
    assert m.language == Language.RUST


def test_module_flat_path(parser: RustParser) -> None:
    result = parser.parse(Path("lib.rs"), "")
    assert result.entities[0].name == "lib"
    assert result.entities[0].entity_id == "rs:lib.rs:lib"


# ---------- top-level function ----------


def test_extracts_top_level_function(parser: RustParser) -> None:
    src = 'pub fn greet(name: &str) -> String {\n    format!("Hi")\n}\n'
    result = parser.parse(Path("lib.rs"), src)
    fn = _by_name(result, "greet")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.qualified_name == "greet"
    assert fn.entity_id == "rs:lib.rs:greet"
    assert fn.is_exported is True
    assert fn.signature is not None and "greet" in fn.signature


def test_private_function_not_exported(parser: RustParser) -> None:
    src = "fn helper() {}\n"
    result = parser.parse(Path("lib.rs"), src)
    fn = _by_name(result, "helper")
    assert fn is not None
    assert fn.is_exported is False


def test_async_function_flagged(parser: RustParser) -> None:
    src = "pub async fn fetch() -> String { String::new() }\n"
    result = parser.parse(Path("lib.rs"), src)
    fn = _by_name(result, "fetch")
    assert fn is not None
    assert fn.is_async is True


# ---------- struct ----------


def test_extracts_struct(parser: RustParser) -> None:
    src = "pub struct Server { host: String }\n"
    result = parser.parse(Path("server.rs"), src)
    cls = _by_name(result, "Server")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.is_exported is True
    assert cls.signature == "pub struct Server"


def test_private_struct_not_exported(parser: RustParser) -> None:
    src = "struct Config { port: u16 }\n"
    result = parser.parse(Path("lib.rs"), src)
    cls = _by_name(result, "Config")
    assert cls is not None
    assert cls.is_exported is False
    assert cls.signature == "struct Config"


def test_extracts_enum(parser: RustParser) -> None:
    src = "pub enum Status { Active, Inactive }\n"
    result = parser.parse(Path("lib.rs"), src)
    cls = _by_name(result, "Status")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.signature == "pub enum Status"


# ---------- trait ----------


def test_extracts_trait_as_interface(parser: RustParser) -> None:
    src = "pub trait Handler {\n    fn handle(&self) -> String;\n}\n"
    result = parser.parse(Path("lib.rs"), src)
    trait = _by_name(result, "Handler")
    assert trait is not None
    assert trait.type == EntityType.INTERFACE
    assert trait.is_exported is True
    assert trait.signature == "pub trait Handler"


def test_private_trait_not_exported(parser: RustParser) -> None:
    src = "trait Internal {}\n"
    result = parser.parse(Path("lib.rs"), src)
    t = _by_name(result, "Internal")
    assert t is not None
    assert t.is_exported is False


# ---------- impl methods ----------


def test_impl_methods_emitted_as_methods(parser: RustParser) -> None:
    src = "pub struct Server {}\n\nimpl Server {\n    pub fn start(&self) {}\n    fn listen(&self) {}\n}\n"
    result = parser.parse(Path("server.rs"), src)

    start = _by_name(result, "start")
    assert start is not None
    assert start.type == EntityType.METHOD
    assert start.qualified_name == "Server.start"
    assert start.entity_id == "rs:server.rs:Server.start"
    assert start.is_exported is True

    listen = _by_name(result, "listen")
    assert listen is not None
    assert listen.type == EntityType.METHOD
    assert listen.qualified_name == "Server.listen"
    assert listen.is_exported is False


def test_method_parent_id_points_to_struct(parser: RustParser) -> None:
    src = "pub struct Foo {}\nimpl Foo {\n    pub fn bar(&self) {}\n}\n"
    result = parser.parse(Path("foo.rs"), src)
    method = _by_name(result, "bar")
    struct_entity = _by_name(result, "Foo")
    assert method is not None and struct_entity is not None
    assert method.parent_id == struct_entity.entity_id


def test_trait_impl_methods_emitted(parser: RustParser) -> None:
    src = (
        "pub struct Server {}\n"
        "pub trait Handler { fn handle(&self) -> String; }\n"
        "impl Handler for Server {\n"
        "    fn handle(&self) -> String { String::new() }\n"
        "}\n"
    )
    result = parser.parse(Path("s.rs"), src)
    handle = _by_name(result, "handle")
    assert handle is not None
    assert handle.qualified_name == "Server.handle"
    assert handle.type == EntityType.METHOD


# ---------- imports ----------


def test_simple_use(parser: RustParser) -> None:
    src = "use std::io;\n"
    result = parser.parse(Path("main.rs"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "rs:?:std::io" for e in edges)


def test_grouped_use(parser: RustParser) -> None:
    src = "use std::fmt::{self, Write};\n"
    result = parser.parse(Path("main.rs"), src)
    edges = _import_edges(result)
    dst_ids = {e.dst_id for e in edges}
    assert "rs:?:std::fmt" in dst_ids
    assert "rs:?:std::fmt::Write" in dst_ids


def test_use_as_clause(parser: RustParser) -> None:
    src = "use serde::Serialize as Ser;\n"
    result = parser.parse(Path("main.rs"), src)
    edges = _import_edges(result)
    assert any("serde::Serialize" in e.dst_id for e in edges)


def test_use_wildcard(parser: RustParser) -> None:
    src = "use std::*;\n"
    result = parser.parse(Path("main.rs"), src)
    edges = _import_edges(result)
    assert any("std::*" in e.dst_id for e in edges)


def test_import_src_is_module_entity(parser: RustParser) -> None:
    src = "use std::io;\n"
    result = parser.parse(Path("main.rs"), src)
    edges = _import_edges(result)
    assert edges[0].src_id == "rs:main.rs:main"


# ---------- calls ----------


def test_simple_call_edge(parser: RustParser) -> None:
    src = 'fn run() {\n    greet("world");\n}\n'
    result = parser.parse(Path("main.rs"), src)
    assert any(e.dst_id == "rs:?call:greet" for e in _call_edges(result))


def test_method_call_extracts_method_name(parser: RustParser) -> None:
    src = "fn run(s: &Server) {\n    s.start();\n}\n"
    result = parser.parse(Path("main.rs"), src)
    assert any(e.dst_id == "rs:?call:start" for e in _call_edges(result))


def test_scoped_call_extracts_last_segment(parser: RustParser) -> None:
    src = "fn run() {\n    Server::new();\n}\n"
    result = parser.parse(Path("main.rs"), src)
    assert any(e.dst_id == "rs:?call:new" for e in _call_edges(result))


# ---------- fixture end-to-end ----------


def test_fixture_server_emits_expected_entities(parser: RustParser) -> None:
    src = Path("tests/fixtures/sample_repo_rust/src/server.rs").read_text(encoding="utf-8")
    result = parser.parse(Path("src/server.rs"), src)
    names = {e.name for e in result.entities}

    assert "Server" in names
    assert "Status" in names
    assert "Handler" in names
    assert "new_server" in names
    assert "new" in names
    assert "start" in names
    assert "listen" in names
    assert "handle" in names

    server = _by_name(result, "Server")
    assert server is not None and server.type == EntityType.CLASS

    status = _by_name(result, "Status")
    assert status is not None and status.type == EntityType.CLASS

    handler = _by_name(result, "Handler")
    assert handler is not None and handler.type == EntityType.INTERFACE

    start = _by_name(result, "start")
    assert start is not None
    assert start.qualified_name == "Server.start"
    assert start.is_exported is True
    assert start.parent_id == server.entity_id

    listen = _by_name(result, "listen")
    assert listen is not None and listen.is_exported is False

    import_edges = _import_edges(result)
    assert any("fmt" in e.dst_id for e in import_edges)

    # start() calls self.listen() — should emit a call edge
    call_edges = _call_edges(result)
    assert any(e.dst_id == "rs:?call:listen" for e in call_edges)
