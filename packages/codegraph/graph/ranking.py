# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Search-result ranking helpers: identifier segmentation, path classifiers.

Pure functions, no DB access -- `search_literal` (graph/queries.py) uses these
to re-rank its SQL candidate pool. Kept separate from queries.py so the
heuristics (what counts as a test file, how an identifier splits into words)
can be unit-tested without a database.
"""

from __future__ import annotations

import re

# Matches a run of uppercase letters followed by an uppercase+lowercase pair
# (an acronym boundary, e.g. the "XML" in "XMLHttpRequest"), or a capitalized
# word, or a run of uppercase letters, or a run of digits. Standard
# camelCase/acronym identifier splitter.
_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

# Boundaries between words that aren't a case change: underscores, hyphens,
# dots, slashes, whitespace.
_SEPARATOR_RE = re.compile(r"[_\-.\s/]+")

# Common short/prose words excluded from multi-term co-occurrence scoring --
# without this, a query like "how does X work" would "corroborate" on "how"
# matching hundreds of unrelated names, drowning out the real signal.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "of",
        "in",
        "on",
        "for",
        "to",
        "and",
        "or",
        "it",
        "this",
        "that",
        "with",
        "as",
        "be",
        "by",
        "at",
        "how",
        "does",
        "do",
        "what",
        "where",
        "which",
        "i",
        "we",
        "you",
    }
)


def split_identifier_segments(name: str) -> list[str]:
    """Split an identifier into lowercase words on case-change and separator
    boundaries.

    `"OrderStateMachine"` -> `["order", "state", "machine"]`
    `"get_user_id"` -> `["get", "user", "id"]`
    `"XMLHttpRequest"` -> `["xml", "http", "request"]`

    Used so a search for "state machine" can find `OrderStateMachine` -- FTS
    and substring matching can't see inside a single camelCase token.
    """
    if not name:
        return []
    segments: list[str] = []
    for chunk in _SEPARATOR_RE.split(name):
        if not chunk:
            continue
        segments.extend(m.group(0).lower() for m in _WORD_RE.finditer(chunk))
    return segments


def extract_search_terms(query: str) -> list[str]:
    """Split a free-text query into lowercase whitespace-separated terms.

    Splits on whitespace only, NOT underscores/hyphens/dots -- a single
    identifier-like query (`"zzz_not_a_real_symbol"`) stays one term, so it's
    still scored as an exact/substring match attempt rather than exploding
    into fragments (`zzz`, `not`, `a`, ...) that would match almost anything.
    Only a genuine multi-word query (`"state machine"`) produces 2+ terms.
    """
    return [t.lower() for t in query.split() if t]


def significant_terms(terms: list[str]) -> list[str]:
    """Filter `extract_search_terms` output down to terms worth using for
    co-occurrence scoring/broadened matching -- drops stopwords and
    single-character terms, both of which are too common to be signal."""
    return [t for t in terms if len(t) >= 2 and t not in STOPWORDS]


# Directory-name and filename conventions that mark test/fixture/sample code
# across the languages CodeGraph indexes. Intentionally broad -- a false
# positive here only costs a minor rank demotion, not exclusion.
_TEST_DIR_RE = re.compile(
    r"(^|/)(tests?|specs?|__tests__|__mocks__|mocks?|fixtures?|samples?|"
    r"examples?|benchmarks?|testdata)(/|$)",
    re.IGNORECASE,
)
_TEST_FILE_RE = re.compile(
    r"(^|[_./])(test|tests|spec|specs)([_.]|$)|"  # test_foo.py, foo_test.go, foo.spec.ts
    r"(Test|Tests|Spec|IT)\.[A-Za-z]+$",  # FooTest.java, FooIT.java (Maven failsafe)
)


def is_test_path(file: str) -> bool:
    """True if *file* looks like a test/spec/fixture/sample file by naming
    convention (any indexed language) -- not by content."""
    if not file:
        return False
    if _TEST_DIR_RE.search(file):
        return True
    name = file.rsplit("/", 1)[-1]
    return bool(_TEST_FILE_RE.search(name))


# Path suffixes that mark machine-generated code across common codegen tools.
# Distinct from walker.looks_generated (which is content-based, for minified/
# bundled single-line files and hard-excludes them from the index entirely):
# this is a *naming* convention for normally-formatted generated source that
# stays indexed and searchable, just ranked behind hand-written code on a
# name collision.
_GENERATED_SUFFIXES = (
    ".pb.go",
    "_grpc.pb.go",
    ".pb.ts",
    "_pb.ts",
    "_pb2.py",
    "_pb2_grpc.py",
    ".g.dart",
    ".g.cs",
    ".g.ts",
    ".designer.cs",
    ".freezed.dart",
    ".min.js",
    ".bundle.js",
    "_generated.go",
    "_generated.py",
    "_generated.ts",
    ".generated.ts",
    ".generated.cs",
)


def is_generated_path(file: str) -> bool:
    """True if *file*'s name matches a common codegen-tool naming convention."""
    if not file:
        return False
    lower = file.lower()
    return any(lower.endswith(suffix) for suffix in _GENERATED_SUFFIXES)
