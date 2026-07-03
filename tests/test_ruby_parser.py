"""Tests for the Ruby parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.ruby import RubyParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def parser() -> RubyParser:
    return RubyParser()


def _by_name(result: ParseResult, name: str):
    # Prefer non-module entities on name collision (e.g. server.rb → "server" module)
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


def test_parser_implements_iparser_protocol(parser: RubyParser) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == Language.RUBY


def test_empty_source_yields_only_module_entity(parser: RubyParser) -> None:
    result = parser.parse(Path("lib/server.rb"), "")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []
    assert result.errors == []


# ---------- module entity ----------


def test_module_name_derived_from_path(parser: RubyParser) -> None:
    result = parser.parse(Path("lib/server.rb"), "")
    m = result.entities[0]
    assert m.name == "lib.server"
    assert m.qualified_name == "lib.server"
    assert m.entity_id == "rb:lib/server.rb:lib.server"
    assert m.language == Language.RUBY


def test_module_flat_path(parser: RubyParser) -> None:
    result = parser.parse(Path("server.rb"), "")
    assert result.entities[0].name == "server"
    assert result.entities[0].entity_id == "rb:server.rb:server"


# ---------- class ----------


def test_extracts_class(parser: RubyParser) -> None:
    src = "class Server\nend\n"
    result = parser.parse(Path("server.rb"), src)
    cls = _by_name(result, "Server")
    assert cls is not None
    assert cls.type == EntityType.CLASS
    assert cls.entity_id == "rb:server.rb:Server"
    assert cls.is_exported is True
    assert "Server" in (cls.signature or "")


# ---------- module ----------


def test_extracts_module_as_class(parser: RubyParser) -> None:
    src = "module Handler\nend\n"
    result = parser.parse(Path("handler.rb"), src)
    mod = _by_name(result, "Handler")
    assert mod is not None
    assert mod.type == EntityType.CLASS
    assert mod.is_exported is True
    assert "Handler" in (mod.signature or "")


# ---------- top-level function ----------


def test_extracts_top_level_def(parser: RubyParser) -> None:
    src = 'def greet(name)\n  "Hello"\nend\n'
    result = parser.parse(Path("utils.rb"), src)
    fn = _by_name(result, "greet")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.qualified_name == "greet"
    assert fn.entity_id == "rb:utils.rb:greet"
    assert fn.is_exported is True


def test_is_async_always_false(parser: RubyParser) -> None:
    src = "def greet\n  'hi'\nend\n"
    result = parser.parse(Path("utils.rb"), src)
    for entity in result.entities:
        assert entity.is_async is False


# ---------- methods ----------


def test_methods_emitted_with_qualified_names(parser: RubyParser) -> None:
    src = "class Server\n  def start\n  end\n  def stop\n  end\nend\n"
    result = parser.parse(Path("server.rb"), src)
    start = _by_name(result, "start")
    assert start is not None
    assert start.type == EntityType.METHOD
    assert start.qualified_name == "Server.start"
    assert start.entity_id == "rb:server.rb:Server.start"
    assert start.is_exported is True


def test_method_parent_id_points_to_class(parser: RubyParser) -> None:
    src = "class Foo\n  def bar\n  end\nend\n"
    result = parser.parse(Path("foo.rb"), src)
    method = _by_name(result, "bar")
    cls = _by_name(result, "Foo")
    assert method is not None and cls is not None
    assert method.parent_id == cls.entity_id


def test_private_method_not_exported(parser: RubyParser) -> None:
    src = "class Server\n  def start\n  end\n  private\n  def listen\n  end\nend\n"
    result = parser.parse(Path("server.rb"), src)
    start = _by_name(result, "start")
    listen = _by_name(result, "listen")
    assert start is not None and start.is_exported is True
    assert listen is not None and listen.is_exported is False


def test_singleton_method_always_exported(parser: RubyParser) -> None:
    src = "class Server\n  private\n  def self.create(host)\n    new(host)\n  end\nend\n"
    result = parser.parse(Path("server.rb"), src)
    m = _by_name(result, "create")
    assert m is not None
    assert m.type == EntityType.METHOD
    assert m.qualified_name == "Server.create"
    assert m.is_exported is True  # singleton methods ignore private


def test_module_methods_emitted(parser: RubyParser) -> None:
    src = "module Handler\n  def handle(req)\n    'OK'\n  end\nend\n"
    result = parser.parse(Path("handler.rb"), src)
    m = _by_name(result, "handle")
    assert m is not None
    assert m.type == EntityType.METHOD
    assert m.qualified_name == "Handler.handle"


# ---------- imports ----------


def test_require_emits_import_edge(parser: RubyParser) -> None:
    src = "require 'json'\n"
    result = parser.parse(Path("main.rb"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "rb:?:json" for e in edges)


def test_require_relative_emits_import_edge(parser: RubyParser) -> None:
    src = "require_relative './server'\n"
    result = parser.parse(Path("main.rb"), src)
    edges = _import_edges(result)
    assert any(e.dst_id == "rb:?:./server" for e in edges)


def test_multiple_requires(parser: RubyParser) -> None:
    src = "require 'json'\nrequire 'net/http'\n"
    result = parser.parse(Path("main.rb"), src)
    dst_ids = {e.dst_id for e in _import_edges(result)}
    assert "rb:?:json" in dst_ids
    assert "rb:?:net/http" in dst_ids


def test_import_src_is_module_entity(parser: RubyParser) -> None:
    src = "require 'json'\n"
    result = parser.parse(Path("main.rb"), src)
    edges = _import_edges(result)
    assert edges[0].src_id == "rb:main.rb:main"


# ---------- calls ----------


def test_explicit_call_edge(parser: RubyParser) -> None:
    src = "def run\n  greet('world')\nend\n"
    result = parser.parse(Path("main.rb"), src)
    assert any(e.dst_id == "rb:?call:greet" for e in _call_edges(result))


def test_method_call_with_receiver(parser: RubyParser) -> None:
    src = "class T\n  def run\n    self.listen\n  end\nend\n"
    result = parser.parse(Path("t.rb"), src)
    # `self.listen` -- receiver type is inferred as the enclosing class T.
    assert any(e.dst_id == "rb:?methodcall:T.listen" for e in _call_edges(result))


def test_class_method_call(parser: RubyParser) -> None:
    src = "class T\n  def run\n    Server.create('x')\n  end\nend\n"
    result = parser.parse(Path("t.rb"), src)
    assert any(e.dst_id == "rb:?call:create" for e in _call_edges(result))


# ---------- fixture end-to-end ----------


def test_fixture_server_emits_expected_entities(parser: RubyParser) -> None:
    src = Path("tests/fixtures/sample_repo_ruby/lib/server.rb").read_text(encoding="utf-8")
    result = parser.parse(Path("lib/server.rb"), src)
    names = {e.name for e in result.entities}

    assert "Server" in names
    assert "Handler" in names
    assert "initialize" in names
    assert "start" in names
    assert "create" in names
    assert "listen" in names
    assert "handle" in names
    assert "greet" in names

    server = _by_name(result, "Server")
    assert server is not None and server.type == EntityType.CLASS

    handler = _by_name(result, "Handler")
    assert handler is not None and handler.type == EntityType.CLASS

    start = _by_name(result, "start")
    assert start is not None
    assert start.qualified_name == "Server.start"
    assert start.is_exported is True
    assert start.parent_id == server.entity_id

    listen = _by_name(result, "listen")
    assert listen is not None and listen.is_exported is False  # after private

    create = _by_name(result, "create")
    assert create is not None and create.is_exported is True  # singleton method

    greet = _by_name(result, "greet")
    assert greet is not None and greet.type == EntityType.FUNCTION

    # start() calls self.listen — receiver type inferred as Server
    call_edges = _call_edges(result)
    assert any(e.dst_id == "rb:?methodcall:Server.listen" for e in call_edges)

    # imports
    import_edges = _import_edges(result)
    assert any("json" in e.dst_id for e in import_edges)
    assert any("utils" in e.dst_id for e in import_edges)
