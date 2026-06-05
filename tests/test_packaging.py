"""Tests for T18.3 — PyPI packaging metadata is present and well-formed."""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _project() -> dict:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]


def test_core_identity_present() -> None:
    p = _project()
    assert p["name"] == "codegraph"
    assert p["version"]
    assert p["description"]
    assert p["readme"] == "README.md"


def test_console_script_entry_point() -> None:
    p = _project()
    assert p["scripts"]["codegraph"] == "codegraph.cli:app"


def test_urls_point_at_repo() -> None:
    urls = _project()["urls"]
    assert "github.com" in urls["Repository"]
    assert "Homepage" in urls and "Issues" in urls


def test_classifiers_declare_license_and_python() -> None:
    classifiers = _project()["classifiers"]
    # Source-available under PolyForm Noncommercial 1.0.0; no SPDX trove classifier
    # exists for it, so PyPI's "Other/Proprietary License" is the canonical fallback.
    assert any("Other/Proprietary License" in c for c in classifiers)
    assert any("Python :: 3.11" in c for c in classifiers)


def test_license_text_is_polyform_noncommercial() -> None:
    p = _project()
    assert p["license"]["text"] == "PolyForm-Noncommercial-1.0.0"
    license_file = _PYPROJECT.parent / "LICENSE"
    text = license_file.read_text(encoding="utf-8")
    assert "PolyForm Noncommercial" in text
    assert "Kunal Mathur" in text


def test_keywords_present() -> None:
    kw = _project()["keywords"]
    assert "mcp" in kw
    assert len(kw) >= 5


def test_license_file_exists() -> None:
    assert (_PYPROJECT.parent / "LICENSE").exists()
