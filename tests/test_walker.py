"""Tests for the file walker."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.uir import Language
from codegraph.walker import (
    ALWAYS_EXCLUDE,
    LANGUAGE_BY_EXT,
    detect_language,
    is_binary,
    walk,
)


def _make_repo(root: Path, layout: dict[str, str]) -> None:
    """Create a fake repo at `root` from a {relpath: content} dict.

    Use forward slashes in relpath. An empty content means create the file as empty.
    """
    for rel, content in layout.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _relpaths(result) -> set[str]:
    return {p.name for p, _ in result}


# ---------- detect_language ----------


def test_detect_language_known_extensions() -> None:
    assert detect_language(Path("a.py")) == Language.PYTHON
    assert detect_language(Path("a.pyi")) == Language.PYTHON
    assert detect_language(Path("a.ts")) == Language.TYPESCRIPT
    assert detect_language(Path("a.tsx")) == Language.TYPESCRIPT
    assert detect_language(Path("a.js")) == Language.JAVASCRIPT
    assert detect_language(Path("a.jsx")) == Language.JAVASCRIPT
    assert detect_language(Path("a.mjs")) == Language.JAVASCRIPT


def test_detect_language_case_insensitive() -> None:
    assert detect_language(Path("UPPER.PY")) == Language.PYTHON
    assert detect_language(Path("Mixed.Ts")) == Language.TYPESCRIPT


def test_detect_language_unknown_returns_none() -> None:
    assert detect_language(Path("README.md")) is None
    assert detect_language(Path("config.yaml")) is None
    assert detect_language(Path("style.css")) is None
    assert detect_language(Path("no_extension")) is None


# ---------- is_binary ----------


def test_is_binary_detects_nul_byte(tmp_path: Path) -> None:
    binary = tmp_path / "blob.py"
    binary.write_bytes(b"def f():\x00 pass\n")
    assert is_binary(binary) is True


def test_is_binary_passes_normal_text(tmp_path: Path) -> None:
    text = tmp_path / "ok.py"
    text.write_text("def f(): pass\n")
    assert is_binary(text) is False


def test_is_binary_handles_missing_file(tmp_path: Path) -> None:
    assert is_binary(tmp_path / "nonexistent.py") is True


# ---------- walk basics ----------


def test_walk_finds_python_files(tmp_path: Path) -> None:
    _make_repo(
        tmp_path,
        {
            "main.py": "x = 1\n",
            "lib/util.py": "y = 2\n",
            "lib/deep/nested.py": "z = 3\n",
        },
    )
    out = list(walk(tmp_path))
    assert len(out) == 3
    for _, lang in out:
        assert lang == Language.PYTHON
    assert _relpaths(out) == {"main.py", "util.py", "nested.py"}


def test_walk_yields_typescript(tmp_path: Path) -> None:
    _make_repo(
        tmp_path,
        {
            "src/index.ts": "export const x = 1;\n",
            "src/App.tsx": "export default function App() { return null; }\n",
            "src/legacy.js": "module.exports = {};\n",
        },
    )
    out = list(walk(tmp_path))
    langs = {p.name: lang for p, lang in out}
    assert langs["index.ts"] == Language.TYPESCRIPT
    assert langs["App.tsx"] == Language.TYPESCRIPT
    assert langs["legacy.js"] == Language.JAVASCRIPT


def test_walk_ignores_unknown_extensions(tmp_path: Path) -> None:
    _make_repo(
        tmp_path,
        {
            "main.py": "x = 1\n",
            "README.md": "# hi\n",
            "config.yaml": "k: v\n",
            "binary_blob.bin": "irrelevant\n",
        },
    )
    out = list(walk(tmp_path))
    assert _relpaths(out) == {"main.py"}


def test_walk_empty_dir_yields_nothing(tmp_path: Path) -> None:
    assert list(walk(tmp_path)) == []


def test_walk_nonexistent_root_yields_nothing(tmp_path: Path) -> None:
    assert list(walk(tmp_path / "does_not_exist")) == []


def test_walk_root_is_a_file_yields_nothing(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x=1\n")
    assert list(walk(f)) == []


# ---------- always-exclude dirs ----------


def test_walk_skips_always_excluded_dirs(tmp_path: Path) -> None:
    _make_repo(
        tmp_path,
        {
            "keep.py": "x = 1\n",
            ".git/objects/something.py": "x = 1\n",
            "node_modules/pkg/index.js": "x;\n",
            "__pycache__/cache.py": "x = 1\n",
            ".venv/lib/site.py": "x = 1\n",
            "dist/bundle.js": "x;\n",
            "build/out.py": "x = 1\n",
        },
    )
    out = list(walk(tmp_path))
    assert _relpaths(out) == {"keep.py"}


def test_always_exclude_constants_present() -> None:
    # Smoke-check the set of always-excluded names. If we change one, this test
    # is the canary that the change was intentional.
    for required in {".git", ".codegraph", "node_modules", "__pycache__", ".venv", "venv"}:
        assert required in ALWAYS_EXCLUDE


# ---------- gitignore ----------


def test_walk_respects_gitignore_files(tmp_path: Path) -> None:
    _make_repo(
        tmp_path,
        {
            ".gitignore": "ignored.py\nbuild_out/\n*.gen.ts\n",
            "main.py": "x = 1\n",
            "ignored.py": "x = 1\n",
            "build_out/inner.py": "x = 1\n",
            "src/file.ts": "x;\n",
            "src/auto.gen.ts": "x;\n",
        },
    )
    out = list(walk(tmp_path))
    assert _relpaths(out) == {"main.py", "file.ts"}


def test_walk_no_gitignore_is_fine(tmp_path: Path) -> None:
    _make_repo(tmp_path, {"a.py": "x = 1\n"})
    out = list(walk(tmp_path))
    assert _relpaths(out) == {"a.py"}


def test_walk_gitignore_with_blank_lines_and_comments(tmp_path: Path) -> None:
    _make_repo(
        tmp_path,
        {
            ".gitignore": "# a comment\n\nignored.py\n  \n",
            "main.py": "x = 1\n",
            "ignored.py": "x = 1\n",
        },
    )
    out = list(walk(tmp_path))
    assert _relpaths(out) == {"main.py"}


# ---------- binary skip ----------


def test_walk_skips_binary_python_files(tmp_path: Path) -> None:
    _make_repo(tmp_path, {"good.py": "x = 1\n"})
    binary = tmp_path / "bad.py"
    binary.write_bytes(b"\x00\x01\x02")
    out = list(walk(tmp_path))
    assert _relpaths(out) == {"good.py"}


# ---------- against the real fixture ----------


def test_walk_on_sample_repo_fixture() -> None:
    out = list(walk(Path("tests/fixtures/sample_repo_py")))
    # fixture has tests/fixtures/sample_repo_py/auth/login.py
    paths = [p for p, _ in out]
    assert any(p.name == "login.py" for p in paths)
    assert all(lang == Language.PYTHON for _, lang in out)


# ---------- LANGUAGE_BY_EXT coverage ----------


@pytest.mark.parametrize("ext, expected", list(LANGUAGE_BY_EXT.items()))
def test_language_table_coverage(ext: str, expected: Language) -> None:
    assert detect_language(Path(f"x{ext}")) == expected
