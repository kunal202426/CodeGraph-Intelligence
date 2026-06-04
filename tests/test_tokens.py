"""Tests for T15.2 — token estimator."""

from __future__ import annotations

from codegraph.ai.tokens import estimate_tokens


def test_empty_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_nonempty_is_at_least_one() -> None:
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("hi") >= 1


def test_monotonic_in_length() -> None:
    short = estimate_tokens("def f(): pass")
    long = estimate_tokens("def f(): pass\n" * 100)
    assert long > short


def test_roughly_quarter_of_chars() -> None:
    text = "x" * 400
    # ~4 chars/token → ~100 tokens. Allow slack for the heuristic.
    assert 80 <= estimate_tokens(text) <= 120
