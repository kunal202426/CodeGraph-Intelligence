"""Tests for the Python parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.python import PythonParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def parser() -> PythonParser:
    return PythonParser()


@pytest.fixture
def fixture_login_path() -> Path:
    return Path("auth/login.py")


@pytest.fixture
def fixture_login_source() -> str:
    return Path("tests/fixtures/sample_repo_py/auth/login.py").read_text(encoding="utf-8")


def _by_name(result: ParseResult, name: str):
    return next((e for e in result.entities if e.name == name), None)


# ---------- protocol conformance ----------


def test_parser_implements_iparser_protocol(parser: PythonParser) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == Language.PYTHON


def test_empty_source_yields_only_module_entity(parser: PythonParser) -> None:
    result = parser.parse(Path("empty.py"), "")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []
    assert result.errors == []


# ---------- top-level function ----------


def test_extracts_top_level_function(parser: PythonParser) -> None:
    src = "def authenticate(email, password):\n    return True\n"
    result = parser.parse(Path("auth.py"), src)
    fn = _by_name(result, "authenticate")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.qualified_name == "authenticate"
    assert fn.entity_id == "py:auth.py:authenticate"
    assert fn.parent_id == "py:auth.py:auth"  # module is the parent
    assert fn.start_line == 1
    assert fn.signature == "def authenticate(email, password)"
    assert fn.is_async is False
    assert fn.is_exported is True


def test_private_function_marked_not_exported(parser: PythonParser) -> None:
    src = "def _helper():\n    return 1\n"
    result = parser.parse(Path("x.py"), src)
    fn = _by_name(result, "_helper")
    assert fn is not None
    assert fn.is_exported is False


# ---------- async ----------


def test_async_function_flagged(parser: PythonParser) -> None:
    src = "async def fetch_user(uid):\n    return {}\n"
    result = parser.parse(Path("api.py"), src)
    fn = _by_name(result, "fetch_user")
    assert fn is not None
    assert fn.is_async is True


# ---------- decorated ----------


def test_decorator_in_raw_source(parser: PythonParser) -> None:
    src = '@staticmethod\ndef make_token():\n    """mint."""\n    return ""\n'
    result = parser.parse(Path("t.py"), src)
    fn = _by_name(result, "make_token")
    assert fn is not None
    assert fn.raw_source.startswith("@staticmethod"), (
        f"raw_source should include decorator, got: {fn.raw_source!r}"
    )
    assert fn.start_line == 1  # span starts at the decorator line


# ---------- class + method ----------


def test_class_method_emits_with_parent_id(parser: PythonParser) -> None:
    src = "class LoginForm:\n    def validate(self):\n        return True\n"
    result = parser.parse(Path("auth/login.py"), src)
    cls = _by_name(result, "LoginForm")
    method = _by_name(result, "validate")
    assert cls is not None and cls.type == EntityType.CLASS
    assert method is not None and method.type == EntityType.METHOD
    assert method.qualified_name == "LoginForm.validate"
    assert method.entity_id == "py:auth/login.py:LoginForm.validate"
    assert method.parent_id == cls.entity_id


def test_async_method_carries_async_flag(parser: PythonParser) -> None:
    src = "class C:\n    async def go(self):\n        return None\n"
    result = parser.parse(Path("c.py"), src)
    method = _by_name(result, "go")
    assert method is not None
    assert method.is_async is True
    assert method.type == EntityType.METHOD


def test_nested_class_qualified_name(parser: PythonParser) -> None:
    src = "class Outer:\n    class Inner:\n        def m(self):\n            return 1\n"
    result = parser.parse(Path("n.py"), src)
    inner = _by_name(result, "Inner")
    assert inner is not None
    assert inner.qualified_name == "Outer.Inner"
    method = _by_name(result, "m")
    assert method is not None
    assert method.qualified_name == "Outer.Inner.m"
    assert method.parent_id == inner.entity_id


# ---------- docstrings ----------


def test_extracts_function_docstring_dedented(parser: PythonParser) -> None:
    src = 'def f():\n    """Hello.\n\n    World.\n    """\n    return 1\n'
    result = parser.parse(Path("d.py"), src)
    fn = _by_name(result, "f")
    assert fn is not None
    assert fn.docstring is not None
    assert "Hello." in fn.docstring
    assert fn.docstring.startswith("Hello.")
    # cleandoc dedents the continuation lines so "World." is flush left
    assert "    World." not in fn.docstring
    assert "World." in fn.docstring


def test_extracts_module_docstring(parser: PythonParser) -> None:
    src = '"""Module docstring."""\n\ndef f():\n    return 1\n'
    result = parser.parse(Path("m.py"), src)
    module = next(e for e in result.entities if e.type == EntityType.MODULE)
    assert module.docstring == "Module docstring."


def test_no_docstring_when_first_stmt_is_code(parser: PythonParser) -> None:
    src = "def f():\n    x = 1\n    return x\n"
    result = parser.parse(Path("x.py"), src)
    fn = _by_name(result, "f")
    assert fn is not None
    assert fn.docstring is None


# ---------- fixture end-to-end ----------


def test_fixture_login_emits_expected_entities(
    parser: PythonParser, fixture_login_path: Path, fixture_login_source: str
) -> None:
    result = parser.parse(fixture_login_path, fixture_login_source)
    names = {e.name for e in result.entities}
    # Module + 3 top-level funcs + 2 classes + 4 methods + 1 method on private class = 11
    assert "authenticate" in names
    assert "fetch_user" in names
    assert "make_token" in names
    assert "LoginForm" in names
    assert "_PrivateForm" in names
    assert "validate" in names
    assert "submit" in names
    assert "helper" in names

    # async detection
    fetch = _by_name(result, "fetch_user")
    assert fetch is not None and fetch.is_async is True
    submit = _by_name(result, "submit")
    assert submit is not None and submit.is_async is True

    # method has parent_id pointing at LoginForm
    validate = _by_name(result, "validate")
    cls = _by_name(result, "LoginForm")
    assert validate is not None and cls is not None
    assert validate.parent_id == cls.entity_id

    # private class not exported
    pcls = _by_name(result, "_PrivateForm")
    assert pcls is not None and pcls.is_exported is False
