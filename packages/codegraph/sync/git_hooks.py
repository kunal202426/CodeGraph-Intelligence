# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Git hook installer -- fallback for `codegraph watch`.

`codegraph watch` relies on OS filesystem-change events. On some setups that's
unreliable (mounted network drives, some WSL2 `/mnt/*` paths), and the index
silently goes stale until someone remembers to run `codegraph index` by hand.
This installs an opt-in, idempotent snippet into `post-commit`, `post-merge`
(covers `git pull`), and `post-checkout` that re-indexes in the background
after the operations that actually change files on disk.

Mirrors `installer/guide.py`'s BEGIN/END marker pattern: re-running install is
a no-op, uninstall only removes what this wrote, and any other content a hook
file already has (from the user or another tool) is left untouched. The
snippet is a POSIX shell fragment -- git invokes hooks through the `sh` it
ships with on every platform it supports, Windows included, so one snippet
works everywhere without a separate `.cmd`/`.ps1` variant.

Public API
----------
install_git_hooks(repo_dir)   -- write/update the snippet in all three hooks
uninstall_git_hooks(repo_dir) -- strip the snippet from all three hooks
has_git_hooks(repo_dir)       -- True if the snippet is present in any of them
HOOK_NAMES                    -- ("post-commit", "post-merge", "post-checkout")
"""

from __future__ import annotations

import stat
from pathlib import Path

HOOK_NAMES = ("post-commit", "post-merge", "post-checkout")

_BEGIN = "# >>> codegraph sync hook >>>"
_END = "# <<< codegraph sync hook <<<"

# Runs in the background so the hook never blocks git; no-ops cleanly if
# codegraph isn't on PATH (e.g. a clone that hasn't run `pip install` yet).
_SNIPPET_BODY = """\
if command -v codegraph >/dev/null 2>&1; then
  (codegraph index . --no-embed >/dev/null 2>&1 &)
fi"""

_SNIPPET = f"{_BEGIN}\n{_SNIPPET_BODY}\n{_END}"


def _hooks_dir(repo_dir: Path) -> Path | None:
    """Return `<repo_dir>/.git/hooks`, or None if this isn't a plain git repo.

    A git worktree has `.git` as a *file* (pointing at the real gitdir) and
    shares hooks with the main checkout rather than having its own -- not
    handled here; installing per-worktree hooks would either duplicate the
    snippet or write into the wrong place.
    """
    git_path = Path(repo_dir) / ".git"
    if not git_path.is_dir():
        return None
    return git_path / "hooks"


def _strip_snippet(text: str) -> str:
    """Remove the managed snippet from *text*, collapsing surrounding blank lines."""
    start = text.find(_BEGIN)
    if start == -1:
        return text
    end = text.find(_END, start)
    if end == -1:
        return text[:start].rstrip() + "\n"
    end += len(_END)
    before = text[:start].rstrip()
    after = text[end:].lstrip()
    if before and after:
        return f"{before}\n\n{after}\n"
    return (before or after).rstrip() + "\n" if (before or after) else ""


def install_git_hooks(repo_dir: Path) -> list[Path]:
    """Write/update the sync snippet into post-commit, post-merge, post-checkout.

    Creates a hook file (with a `#!/bin/sh` shebang) if it doesn't exist yet;
    if it does, the snippet is appended without disturbing existing content.
    Returns the hook files written. Returns `[]` if `repo_dir` isn't a plain
    git repo (see `_hooks_dir`).
    """
    hooks_dir = _hooks_dir(repo_dir)
    if hooks_dir is None:
        return []
    hooks_dir.mkdir(parents=True, exist_ok=True)

    touched: list[Path] = []
    for name in HOOK_NAMES:
        path = hooks_dir / name
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            without = _strip_snippet(existing).rstrip()
            content = f"{without}\n\n{_SNIPPET}\n" if without else f"#!/bin/sh\n{_SNIPPET}\n"
        else:
            content = f"#!/bin/sh\n{_SNIPPET}\n"
        path.write_text(content, encoding="utf-8")
        _make_executable(path)
        touched.append(path)
    return touched


def uninstall_git_hooks(repo_dir: Path) -> list[Path]:
    """Strip the sync snippet from each hook file.

    A hook file that becomes just a bare shebang afterwards is deleted
    rather than left as dead weight. Returns the hook files that were
    modified or removed; a hook without the snippet, or that doesn't exist,
    is left alone.
    """
    hooks_dir = _hooks_dir(repo_dir)
    if hooks_dir is None:
        return []

    touched: list[Path] = []
    for name in HOOK_NAMES:
        path = hooks_dir / name
        if not path.exists():
            continue
        existing = path.read_text(encoding="utf-8")
        if _BEGIN not in existing:
            continue
        remaining = _strip_snippet(existing).strip()
        if remaining in ("", "#!/bin/sh"):
            path.unlink()
        else:
            path.write_text(remaining + "\n", encoding="utf-8")
        touched.append(path)
    return touched


def has_git_hooks(repo_dir: Path) -> bool:
    """True if the managed snippet is present in at least one hook file."""
    hooks_dir = _hooks_dir(repo_dir)
    if hooks_dir is None:
        return False
    for name in HOOK_NAMES:
        path = hooks_dir / name
        if path.exists() and _BEGIN in path.read_text(encoding="utf-8"):
            return True
    return False


def _make_executable(path: Path) -> None:
    """Best-effort chmod +x. Irrelevant on Windows; required on POSIX or git
    silently skips a hook it can't execute."""
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
