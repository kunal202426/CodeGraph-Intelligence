"""Tests for the UIR contract — entity IDs, serialization, validation, hashing."""

from __future__ import annotations

import json

import pytest
from codegraph.uir import (
    Edge,
    EntityType,
    Language,
    UIREntity,
    hash_source,
    make_entity_id,
)
from pydantic import ValidationError


def _sample_entity(**overrides) -> UIREntity:
    defaults = dict(
        entity_id="py:src/auth/login.py:authenticate",
        type=EntityType.FUNCTION,
        name="authenticate",
        qualified_name="authenticate",
        language=Language.PYTHON,
        file="src/auth/login.py",
        start_line=10,
        end_line=20,
        raw_source="def authenticate(email, password):\n    return True\n",
        hash=hash_source("def authenticate(email, password):\n    return True\n"),
    )
    defaults.update(overrides)
    return UIREntity(**defaults)


# ---------- make_entity_id ----------


def test_make_entity_id_python() -> None:
    assert (
        make_entity_id(Language.PYTHON, "src/auth/login.py", "authenticate")
        == "py:src/auth/login.py:authenticate"
    )


def test_make_entity_id_typescript() -> None:
    assert (
        make_entity_id(Language.TYPESCRIPT, "src/auth/login.ts", "authenticate")
        == "ts:src/auth/login.ts:authenticate"
    )


def test_make_entity_id_method_qualified() -> None:
    assert (
        make_entity_id(Language.PYTHON, "src/auth/login.py", "LoginForm.validate")
        == "py:src/auth/login.py:LoginForm.validate"
    )


def test_make_entity_id_rejects_backslash() -> None:
    with pytest.raises(ValueError, match="forward slashes"):
        make_entity_id(Language.PYTHON, "src\\auth\\login.py", "authenticate")


# ---------- hash_source ----------


def test_hash_source_is_deterministic() -> None:
    s = "def foo(): pass\n"
    assert hash_source(s) == hash_source(s)


def test_hash_source_differs_on_change() -> None:
    assert hash_source("def foo(): pass\n") != hash_source("def bar(): pass\n")


def test_hash_source_is_sha256_hex() -> None:
    h = hash_source("anything")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ---------- UIREntity ----------


def test_entity_roundtrip_json() -> None:
    e = _sample_entity()
    blob = e.model_dump_json()
    restored = UIREntity(**json.loads(blob))
    assert restored == e


def test_entity_defaults() -> None:
    e = _sample_entity()
    assert e.docstring is None
    assert e.signature is None
    assert e.is_exported is True
    assert e.is_async is False
    assert e.parent_id is None
    assert e.summary is None
    assert e.embedding_id is None
    assert e.start_col == 0
    assert e.end_col == 0


def test_entity_rejects_backslash_in_file() -> None:
    with pytest.raises(ValidationError, match="forward slashes"):
        _sample_entity(file="src\\auth\\login.py")


def test_entity_rejects_end_line_before_start() -> None:
    with pytest.raises(ValidationError, match="end_line.*must be >="):
        _sample_entity(start_line=20, end_line=10)


def test_entity_rejects_zero_line_numbers() -> None:
    with pytest.raises(ValidationError):
        _sample_entity(start_line=0)


def test_method_entity_carries_parent_id() -> None:
    e = _sample_entity(
        entity_id="py:src/auth/login.py:LoginForm.validate",
        type=EntityType.METHOD,
        qualified_name="LoginForm.validate",
        parent_id="py:src/auth/login.py:LoginForm",
    )
    assert e.parent_id == "py:src/auth/login.py:LoginForm"


# ---------- Edge ----------


def test_edge_basic_construction() -> None:
    edge = Edge(
        src_id="py:src/a.py:foo",
        dst_id="py:src/b.py:bar",
        type="calls",
        line=42,
    )
    assert edge.confidence == 1.0
    assert edge.is_dynamic is False


def test_edge_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError):
        Edge(src_id="a", dst_id="b", type="invented", line=1)  # type: ignore[arg-type]


def test_edge_clamps_confidence_range() -> None:
    with pytest.raises(ValidationError):
        Edge(src_id="a", dst_id="b", type="calls", line=1, confidence=1.5)
    with pytest.raises(ValidationError):
        Edge(src_id="a", dst_id="b", type="calls", line=1, confidence=-0.1)


def test_edge_roundtrip_json() -> None:
    edge = Edge(
        src_id="py:src/a.py:foo",
        dst_id="py:src/b.py:bar",
        type="imports",
        line=3,
        confidence=0.7,
        is_dynamic=True,
    )
    blob = edge.model_dump_json()
    restored = Edge(**json.loads(blob))
    assert restored == edge


# ---------- Enums ----------


def test_language_prefix_coverage() -> None:
    from codegraph.uir import LANGUAGE_PREFIX

    for lang in Language:
        assert lang in LANGUAGE_PREFIX, f"missing prefix for {lang}"


def test_entity_type_values_match_strings() -> None:
    assert EntityType.FUNCTION.value == "function"
    assert EntityType.CLASS.value == "class"
    assert EntityType.METHOD.value == "method"
