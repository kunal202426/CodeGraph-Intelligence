"""Tests for T14.2 — CLAUDE.md managed-block agent guide writer."""

from __future__ import annotations

from pathlib import Path

from codegraph.installer.guide import (
    GUIDE_FILENAME,
    has_agent_guide,
    remove_agent_guide,
    write_agent_guide,
)

_BEGIN = "<!-- BEGIN CODEGRAPH -->"
_END = "<!-- END CODEGRAPH -->"


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def test_write_creates_file_when_absent(tmp_path: Path) -> None:
    path = write_agent_guide(tmp_path)
    assert path == tmp_path / GUIDE_FILENAME
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert _BEGIN in text and _END in text
    assert "get_context" in text


def test_write_block_is_under_400_tokens(tmp_path: Path) -> None:
    path = write_agent_guide(tmp_path)
    text = path.read_text(encoding="utf-8")
    # ~4 chars/token heuristic; keep the block well under 400 tokens.
    assert len(text) / 4 < 400


def test_guide_is_strong_mandate_with_savings_instruction(tmp_path: Path) -> None:
    """The guide must (a) require CodeGraph before reading files and (b) tell the
    agent to report the token savings — the two levers behind 'auto-use'."""
    text = write_agent_guide(tmp_path).read_text(encoding="utf-8")
    assert "REQUIRED" in text
    assert "Do NOT open a source file" in text
    # Savings-reporting instruction references the get_context response fields.
    assert "savings_ratio" in text
    assert "tokens_if_read" in text


def test_write_is_idempotent(tmp_path: Path) -> None:
    write_agent_guide(tmp_path)
    first = (tmp_path / GUIDE_FILENAME).read_text(encoding="utf-8")
    write_agent_guide(tmp_path)
    second = (tmp_path / GUIDE_FILENAME).read_text(encoding="utf-8")
    assert first == second
    # Exactly one block.
    assert second.count(_BEGIN) == 1
    assert second.count(_END) == 1


def test_write_preserves_existing_content(tmp_path: Path) -> None:
    path = tmp_path / GUIDE_FILENAME
    path.write_text("# My Project\n\nSome existing instructions.\n", encoding="utf-8")
    write_agent_guide(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "# My Project" in text
    assert "Some existing instructions." in text
    assert _BEGIN in text


def test_write_replaces_old_block_not_duplicate(tmp_path: Path) -> None:
    path = tmp_path / GUIDE_FILENAME
    # Simulate an older block with different body.
    path.write_text(f"# Header\n\n{_BEGIN}\nOLD BODY\n{_END}\n\n## Footer\n", encoding="utf-8")
    write_agent_guide(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert text.count(_BEGIN) == 1
    assert "OLD BODY" not in text
    assert "# Header" in text
    assert "## Footer" in text


# ---------------------------------------------------------------------------
# has
# ---------------------------------------------------------------------------


def test_has_false_when_absent(tmp_path: Path) -> None:
    assert not has_agent_guide(tmp_path)


def test_has_true_after_write(tmp_path: Path) -> None:
    write_agent_guide(tmp_path)
    assert has_agent_guide(tmp_path)


def test_has_false_when_file_without_block(tmp_path: Path) -> None:
    (tmp_path / GUIDE_FILENAME).write_text("# Just a heading\n", encoding="utf-8")
    assert not has_agent_guide(tmp_path)


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_deletes_file_when_only_block(tmp_path: Path) -> None:
    write_agent_guide(tmp_path)
    assert remove_agent_guide(tmp_path) is True
    assert not (tmp_path / GUIDE_FILENAME).exists()


def test_remove_preserves_other_content(tmp_path: Path) -> None:
    path = tmp_path / GUIDE_FILENAME
    path.write_text("# My Project\n\nKeep me.\n", encoding="utf-8")
    write_agent_guide(tmp_path)
    assert remove_agent_guide(tmp_path) is True
    text = path.read_text(encoding="utf-8")
    assert "# My Project" in text
    assert "Keep me." in text
    assert _BEGIN not in text


def test_remove_noop_when_absent(tmp_path: Path) -> None:
    assert remove_agent_guide(tmp_path) is False


def test_remove_noop_when_no_block(tmp_path: Path) -> None:
    (tmp_path / GUIDE_FILENAME).write_text("# No block here\n", encoding="utf-8")
    assert remove_agent_guide(tmp_path) is False


def test_write_remove_roundtrip_restores_original(tmp_path: Path) -> None:
    path = tmp_path / GUIDE_FILENAME
    original = "# My Project\n\nSome existing instructions.\n"
    path.write_text(original, encoding="utf-8")
    write_agent_guide(tmp_path)
    remove_agent_guide(tmp_path)
    # Content preserved (trailing whitespace may normalize).
    assert "# My Project" in path.read_text(encoding="utf-8")
    assert "Some existing instructions." in path.read_text(encoding="utf-8")
    assert _BEGIN not in path.read_text(encoding="utf-8")
