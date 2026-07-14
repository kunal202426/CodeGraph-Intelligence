# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Agent-guide writer: manages a CodeGraph block inside a project's CLAUDE.md.

The block tells the agent to prefer CodeGraph's MCP tools over reading files.
It is wrapped in BEGIN/END markers so it can be inserted, updated, or removed
idempotently without touching the rest of the user's CLAUDE.md.

Public API
----------
write_agent_guide(project_dir)   -- create/update the managed block; returns the path
remove_agent_guide(project_dir)  -- strip the block (and the file if it becomes empty)
has_agent_guide(project_dir)     -- True if the managed block is present
GUIDE_FILENAME                   -- "CLAUDE.md"
"""

from __future__ import annotations

from pathlib import Path

GUIDE_FILENAME = "CLAUDE.md"

_BEGIN = "<!-- BEGIN CODEGRAPH -->"
_END = "<!-- END CODEGRAPH -->"

# The managed block body (between the markers). Kept under ~400 tokens because the
# agent reads it every message. ASCII-only.
_BLOCK_BODY = """\
## CodeGraph -- REQUIRED workflow (code intelligence over MCP)

This repo is indexed by CodeGraph -- tools return far fewer tokens than reading files and
surface cross-file edges files can't show. Use them by default.

**Rules (every task):**
1. First message? Call `project_brief()` once. Then, before opening any source file, call
   `get_context("<concept>")` -- skip `index_status`; `get_context` reports staleness via
   `warnings`, so run `reindex` only if it appears.
2. Use `detail="full"` on the first call when you'll need real code -- understanding or
   editing. A second round-trip costs more than the larger response. Summary mode is for
   browsing candidates first.
3. Editing? Locate it, then Read + Edit yourself -- Edit needs a fresh Read either way.
4. After `get_context`, report: `CodeGraph: ~<tokens_estimated> vs ~<tokens_if_read> tokens
   (<savings_ratio>x less)` -- response size, not $ cost.

**Which tool:**
- `project_brief()` -- ONCE, first: layers, hot paths, entry points.
- `get_context(query)` -- signatures + callers/callees + staleness; `detail="full"` = source.
  2+ known names? Pass a list (max 5), not separately.
- `get_entity_context(id)` -- full source + neighbours, 1 entity.
- `impact_analysis(id)` -- what breaks.
- `trace_path(from_id, to_id)` -- shortest call chain A to B.
- `search_code(query)` -- id lookup.

**entity_id:** `{lang}:{rel_path}:{qualified_name}`, e.g. `py:auth/login.py:authenticate`.
`detail="full"` on many entities at once still costs tokens -- fine for 1-5, not a whole
search."""

# The full managed block including markers.
_MANAGED_BLOCK = f"{_BEGIN}\n{_BLOCK_BODY}\n{_END}"


def _guide_path(project_dir: Path) -> Path:
    return Path(project_dir) / GUIDE_FILENAME


def _strip_block(text: str) -> str:
    """Remove an existing managed block (markers + body) from *text*.

    Returns the text with the block and any immediately surrounding blank lines
    collapsed. If no block is present, returns *text* unchanged.
    """
    start = text.find(_BEGIN)
    if start == -1:
        return text
    end = text.find(_END, start)
    if end == -1:
        # Malformed (begin without end): drop from begin to end of file.
        return text[:start].rstrip() + "\n"
    end += len(_END)
    before = text[:start].rstrip()
    after = text[end:].lstrip()
    if before and after:
        return f"{before}\n\n{after}"
    return (before or after).rstrip() + "\n" if (before or after) else ""


def write_agent_guide(project_dir: Path) -> Path:
    """Create or update the CodeGraph managed block in ``<project_dir>/CLAUDE.md``.

    If the file does not exist, it is created containing only the block. If it
    exists with an older block, only the block is replaced. If it exists without
    a block, the block is appended. Returns the guide file path.
    """
    path = _guide_path(project_dir)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        without = _strip_block(existing).rstrip()
        content = f"{without}\n\n{_MANAGED_BLOCK}\n" if without else f"{_MANAGED_BLOCK}\n"
    else:
        content = f"{_MANAGED_BLOCK}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def remove_agent_guide(project_dir: Path) -> bool:
    """Strip the CodeGraph managed block from ``<project_dir>/CLAUDE.md``.

    Returns True if a block was removed. If the file becomes empty afterwards,
    it is deleted. Other content is preserved untouched.
    """
    path = _guide_path(project_dir)
    if not path.exists():
        return False
    existing = path.read_text(encoding="utf-8")
    if _BEGIN not in existing:
        return False
    remaining = _strip_block(existing).strip()
    if remaining:
        path.write_text(remaining + "\n", encoding="utf-8")
    else:
        path.unlink()
    return True


def has_agent_guide(project_dir: Path) -> bool:
    """Return True if the managed block is present in the project's CLAUDE.md."""
    path = _guide_path(project_dir)
    if not path.exists():
        return False
    return _BEGIN in path.read_text(encoding="utf-8")
