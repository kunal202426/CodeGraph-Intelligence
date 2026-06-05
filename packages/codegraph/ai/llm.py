# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Anthropic SDK wrapper: streaming generation with prompt caching (T5.1).

A thin, testable seam over `anthropic.Anthropic`. The graph/AI layers depend on
this interface (`stream` / `complete`) rather than the SDK directly, so the rest
of the codebase never imports `anthropic` and tests can inject a fake client.

Design notes:
- **Prompt caching**: the system prompt is sent as a cached text block
  (`cache_control: {"type": "ephemeral"}`). For a given indexed repo the system
  prompt is stable across many `ask` queries, so the cache turns the bulk of the
  per-call input cost into ~0.1x cache reads after the first request.
- **Retries**: delegated to the SDK's built-in exponential backoff (configurable
  via `max_retries`); we don't hand-roll a retry loop.
- **Errors**: SDK/API failures are re-raised as `LLMError` with a clear message
  so callers (the CLI) can show one friendly line instead of a stack trace.
- **Lazy client**: the real client is constructed on first use, so importing this
  module — and unit-testing it with an injected client — needs no API key.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid importing the SDK at module load for type-checkers only
    from anthropic import Anthropic

# Locked by the build plan (AGENTS.md / BUILD_PLAN.md §1) — do not relitigate.
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2000


class LLMError(RuntimeError):
    """Raised when the LLM call cannot be made or the API returns an error.

    Wraps missing-key / missing-dependency / API failures so callers don't need
    to know about `anthropic`'s exception hierarchy.
    """


class LLM:
    """Streaming wrapper around the Anthropic Messages API.

    Pass `client` to inject a fake in tests; otherwise a real `anthropic.Anthropic`
    is created lazily on first use (reading `ANTHROPIC_API_KEY` from the env).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        client: Anthropic | None = None,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self._client = client
        self._max_retries = max_retries

    @property
    def client(self) -> Anthropic:
        """The Anthropic client, constructed on first access."""
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - anthropic is a hard dep
                raise LLMError("The 'anthropic' package is not installed. Run `uv sync`.") from exc
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set. Export your Anthropic API key "
                    "to use AI features (ask / summarize)."
                )
            self._client = anthropic.Anthropic(max_retries=self._max_retries)
        return self._client

    def _build_kwargs(self, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        """Compose the messages.create/stream request payload.

        The system prompt is a single cached text block; the per-query user text
        comes after it so the stable prefix (system) caches independently of the
        volatile suffix (the question).
        """
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }

    def stream(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[str]:
        """Yield response text incrementally.

        Raises `LLMError` on any API/connection failure (after the SDK's retries).
        """
        kwargs = self._build_kwargs(system, user, max_tokens)
        try:
            with self.client.messages.stream(**kwargs) as stream:
                yield from stream.text_stream
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize SDK/API errors for callers
            raise LLMError(self._format_error(exc)) from exc

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """Collect the full streamed response into a single string."""
        return "".join(self.stream(system, user, max_tokens))

    @staticmethod
    def _format_error(exc: Exception) -> str:
        name = type(exc).__name__
        status = getattr(exc, "status_code", None)
        prefix = f"Anthropic API error ({name}"
        prefix += f", HTTP {status})" if status is not None else ")"
        return f"{prefix}: {exc}"
