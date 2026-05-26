"""Per-entity code ownership via `git blame` (T9.1).

Given an entity's file + line span, run `git blame --line-porcelain` over just
those lines and tally authors. `--line-porcelain` repeats the full header for
every line, so each source line contributes exactly one `author ` record — an
accurate per-line tally (plain `--porcelain` only prints the header once per
commit group and would undercount).

Pure subprocess + parsing; no DuckDB. Returns [] when the file isn't tracked by
git or the repo isn't a git working tree, so callers can degrade gracefully.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Ownership:
    """How many lines of an entity a given author last touched."""

    author: str
    lines: int


def entity_ownership(
    repo_root: Path | str,
    file: str,
    start_line: int,
    end_line: int,
) -> list[Ownership]:
    """Authors of lines [start_line, end_line] of `file`, most lines first.

    `file` is repo-relative (forward slashes, as stored in the graph). Returns an
    empty list if git blame fails (untracked file / not a git repo / bad range).
    """
    if start_line < 1 or end_line < start_line:
        return []
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "blame",
                "--line-porcelain",
                "-L",
                f"{start_line},{end_line}",
                "--",
                file,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []

    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        # "author NAME" (with a space) — not "author-mail"/"author-time"/"author-tz".
        if line.startswith("author "):
            name = line[len("author ") :]
            counts[name] = counts.get(name, 0) + 1

    return sorted(
        (Ownership(author=a, lines=n) for a, n in counts.items()),
        key=lambda o: (-o.lines, o.author),
    )


def primary_owner(ownerships: list[Ownership]) -> str | None:
    """The author with the most lines (the first, since the list is sorted), or None."""
    return ownerships[0].author if ownerships else None
