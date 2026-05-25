"""Tests for T5.3 — `ask` system prompt + context assembly."""

from __future__ import annotations

from codegraph.ai.graphrag import (
    RetrievedEntity,
    build_user_message,
    format_entity_block,
    load_system_prompt,
)


def _entity(
    entity_id: str = "py:auth/login.py:authenticate",
    *,
    type: str = "function",
    file: str = "auth/login.py",
    start_line: int = 9,
    end_line: int = 20,
    signature: str | None = "def authenticate(email, password)",
    docstring: str | None = None,
    raw_source: str | None = "def authenticate(email, password):\n    return True\n",
    neighbors: tuple[str, ...] = (),
) -> RetrievedEntity:
    return RetrievedEntity(
        entity_id=entity_id,
        type=type,
        name=entity_id.rsplit(":", 1)[-1],
        qualified_name=entity_id.rsplit(":", 1)[-1],
        file=file,
        start_line=start_line,
        end_line=end_line,
        signature=signature,
        docstring=docstring,
        raw_source=raw_source,
        similarity=0.9,
        degree=2,
        score=0.8,
        via="vector",
        neighbors=neighbors,
    )


# ---------- system prompt ----------


def test_system_prompt_loads_and_has_grounding_rules() -> None:
    prompt = load_system_prompt()
    assert prompt  # non-empty
    assert "entity_id" in prompt
    assert "[py:src/auth/login.py:authenticate]" in prompt  # citation format example
    assert "ONLY" in prompt  # ground-in-context rule


# ---------- entity block ----------


def test_block_header_has_id_type_and_location() -> None:
    block = format_entity_block(_entity())
    assert block.splitlines()[0] == (
        "--- [py:auth/login.py:authenticate] function (auth/login.py:9-20)"
    )


def test_block_prefers_signature() -> None:
    block = format_entity_block(_entity(signature="def authenticate(email, password)"))
    assert "def authenticate(email, password)" in block
    assert "return True" not in block  # body not shown when signature present


def test_block_falls_back_to_source_preview_and_truncates() -> None:
    body = "".join(f"line{i} = {i}\n" for i in range(40))
    block = format_entity_block(_entity(signature=None, raw_source=body))
    assert "line0 = 0" in block
    assert "line19 = 19" in block
    assert "line20 = 20" not in block  # truncated at 20 lines
    assert "..." in block


def test_block_includes_docstring_and_calls() -> None:
    block = format_entity_block(
        _entity(docstring="Validate credentials.", neighbors=("py:db/models.py:User",))
    )
    assert "Validate credentials." in block
    assert "Calls: py:db/models.py:User" in block


def test_block_omits_calls_line_when_no_neighbors() -> None:
    assert "Calls:" not in format_entity_block(_entity(neighbors=()))


# ---------- user message ----------


def test_user_message_structure() -> None:
    msg = build_user_message("how does login work?", [_entity()])
    assert msg.startswith("QUESTION: how does login work?")
    assert "REPOSITORY CONTEXT:" in msg
    assert "[py:auth/login.py:authenticate]" in msg


def test_user_message_with_no_entities_admits_gap() -> None:
    msg = build_user_message("anything?", [])
    assert "REPOSITORY CONTEXT:" in msg
    assert "no relevant entities" in msg


def test_user_message_respects_char_budget() -> None:
    entities = [_entity(entity_id=f"py:m.py:fn_{i}") for i in range(50)]
    msg = build_user_message("q", entities, char_budget=200)
    # At least one block is always included; the budget caps the rest.
    assert "fn_0" in msg
    assert "fn_49" not in msg


def test_user_message_includes_all_when_under_budget() -> None:
    entities = [_entity(entity_id=f"py:m.py:fn_{i}") for i in range(3)]
    msg = build_user_message("q", entities, char_budget=100_000)
    for i in range(3):
        assert f"fn_{i}" in msg
