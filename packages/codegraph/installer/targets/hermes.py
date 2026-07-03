# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Install target: Hermes Agent (Nous Research).

Global config only: ``$HERMES_HOME/config.yaml`` (default ``~/.hermes/config.yaml``).
YAML, not JSON -- every other helper in this package assumes JSON, so this
overrides the base class's read-modify-write with a small top-level-key
text editor instead of a full YAML parser. That keeps a user's comments and
formatting everywhere else in the file untouched; round-tripping the whole
file through PyYAML would not preserve those, and this project doesn't
otherwise depend on PyYAML.

Entry shape:

    mcp_servers:
      codegraph:
        command: <python>
        args:
          - -m
          - codegraph.server.mcp_server

Simplification: this does NOT also add ``codegraph`` to a
``platform_toolsets.cli`` list the way some Hermes CLI profiles require --
if the MCP server connects but its tools don't show up in a CLI session,
check that list by hand. Getting that additional edit right needs
indentation-aware block editing beyond the single-key case here; simpler
to document than to build for one target.

Docs: https://hermes-agent.nousresearch.com
"""

from __future__ import annotations

import os
from pathlib import Path

from codegraph.installer.base import _SERVER_KEY, McpEntry, Target

_MCP_SERVERS_KEY = "mcp_servers"


def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def _entry_lines(entry: McpEntry) -> list[str]:
    lines = [f"  {_SERVER_KEY}:", f"    command: {entry.command}"]
    if entry.args:
        lines.append("    args:")
        lines += [f"      - {a}" for a in entry.args]
    return lines


def _find_top_level_block(lines: list[str], key: str) -> tuple[int, int] | None:
    """[start, end) of the top-level ``key:`` block (the key line plus every
    indented line under it), or None if the key isn't present."""
    start = next((i for i, line in enumerate(lines) if line.rstrip() == f"{key}:"), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "":
            continue
        if not lines[i].startswith(" "):
            end = i
            break
    return (start, end)


def _find_child_block(
    lines: list[str], parent: tuple[int, int], key: str
) -> tuple[int, int] | None:
    """[start, end) of a 2-space-indented ``  key:`` child block within the
    parent's line range, or None if absent."""
    p_start, p_end = parent
    start = next((i for i in range(p_start + 1, p_end) if lines[i].rstrip() == f"  {key}:"), None)
    if start is None:
        return None
    end = p_end
    for i in range(start + 1, p_end):
        if lines[i].strip() == "":
            continue
        if not lines[i].startswith("    "):
            end = i
            break
    return (start, end)


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


class HermesTarget(Target):
    name = "hermes"
    display_name = "Hermes Agent"

    def global_config_path(self) -> Path:
        return _hermes_home() / "config.yaml"

    def is_available(self) -> bool:
        """True if ``$HERMES_HOME``/``~/.hermes`` or its config file exists."""
        return _hermes_home().is_dir() or self.global_config_path().exists()

    def config_snippet(self, db: Path | None) -> str:
        entry = self.build_entry(db)
        body = "\n".join([f"{_MCP_SERVERS_KEY}:", *_entry_lines(entry)])
        return f"# Add to {self.global_config_path()}\n\n{body}\n"

    def is_configured(self, *, global_: bool = True) -> bool:
        lines = _read_lines(self.global_config_path())
        parent = _find_top_level_block(lines, _MCP_SERVERS_KEY)
        if parent is None:
            return False
        return _find_child_block(lines, parent, _SERVER_KEY) is not None

    def _write_entry(self, path: Path, db: Path | None) -> None:
        lines = _read_lines(path)
        new_child = _entry_lines(self.build_entry(db))

        parent = _find_top_level_block(lines, _MCP_SERVERS_KEY)
        if parent is None:
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.append(f"{_MCP_SERVERS_KEY}:")
            lines.extend(new_child)
            _write_lines(path, lines)
            return

        p_start, _p_end = parent
        child = _find_child_block(lines, parent, _SERVER_KEY)
        if child is not None:
            c_start, c_end = child
            lines[c_start:c_end] = new_child
        else:
            lines[p_start + 1 : p_start + 1] = new_child
        _write_lines(path, lines)

    def _remove_entry(self, path: Path) -> None:
        lines = _read_lines(path)
        if not lines:
            return
        parent = _find_top_level_block(lines, _MCP_SERVERS_KEY)
        if parent is None:
            return
        child = _find_child_block(lines, parent, _SERVER_KEY)
        if child is None:
            return
        c_start, c_end = child
        del lines[c_start:c_end]

        # Drop the now-empty mcp_servers: wrapper too.
        parent = _find_top_level_block(lines, _MCP_SERVERS_KEY)
        if parent is not None:
            p_start, p_end = parent
            if p_end == p_start + 1:
                del lines[p_start:p_end]
        _write_lines(path, lines)
