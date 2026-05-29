"""Tests for the PHP parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.php import PHPParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def parser() -> PHPParser:
    return PHPParser()


def _by_name(result: ParseResult, name: str):
    # Prefer non-module entities on name collision
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


def test_parser_implements_iparser_protocol(parser: PHPParser) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == Language.PHP


def test_empty_source_yields_only_module_entity(parser: PHPParser) -> None:
    result = parser.parse(Path("src/Server.php"), "<?php\n")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []
    assert result.errors == []


# ---------- module entity ----------


def test_module_name_derived_from_path(parser: PHPParser) -> None:
    result = parser.parse(Path("src/Server.php"), "<?php\n")
    m = result.entities[0]
    assert m.name == "src.Server"
    assert m.qualified_name == "src.Server"
    assert m.entity_id == "php:src/Server.php:src.Server"
    assert m.language == Language.PHP


def test_module_flat_path(parser: PHPParser) -> None:
    result = parser.parse(Path("server.php"), "<?php\n")
    assert result.entities[0].name == "server"
    assert result.entities[0].entity_id == "php:server.php:server"


# ---------- class ----------


def test_extracts_class(parser: PHPParser) -> None:
    src = "<?php\nclass Server {}"
    result = parser.parse(Path("Server.php"), src)
    cls = _by_name(result, "Server")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.entity_id == "php:Server.php:Server"
    assert cls.is_exported is True
    assert "Server" in (cls.signature or "")


def test_extracts_trait_as_class(parser: PHPParser) -> None:
    src = "<?php\ntrait Logging { public function log(string $msg): void {} }"
    result = parser.parse(Path("Logging.php"), src)
    t = _by_name(result, "Logging")
    assert t is not None
    assert t.type == EntityType.CLASS


def test_is_async_always_false(parser: PHPParser) -> None:
    src = "<?php\nclass Foo { public function bar(): void {} }"
    result = parser.parse(Path("Foo.php"), src)
    for entity in result.entities:
        assert entity.is_async is False


# ---------- interface ----------


def test_extracts_interface(parser: PHPParser) -> None:
    src = "<?php\ninterface Handler { public function handle(string $req): string; }"
    result = parser.parse(Path("Handler.php"), src)
    iface = _by_name(result, "Handler")
    assert iface is not None
    assert iface.type == EntityType.INTERFACE
    assert iface.is_exported is True
    assert "Handler" in (iface.signature or "")


# ---------- top-level function ----------


def test_extracts_top_level_function(parser: PHPParser) -> None:
    src = '<?php\nfunction greet(string $name): string { return "Hello"; }'
    result = parser.parse(Path("utils.php"), src)
    fn = _by_name(result, "greet")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.qualified_name == "greet"
    assert fn.is_exported is True


# ---------- methods ----------


def test_methods_emitted_with_qualified_names(parser: PHPParser) -> None:
    src = "<?php\nclass Server {\n    public function start(): void {}\n    private function listen(): void {}\n}"
    result = parser.parse(Path("Server.php"), src)
    start = _by_name(result, "start")
    assert start is not None
    assert start.type == EntityType.METHOD
    assert start.qualified_name == "Server.start"
    assert start.entity_id == "php:Server.php:Server.start"
    assert start.is_exported is True

    listen = _by_name(result, "listen")
    assert listen is not None
    assert listen.qualified_name == "Server.listen"
    assert listen.is_exported is False


def test_method_parent_id_points_to_class(parser: PHPParser) -> None:
    src = "<?php\nclass Foo {\n    public function bar(): void {}\n}"
    result = parser.parse(Path("Foo.php"), src)
    method = _by_name(result, "bar")
    cls = _by_name(result, "Foo")
    assert method is not None and cls is not None
    assert method.parent_id == cls.entity_id


def test_static_method_emitted(parser: PHPParser) -> None:
    src = "<?php\nclass Server {\n    public static function create(string $h): self { return new self($h, 8080); }\n}"
    result = parser.parse(Path("Server.php"), src)
    m = _by_name(result, "create")
    assert m is not None
    assert m.type == EntityType.METHOD
    assert m.is_exported is True


def test_interface_method_emitted(parser: PHPParser) -> None:
    src = "<?php\ninterface Handler {\n    public function handle(string $req): string;\n}"
    result = parser.parse(Path("Handler.php"), src)
    m = _by_name(result, "handle")
    assert m is not None
    assert m.type == EntityType.METHOD
    assert m.qualified_name == "Handler.handle"
    assert m.parent_id == _by_name(result, "Handler").entity_id


# ---------- imports — use ----------


def test_simple_use(parser: PHPParser) -> None:
    src = "<?php\nuse App\\Http\\Request;\n"
    result = parser.parse(Path("main.php"), src)
    edges = _import_edges(result)
    assert any("App\\Http\\Request" in e.dst_id for e in edges)


def test_grouped_use(parser: PHPParser) -> None:
    src = "<?php\nuse App\\Http\\{Response, Middleware};\n"
    result = parser.parse(Path("main.php"), src)
    dst_ids = {e.dst_id for e in _import_edges(result)}
    assert any("Response" in d for d in dst_ids)
    assert any("Middleware" in d for d in dst_ids)


def test_require_once_emits_import(parser: PHPParser) -> None:
    src = "<?php\nrequire_once 'vendor/autoload.php';\n"
    result = parser.parse(Path("main.php"), src)
    edges = _import_edges(result)
    assert any("vendor/autoload.php" in e.dst_id for e in edges)


def test_include_emits_import(parser: PHPParser) -> None:
    src = "<?php\ninclude 'helper.php';\n"
    result = parser.parse(Path("main.php"), src)
    edges = _import_edges(result)
    assert any("helper.php" in e.dst_id for e in edges)


def test_import_src_is_module_entity(parser: PHPParser) -> None:
    src = "<?php\nuse App\\Http\\Request;\n"
    result = parser.parse(Path("main.php"), src)
    edges = _import_edges(result)
    assert edges[0].src_id == "php:main.php:main"


# ---------- calls ----------


def test_function_call_edge(parser: PHPParser) -> None:
    src = '<?php\nfunction run(): void { greet("world"); }\n'
    result = parser.parse(Path("main.php"), src)
    assert any(e.dst_id == "php:?call:greet" for e in _call_edges(result))


def test_member_call_edge(parser: PHPParser) -> None:
    src = "<?php\nclass T {\n    public function run(): void { $this->listen(); }\n}"
    result = parser.parse(Path("T.php"), src)
    assert any(e.dst_id == "php:?call:listen" for e in _call_edges(result))


def test_scoped_call_edge(parser: PHPParser) -> None:
    src = '<?php\nclass T {\n    public function run(): void { self::create("x"); }\n}'
    result = parser.parse(Path("T.php"), src)
    assert any(e.dst_id == "php:?call:create" for e in _call_edges(result))


# ---------- fixture end-to-end ----------


def test_fixture_server_emits_expected_entities(parser: PHPParser) -> None:
    src = Path("tests/fixtures/sample_repo_php/src/Server.php").read_text(encoding="utf-8")
    result = parser.parse(Path("src/Server.php"), src)
    names = {e.name for e in result.entities}

    assert "Server" in names
    assert "Handler" in names
    assert "Logging" in names
    assert "start" in names
    assert "listen" in names
    assert "create" in names
    assert "handle" in names
    assert "greet" in names

    server = _by_name(result, "Server")
    assert server is not None and server.type == EntityType.CLASS

    handler = _by_name(result, "Handler")
    assert handler is not None and handler.type == EntityType.INTERFACE

    logging = _by_name(result, "Logging")
    assert logging is not None and logging.type == EntityType.CLASS

    start = _by_name(result, "start")
    assert start is not None
    assert start.qualified_name == "Server.start"
    assert start.is_exported is True
    assert start.parent_id == server.entity_id

    listen = _by_name(result, "listen")
    assert listen is not None and listen.is_exported is False

    # start() calls $this->listen()
    call_edges = _call_edges(result)
    assert any(e.dst_id == "php:?call:listen" for e in call_edges)

    # use imports
    import_edges = _import_edges(result)
    assert any("Request" in e.dst_id for e in import_edges)
    assert any("vendor/autoload.php" in e.dst_id for e in import_edges)
