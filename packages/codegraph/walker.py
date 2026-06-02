"""File walker — discover indexable source files under a repo root.

`walk(root)` yields `(Path, Language)` tuples for each file that:
- has a known source-language extension (see `LANGUAGE_BY_EXT`)
- does NOT live under one of `ALWAYS_EXCLUDE` directories
- is NOT matched by `<root>/.gitignore` (gitwildmatch patterns)
- does NOT look binary (NUL byte in the first 8 KiB)

Always-exclude dirs are pruned during traversal (no descent), so very large
ignored trees like `.venv/` and `node_modules/` cost nothing. Symlinks are
not followed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pathspec

from codegraph.uir import Language

LANGUAGE_BY_EXT: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".java": Language.JAVA,
    ".rb": Language.RUBY,
    ".php": Language.PHP,
    ".c": Language.C,
    ".h": Language.C,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".hxx": Language.CPP,
}

ALWAYS_EXCLUDE: frozenset[str] = frozenset(
    {
        ".git",
        ".codegraph",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "wheels",
        "pip-wheel-metadata",
        ".next",
        ".nuxt",
        ".turbo",
        ".tox",
        ".idea",
        ".vscode",
    }
)

BINARY_SNIFF_BYTES = 8192


def detect_language(path: Path) -> Language | None:
    """Return the Language for `path` based on its extension, or None."""
    return LANGUAGE_BY_EXT.get(path.suffix.lower())


def is_binary(path: Path) -> bool:
    """Heuristic binary check: NUL byte in the first ~8 KiB, or unreadable file."""
    try:
        with path.open("rb") as f:
            chunk = f.read(BINARY_SNIFF_BYTES)
    except OSError:
        return True
    return b"\x00" in chunk


def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
    gi = root / ".gitignore"
    if not gi.is_file():
        return None
    try:
        text = gi.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return pathspec.PathSpec.from_lines("gitignore", text.splitlines())


def walk(root: Path) -> Iterator[tuple[Path, Language]]:
    """Yield (path, language) for each indexable file under `root`."""
    root = Path(root).resolve()
    if not root.is_dir():
        return
    spec = _load_gitignore(root)

    for dirpath_str, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune always-excluded directories in-place so os.walk doesn't descend.
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDE]

        # Also honour .gitignore-matched directories. gitignore treats a
        # pattern like `build/` as matching the dir; pathspec needs a
        # trailing slash to test that.
        if spec is not None:
            pruned: list[str] = []
            dirpath = Path(dirpath_str)
            for d in dirnames:
                rel_dir = (dirpath / d).relative_to(root).as_posix() + "/"
                if not spec.match_file(rel_dir):
                    pruned.append(d)
            dirnames[:] = pruned

        for fn in filenames:
            path = Path(dirpath_str) / fn
            lang = detect_language(path)
            if lang is None:
                continue

            if spec is not None:
                rel_file = path.relative_to(root).as_posix()
                if spec.match_file(rel_file):
                    continue

            if is_binary(path):
                continue

            yield path, lang
