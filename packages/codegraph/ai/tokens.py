# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Lightweight token estimation.

We deliberately avoid a hard dependency on a tokenizer (tiktoken / the Anthropic
counter) for budgeting: those add weight and network/version coupling, and for
*budgeting* a stable approximation is enough. The ~4-characters-per-token
heuristic is the well-known rule of thumb for English + code and is monotonic in
text length, which is all the budget logic needs.

Public API
----------
estimate_tokens(text) -> int
    Approximate token count for a string (>= 1 for any non-empty text).
"""

from __future__ import annotations

# Average characters per token for English prose and source code. Conservative
# (slightly under-counts dense code) but stable and dependency-free.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate the number of tokens in *text*.

    Uses the ~4-chars-per-token heuristic. Returns 0 for empty input and at
    least 1 for any non-empty string so callers can rely on monotonicity.
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)
