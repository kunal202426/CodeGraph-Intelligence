# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Parser contract.

Every language parser implements `IParser`. Each `parse()` call takes a single
file's path + source text and returns a `ParseResult` envelope containing the
emitted entities, edges, and any non-fatal errors. Parsers must not raise on
malformed input — return a result with `errors` populated instead, so the
indexing pipeline can keep going.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from codegraph.uir import Edge, Language, UIREntity


class ParseResult(BaseModel):
    """Output of a single-file parse."""

    entities: list[UIREntity] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


@runtime_checkable
class IParser(Protocol):
    """A language parser. Stateless; safe to reuse across files."""

    language: Language

    def parse(self, path: Path, source: str) -> ParseResult: ...
