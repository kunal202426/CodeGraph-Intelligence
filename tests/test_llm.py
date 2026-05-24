"""Tests for T5.1 — Anthropic SDK wrapper with prompt caching.

No live API calls: a fake client mirroring the SDK's `messages.stream(...)`
context-manager shape is injected. Live calls are exercised later (T5.4).
"""

from __future__ import annotations

import pytest
from codegraph.ai.llm import DEFAULT_MODEL, LLM, LLMError


class _FakeStream:
    """Mimics the object returned by `client.messages.stream(...)`."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    @property
    def text_stream(self):
        yield from self._tokens


class _FakeMessages:
    def __init__(self, tokens: list[str], capture: dict, error: Exception | None) -> None:
        self._tokens = tokens
        self._capture = capture
        self._error = error

    def stream(self, **kwargs):
        self._capture.clear()
        self._capture.update(kwargs)
        if self._error is not None:
            raise self._error
        return _FakeStream(self._tokens)


class _FakeClient:
    """Stand-in for `anthropic.Anthropic` with just the `.messages.stream` seam."""

    def __init__(
        self,
        tokens: list[str] | None = None,
        capture: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.messages = _FakeMessages(tokens or [], capture if capture is not None else {}, error)


# ---------- streaming + completion ----------


def test_stream_yields_tokens_in_order() -> None:
    llm = LLM(client=_FakeClient(tokens=["Hel", "lo ", "world"]))
    assert list(llm.stream("sys", "hi")) == ["Hel", "lo ", "world"]


def test_complete_joins_tokens() -> None:
    llm = LLM(client=_FakeClient(tokens=["a", "b", "c"]))
    assert llm.complete("sys", "hi") == "abc"


# ---------- request composition ----------


def test_system_prompt_is_a_cached_block() -> None:
    capture: dict = {}
    llm = LLM(client=_FakeClient(tokens=["x"], capture=capture))
    llm.complete("YOU ARE A CODE ANALYST", "how does login work?")
    system = capture["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["type"] == "text"
    assert system[0]["text"] == "YOU ARE A CODE ANALYST"
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_user_message_is_composed() -> None:
    capture: dict = {}
    llm = LLM(client=_FakeClient(tokens=["x"], capture=capture))
    llm.complete("sys", "how does login work?")
    assert capture["messages"] == [{"role": "user", "content": "how does login work?"}]


def test_model_and_max_tokens_passed_through() -> None:
    capture: dict = {}
    llm = LLM(model="claude-haiku-4-5", client=_FakeClient(tokens=["x"], capture=capture))
    llm.complete("sys", "q", max_tokens=512)
    assert capture["model"] == "claude-haiku-4-5"
    assert capture["max_tokens"] == 512


def test_default_model_is_locked_sonnet() -> None:
    assert DEFAULT_MODEL == "claude-sonnet-4-6"
    assert LLM().model == "claude-sonnet-4-6"


# ---------- error handling ----------


def test_api_error_is_wrapped_in_llmerror() -> None:
    class Boom(Exception):
        status_code = 503

    llm = LLM(client=_FakeClient(error=Boom("overloaded")))
    with pytest.raises(LLMError) as ei:
        list(llm.stream("sys", "q"))
    msg = str(ei.value)
    assert "Boom" in msg
    assert "503" in msg
    assert "overloaded" in msg


def test_missing_api_key_raises_llmerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = LLM()  # no injected client → must construct a real one → needs key
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        list(llm.stream("sys", "q"))


def test_injected_client_needs_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # An injected client must work without any key present.
    llm = LLM(client=_FakeClient(tokens=["ok"]))
    assert llm.complete("sys", "q") == "ok"
