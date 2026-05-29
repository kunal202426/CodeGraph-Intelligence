"""Unified Intermediate Representation.

Every parser emits UIREntity + Edge. Every store, search, and AI layer consumes
them. This module is the contract — change with care and bump BUILD_PLAN §3
together with any schema modification.

Entity ID format (LOCKED): "<lang>:<file>:<qualified_name>"
  - lang: short prefix (py, ts, js) — see `LANGUAGE_PREFIX`
  - file: repo-relative path with forward slashes
  - qualified_name: dotted path including parent classes (e.g. "LoginForm.validate")
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class EntityType(StrEnum):
    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"
    VARIABLE = "variable"


class Language(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    RUBY = "ruby"


LANGUAGE_PREFIX: dict[Language, str] = {
    Language.PYTHON: "py",
    Language.TYPESCRIPT: "ts",
    Language.JAVASCRIPT: "js",
    Language.GO: "go",
    Language.RUST: "rs",
    Language.JAVA: "java",
    Language.RUBY: "rb",
}


EdgeType = Literal["imports", "calls", "inherits", "implements", "contains"]


class UIREntity(BaseModel):
    """A single code entity (module, function, class, method, ...)."""

    # Identity
    entity_id: str
    type: EntityType
    name: str
    qualified_name: str
    language: Language

    # Location (1-indexed lines, 0-indexed columns)
    file: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    start_col: int = Field(default=0, ge=0)
    end_col: int = Field(default=0, ge=0)

    # Source
    raw_source: str
    docstring: str | None = None
    signature: str | None = None

    # Metadata
    is_exported: bool = True
    is_async: bool = False
    parent_id: str | None = None

    # Hash of raw_source (for incremental re-parse skip in T2.3)
    hash: str

    # AI metadata (populated in later phases — Phase 3 + 5)
    summary: str | None = None
    embedding_id: int | None = None

    @field_validator("file")
    @classmethod
    def _no_backslash_in_file(cls, v: str) -> str:
        if "\\" in v:
            raise ValueError("file must use forward slashes")
        return v

    @field_validator("end_line")
    @classmethod
    def _end_line_not_before_start(cls, v: int, info) -> int:
        start = info.data.get("start_line")
        if start is not None and v < start:
            raise ValueError(f"end_line ({v}) must be >= start_line ({start})")
        return v


class Edge(BaseModel):
    """Directed edge between two entities (calls, imports, inheritance, ...)."""

    src_id: str
    dst_id: str
    type: EdgeType
    line: int = Field(ge=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    is_dynamic: bool = False


def make_entity_id(language: Language, file: str, qualified_name: str) -> str:
    """Build the canonical entity_id string. Format is part of the public contract."""
    if "\\" in file:
        raise ValueError("file must use forward slashes")
    prefix = LANGUAGE_PREFIX[language]
    return f"{prefix}:{file}:{qualified_name}"


def hash_source(raw_source: str) -> str:
    """SHA-256 of source bytes — deterministic, used for incremental re-parse skip."""
    return hashlib.sha256(raw_source.encode("utf-8")).hexdigest()
