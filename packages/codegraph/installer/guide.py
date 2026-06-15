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

This repo is indexed by CodeGraph. Its tools return ~10x fewer tokens than reading files
and capture cross-file call/import edges single files miss. Use them by default.

**Rules (every task):**
1. Call `index_status` once. If `stale: true`, call `reindex`.
2. Do NOT open a source file to understand code until you have called
   `get_context("<concept>")`. Read a raw file only for the exact full body of an entity
   `get_context` surfaced.
3. After a `get_context` call, report the savings: `CodeGraph: ~<tokens_estimated> vs
   ~<tokens_if_read> tokens (<savings_ratio>x less)`.

**Which tool:**
- `get_context(query)` -- START HERE. Search + signatures + callers/callees, plus
  `tokens_estimated` / `tokens_if_read` / `savings_ratio`. `detail="full"` = complete
  source (1-2 entities max).
- `get_entity_context(entity_id)` -- full source + neighbours for ONE entity.
- `impact_analysis(entity_id)` -- what breaks if I change this (reverse callers).
- `trace_path(from_id, to_id)` -- shortest call chain A to B.
- `search_code(query)` -- fast id-only lookup.

**entity_id format:** `{lang}:{rel_path}:{qualified_name}`, e.g.
`py:auth/login.py:authenticate`. Forward slashes always.

**Token discipline:** keep `get_context` in summary mode; don't request `detail="full"`
for many entities at once."""

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
