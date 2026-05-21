"""Tests for the TypeScript / TSX / JS parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser, ParseResult
from codegraph.parsers.typescript import TypeScriptParser
from codegraph.uir import EntityType, Language


@pytest.fixture
def parser() -> TypeScriptParser:
    return TypeScriptParser()


def _by_name(result: ParseResult, name: str):
    return next((e for e in result.entities if e.name == name), None)


# ---------- protocol ----------


def test_parser_implements_iparser_protocol(parser: TypeScriptParser) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == Language.TYPESCRIPT


def test_empty_source_yields_only_module_entity(parser: TypeScriptParser) -> None:
    result = parser.parse(Path("empty.ts"), "")
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.entities[0].language == Language.TYPESCRIPT


# ---------- top-level function ----------


def test_extracts_function_declaration(parser: TypeScriptParser) -> None:
    src = "function authenticate(email: string, password: string): boolean { return true; }\n"
    result = parser.parse(Path("auth.ts"), src)
    fn = _by_name(result, "authenticate")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.entity_id == "ts:auth.ts:authenticate"
    assert fn.is_exported is False  # no `export` keyword
    assert fn.is_async is False
    assert fn.signature is not None and fn.signature.startswith("function authenticate(")


def test_export_function_marked_exported(parser: TypeScriptParser) -> None:
    src = "export function authenticate(): boolean { return true; }\n"
    result = parser.parse(Path("auth.ts"), src)
    fn = _by_name(result, "authenticate")
    assert fn is not None and fn.is_exported is True
    assert fn.raw_source.startswith("export "), fn.raw_source


def test_export_default_function_marked_exported(parser: TypeScriptParser) -> None:
    src = "export default function authenticate() { return 1; }\n"
    result = parser.parse(Path("auth.ts"), src)
    fn = _by_name(result, "authenticate")
    assert fn is not None and fn.is_exported is True


def test_async_function_flagged(parser: TypeScriptParser) -> None:
    src = "export async function fetchUser(id: number) { return {}; }\n"
    result = parser.parse(Path("api.ts"), src)
    fn = _by_name(result, "fetchUser")
    assert fn is not None and fn.is_async is True and fn.is_exported is True


# ---------- arrow functions ----------


def test_const_arrow_emitted_as_function(parser: TypeScriptParser) -> None:
    src = "const compute = (x: number) => x + 1;\n"
    result = parser.parse(Path("util.ts"), src)
    fn = _by_name(result, "compute")
    assert fn is not None
    assert fn.type == EntityType.FUNCTION
    assert fn.is_async is False


def test_export_const_arrow_marked_exported(parser: TypeScriptParser) -> None:
    src = "export const compute = (x: number) => x + 1;\n"
    result = parser.parse(Path("util.ts"), src)
    fn = _by_name(result, "compute")
    assert fn is not None and fn.is_exported is True


def test_async_arrow_flagged(parser: TypeScriptParser) -> None:
    src = "const fetchIt = async (id: number) => id;\n"
    result = parser.parse(Path("api.ts"), src)
    fn = _by_name(result, "fetchIt")
    assert fn is not None and fn.is_async is True


# ---------- classes + methods ----------


def test_class_declaration_with_methods(parser: TypeScriptParser) -> None:
    src = (
        "export class LoginForm {\n"
        "  validate(): boolean { return true; }\n"
        "  async submit() { return null; }\n"
        "}\n"
    )
    result = parser.parse(Path("auth/form.ts"), src)
    cls = _by_name(result, "LoginForm")
    assert cls is not None and cls.type == EntityType.CLASS and cls.is_exported is True

    validate = _by_name(result, "validate")
    submit = _by_name(result, "submit")
    assert validate is not None and validate.type == EntityType.METHOD
    assert validate.qualified_name == "LoginForm.validate"
    assert validate.parent_id == cls.entity_id
    assert submit is not None and submit.is_async is True


def test_class_method_inherits_parent_id_chain(parser: TypeScriptParser) -> None:
    src = "class C { method_a() {} }\n"
    result = parser.parse(Path("c.ts"), src)
    method = _by_name(result, "method_a")
    cls = _by_name(result, "C")
    assert method is not None and cls is not None
    assert method.qualified_name == "C.method_a"
    assert method.parent_id == cls.entity_id


# ---------- interfaces ----------


def test_interface_declaration_emitted(parser: TypeScriptParser) -> None:
    src = "export interface IUser { id: number; email: string; }\n"
    result = parser.parse(Path("models.ts"), src)
    iface = _by_name(result, "IUser")
    assert iface is not None and iface.type == EntityType.INTERFACE
    assert iface.is_exported is True


def test_interface_without_export_is_not_exported(parser: TypeScriptParser) -> None:
    src = "interface Internal { foo: string; }\n"
    result = parser.parse(Path("m.ts"), src)
    iface = _by_name(result, "Internal")
    assert iface is not None and iface.is_exported is False


# ---------- JSX / TSX ----------


def test_tsx_function_with_jsx_body_parses(parser: TypeScriptParser) -> None:
    src = "export function App() { return <div>hi</div>; }\n"
    result = parser.parse(Path("App.tsx"), src)
    fn = _by_name(result, "App")
    assert fn is not None and fn.is_exported is True
    assert result.errors == []


def test_jsx_file_uses_javascript_language(parser: TypeScriptParser) -> None:
    src = "export function App() { return <div>hi</div>; }\n"
    result = parser.parse(Path("App.jsx"), src)
    fn = _by_name(result, "App")
    assert fn is not None and fn.language == Language.JAVASCRIPT


def test_plain_js_file_uses_javascript_language(parser: TypeScriptParser) -> None:
    src = "export function hi() { return 1; }\n"
    result = parser.parse(Path("hi.js"), src)
    fn = _by_name(result, "hi")
    assert fn is not None and fn.language == Language.JAVASCRIPT
    assert fn.entity_id.startswith("js:")


# ---------- imports (T2.5) ----------


def _import_edges(result):
    return [e for e in result.edges if e.type == "imports"]


def test_named_import_relative(parser: TypeScriptParser) -> None:
    src = 'import { authenticate } from "./auth/login";\n'
    result = parser.parse(Path("src/index.ts"), src)
    edges = _import_edges(result)
    assert [e.dst_id for e in edges] == ["ts:?:./auth/login::authenticate"]
    assert edges[0].src_id == "ts:src/index.ts:src.index"


def test_named_import_multiple(parser: TypeScriptParser) -> None:
    src = 'import { a, b } from "./mod";\n'
    edges = _import_edges(parser.parse(Path("x.ts"), src))
    assert {e.dst_id for e in edges} == {"ts:?:./mod::a", "ts:?:./mod::b"}


def test_named_aliased_import_uses_target_name(parser: TypeScriptParser) -> None:
    src = 'import { authenticate as auth } from "./mod";\n'
    edges = _import_edges(parser.parse(Path("x.ts"), src))
    assert [e.dst_id for e in edges] == ["ts:?:./mod::authenticate"]


def test_default_import(parser: TypeScriptParser) -> None:
    src = 'import auth from "./mod";\n'
    edges = _import_edges(parser.parse(Path("x.ts"), src))
    assert [e.dst_id for e in edges] == ["ts:?:./mod::default"]


def test_namespace_import(parser: TypeScriptParser) -> None:
    src = 'import * as A from "./mod";\n'
    edges = _import_edges(parser.parse(Path("x.ts"), src))
    assert [e.dst_id for e in edges] == ["ts:?:./mod::*"]


def test_side_effect_import(parser: TypeScriptParser) -> None:
    src = 'import "./side-effects";\n'
    edges = _import_edges(parser.parse(Path("x.ts"), src))
    assert [e.dst_id for e in edges] == ["ts:?:./side-effects"]


def test_default_plus_named_import(parser: TypeScriptParser) -> None:
    src = 'import auth, { LoginForm } from "./mod";\n'
    edges = _import_edges(parser.parse(Path("x.ts"), src))
    assert {e.dst_id for e in edges} == {"ts:?:./mod::default", "ts:?:./mod::LoginForm"}


def test_bare_specifier_import_passes_through(parser: TypeScriptParser) -> None:
    """Resolver decides bare-vs-relative; parser only encodes the specifier."""
    src = 'import { useState } from "react";\n'
    edges = _import_edges(parser.parse(Path("x.tsx"), src))
    assert [e.dst_id for e in edges] == ["ts:?:react::useState"]


def test_relative_parent_dir_import(parser: TypeScriptParser) -> None:
    src = 'import { helper } from "../shared/util";\n'
    edges = _import_edges(parser.parse(Path("src/sub/x.ts"), src))
    assert [e.dst_id for e in edges] == ["ts:?:../shared/util::helper"]


def test_import_line_numbers_correct(parser: TypeScriptParser) -> None:
    src = '\n\nimport { a } from "./mod";\n\nimport b from "./b";\n'
    edges = _import_edges(parser.parse(Path("x.ts"), src))
    lines = {e.dst_id: e.line for e in edges}
    assert lines["ts:?:./mod::a"] == 3
    assert lines["ts:?:./b::default"] == 5


def test_no_import_edges_when_no_imports(parser: TypeScriptParser) -> None:
    edges = _import_edges(parser.parse(Path("x.ts"), "export function f() { return 1; }\n"))
    assert edges == []


# ---------- fixture end-to-end ----------


def test_fixture_login_ts_emits_expected_entities(parser: TypeScriptParser) -> None:
    src = Path("tests/fixtures/sample_repo_ts/src/auth/login.ts").read_text(encoding="utf-8")
    result = parser.parse(Path("src/auth/login.ts"), src)
    names = {e.name for e in result.entities}
    for expected in {
        "authenticate",
        "fetchUser",
        "computeRole",
        "LoginForm",
        "validate",
        "submit",
        "Session",
        "InternalCache",
        "store",
    }:
        assert expected in names, f"missing {expected!r} from {sorted(names)}"

    # is_exported flags
    assert _by_name(result, "authenticate").is_exported is True
    assert _by_name(result, "InternalCache").is_exported is False
    assert _by_name(result, "Session").type == EntityType.INTERFACE
    assert _by_name(result, "fetchUser").is_async is True
    assert _by_name(result, "submit").is_async is True


def test_fixture_index_tsx_emits_app_and_default(parser: TypeScriptParser) -> None:
    src = Path("tests/fixtures/sample_repo_ts/src/index.tsx").read_text(encoding="utf-8")
    result = parser.parse(Path("src/index.tsx"), src)
    assert _by_name(result, "App") is not None
    assert result.errors == []
