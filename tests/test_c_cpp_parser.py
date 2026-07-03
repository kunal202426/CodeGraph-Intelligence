"""Tests for the C and C++ parsers."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.c_cpp import CParser, CppParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def c_parser() -> CParser:
    return CParser()


@pytest.fixture
def cpp_parser() -> CppParser:
    return CppParser()


def _by_name(result: ParseResult, name: str):
    matches = [e for e in result.entities if e.name == name]
    if not matches:
        return None
    non_module = [e for e in matches if e.type != EntityType.MODULE]
    return non_module[0] if non_module else matches[0]


def _import_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "imports"]


def _call_edges(result: ParseResult):
    return [e for e in result.edges if e.type == "calls"]


# ---------- protocol conformance ----------


def test_c_parser_implements_iparser_protocol(c_parser: CParser) -> None:
    assert isinstance(c_parser, IParser)
    assert c_parser.language == Language.C


def test_cpp_parser_implements_iparser_protocol(cpp_parser: CppParser) -> None:
    assert isinstance(cpp_parser, IParser)
    assert cpp_parser.language == Language.CPP


def test_c_empty_source_yields_only_module_entity(c_parser: CParser) -> None:
    result = c_parser.parse(Path("src/main.c"), "")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []
    assert result.errors == []


def test_cpp_empty_source_yields_only_module_entity(cpp_parser: CppParser) -> None:
    result = cpp_parser.parse(Path("src/main.cpp"), "")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE


# ---------- module entity ----------


def test_c_module_name_from_path(c_parser: CParser) -> None:
    result = c_parser.parse(Path("src/server.c"), "")
    m = result.entities[0]
    assert m.name == "src.server"
    assert m.entity_id == "c:src/server.c:src.server"
    assert m.language == Language.C


def test_cpp_module_name_from_path(cpp_parser: CppParser) -> None:
    result = cpp_parser.parse(Path("src/server.cpp"), "")
    m = result.entities[0]
    assert m.name == "src.server"
    assert m.entity_id == "cpp:src/server.cpp:src.server"
    assert m.language == Language.CPP


# ---------- C functions ----------


def test_c_extracts_function(c_parser: CParser) -> None:
    src = "void greet(const char *name) { printf(name); }\n"
    result = c_parser.parse(Path("utils.c"), src)
    fn = _by_name(result, "greet")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.entity_id == "c:utils.c:greet"
    assert fn.is_exported is True


def test_c_static_function_not_exported(c_parser: CParser) -> None:
    src = "static void helper() { }\n"
    result = c_parser.parse(Path("utils.c"), src)
    fn = _by_name(result, "helper")
    assert fn is not None
    assert fn.is_exported is False


def test_c_pointer_return_function(c_parser: CParser) -> None:
    src = "Server *server_new(const char *host) { return NULL; }\n"
    result = c_parser.parse(Path("utils.c"), src)
    fn = _by_name(result, "server_new")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION


# ---------- C structs ----------


def test_c_typedef_struct(c_parser: CParser) -> None:
    src = "typedef struct {\n    char *host;\n    int port;\n} Server;\n"
    result = c_parser.parse(Path("server.c"), src)
    cls = _by_name(result, "Server")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.entity_id == "c:server.c:Server"


def test_c_named_struct(c_parser: CParser) -> None:
    src = "struct Config {\n    int timeout;\n};\n"
    result = c_parser.parse(Path("config.c"), src)
    cls = _by_name(result, "Config")
    assert cls is not None
    assert cls.type == EntityType.CLASS


# ---------- C++ class ----------


def test_cpp_extracts_class(cpp_parser: CppParser) -> None:
    src = "class Server {\npublic:\n    void start() {}\n};\n"
    result = cpp_parser.parse(Path("server.cpp"), src)
    cls = _by_name(result, "Server")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.entity_id == "cpp:server.cpp:Server"


def test_cpp_class_methods(cpp_parser: CppParser) -> None:
    src = "class Server {\npublic:\n    void start() {}\nprivate:\n    void listen() {}\n};\n"
    result = cpp_parser.parse(Path("server.cpp"), src)
    start = _by_name(result, "start")
    assert start is not None
    assert start.type == EntityType.METHOD
    assert start.qualified_name == "Server.start"
    assert start.entity_id == "cpp:server.cpp:Server.start"
    assert start.is_exported is True

    listen = _by_name(result, "listen")
    assert listen is not None
    assert listen.is_exported is False


def test_cpp_method_parent_id_points_to_class(cpp_parser: CppParser) -> None:
    src = "class Foo {\npublic:\n    void bar() {}\n};\n"
    result = cpp_parser.parse(Path("foo.cpp"), src)
    method = _by_name(result, "bar")
    cls = _by_name(result, "Foo")
    assert method is not None and cls is not None
    assert method.parent_id == cls.entity_id


def test_cpp_struct_default_public(cpp_parser: CppParser) -> None:
    src = "struct Point {\n    int x;\n    void reset() {}\n};\n"
    result = cpp_parser.parse(Path("point.cpp"), src)
    m = _by_name(result, "reset")
    assert m is not None and m.is_exported is True


def test_is_async_always_false(c_parser: CParser, cpp_parser: CppParser) -> None:
    src_c = "void run() { }\n"
    src_cpp = "class T { public: void run() {} };\n"
    for parser, src, path in [
        (c_parser, src_c, "run.c"),
        (cpp_parser, src_cpp, "run.cpp"),
    ]:
        for entity in parser.parse(Path(path), src).entities:
            assert entity.is_async is False


# ---------- includes ----------


def test_c_system_include(c_parser: CParser) -> None:
    src = "#include <stdio.h>\n"
    result = c_parser.parse(Path("main.c"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "c:?:stdio.h" for e in edges)


def test_c_local_include(c_parser: CParser) -> None:
    src = '#include "server.h"\n'
    result = c_parser.parse(Path("main.c"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "c:?:server.h" for e in edges)


def test_cpp_include(cpp_parser: CppParser) -> None:
    src = "#include <string>\n"
    result = cpp_parser.parse(Path("main.cpp"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "cpp:?:string" for e in edges)


def test_include_src_is_module_entity(c_parser: CParser) -> None:
    src = "#include <stdio.h>\n"
    result = c_parser.parse(Path("main.c"), src)
    edges = _import_edges(result)
    assert edges[0].src_id == "c:main.c:main"


# ---------- calls ----------


def test_c_direct_call_edge(c_parser: CParser) -> None:
    src = 'void run() { greet("world"); }\n'
    result = c_parser.parse(Path("main.c"), src)
    assert any(e.dst_id == "c:?call:greet" for e in _call_edges(result))


def test_cpp_this_member_call(cpp_parser: CppParser) -> None:
    src = "class T {\npublic:\n    void run() { this->listen(); }\nprivate:\n    void listen() {}\n};\n"
    result = cpp_parser.parse(Path("t.cpp"), src)
    # `this->listen()` -- receiver type is inferred as the enclosing class T.
    assert any(e.dst_id == "cpp:?methodcall:T.listen" for e in _call_edges(result))


def test_cpp_scoped_call(cpp_parser: CppParser) -> None:
    src = 'class T {\npublic:\n    void run() { Server::create("x"); }\n};\n'
    result = cpp_parser.parse(Path("t.cpp"), src)
    assert any(e.dst_id == "cpp:?call:create" for e in _call_edges(result))


# ---------- fixture end-to-end ----------


def test_fixture_c_emits_expected_entities(c_parser: CParser) -> None:
    src = Path("tests/fixtures/sample_repo_c_cpp/src/server.c").read_text(encoding="utf-8")
    result = c_parser.parse(Path("src/server.c"), src)
    names = {e.name for e in result.entities}

    assert "Server" in names  # typedef struct
    assert "Config" in names  # named struct
    assert "server_new" in names
    assert "server_start" in names
    assert "server_listen" in names
    assert "main" in names

    server = _by_name(result, "Server")
    assert server is not None and server.type == EntityType.CLASS

    server_start = _by_name(result, "server_start")
    assert server_start is not None and server_start.is_exported is True

    server_listen = _by_name(result, "server_listen")
    assert server_listen is not None and server_listen.is_exported is False  # static

    # includes
    import_edges = _import_edges(result)
    assert any("stdio.h" in e.dst_id for e in import_edges)
    assert any("server.h" in e.dst_id for e in import_edges)

    # server_start calls server_listen
    call_edges = _call_edges(result)
    assert any(e.dst_id == "c:?call:server_listen" for e in call_edges)


def test_fixture_cpp_emits_expected_entities(cpp_parser: CppParser) -> None:
    src = Path("tests/fixtures/sample_repo_c_cpp/src/server.cpp").read_text(encoding="utf-8")
    result = cpp_parser.parse(Path("src/server.cpp"), src)
    names = {e.name for e in result.entities}

    assert "Server" in names
    assert "Handler" in names
    assert "start" in names
    assert "listen" in names
    assert "create" in names
    assert "greet" in names

    server = _by_name(result, "Server")
    assert server is not None and server.type == EntityType.CLASS

    start = _by_name(result, "start")
    assert start is not None
    assert start.qualified_name == "Server.start"
    assert start.is_exported is True
    assert start.parent_id == server.entity_id

    listen = _by_name(result, "listen")
    assert listen is not None and listen.is_exported is False

    # includes
    import_edges = _import_edges(result)
    assert any("string" in e.dst_id for e in import_edges)

    # start() calls this->listen() — receiver type inferred as Server
    call_edges = _call_edges(result)
    assert any(e.dst_id == "cpp:?methodcall:Server.listen" for e in call_edges)
