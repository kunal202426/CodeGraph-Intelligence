"""Tests for T9.6 — dead-code detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.analysis.refactor import find_dead_code
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _index(runner: CliRunner, repo: Path, db: Path) -> None:
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0


# used() is called by caller(); orphan() is never referenced.
_REPO = {
    "a.py": (
        "def used():\n"
        "    return 1\n"
        "\n"
        "def caller():\n"
        "    return used()\n"
        "\n"
        "def orphan():\n"
        "    return 99\n"
        "\n"
        "def main():\n"
        "    return caller()\n"
        "\n"
        "def test_used():\n"
        "    return used()\n"
    ),
}


def _names(repo: Path, db: Path, runner: CliRunner, **kw) -> set[str]:
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        return {d.name for d in find_dead_code(store.conn, **kw)}
    finally:
        store.close()


def test_orphan_flagged_callers_excluded(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    names = _names(repo, tmp_path / "g.duckdb", runner)
    assert "orphan" in names  # never referenced
    assert "used" not in names  # called by caller() and test_used()


def test_entrypoints_and_tests_excluded(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    names = _names(repo, tmp_path / "g.duckdb", runner)
    # main is an entrypoint, test_used is a test, caller is called by main — none flagged.
    assert "main" not in names
    assert "test_used" not in names
    assert "caller" not in names


def test_methods_excluded_by_default(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"c.py": "class C:\n    def lonely(self):\n        return 1\n"})
    db = tmp_path / "g.duckdb"
    default = _names(repo, db, runner)
    assert "lonely" not in default  # methods excluded by default
    with_methods = _names(repo, db, runner, include_methods=True)
    assert "lonely" in with_methods


def test_dunder_excluded(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"c.py": "class C:\n    def __init__(self):\n        self.x = 1\n"})
    names = _names(repo, tmp_path / "g.duckdb", runner, include_methods=True)
    assert "__init__" not in names


def test_framework_registered_excluded(runner: CliRunner, tmp_path: Path) -> None:
    """Typer commands, HTTP routes, and pytest fixtures are invoked indirectly —
    they must not be flagged as dead even with no in-graph callers."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "app.py": (
                "import typer\n"
                "app = typer.Typer()\n"
                "\n"
                "@app.command()\n"
                "def serve():\n"
                "    return 1\n"
                "\n"
                "@app.get('/health')\n"
                "def health():\n"
                "    return 'ok'\n"
                "\n"
                "def really_dead():\n"
                "    return 2\n"
            ),
            "conftest.py": (
                "import pytest\n\n@pytest.fixture\ndef client():\n    return object()\n"
            ),
        },
    )
    names = _names(repo, tmp_path / "g.duckdb", runner)
    assert "serve" not in names  # @app.command()
    assert "health" not in names  # @app.get(...)
    assert "client" not in names  # @pytest.fixture
    assert "really_dead" in names  # genuinely unreferenced, no decorator


# ---------- CLI ----------


def test_cli_deadcode_lists_orphan(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _REPO)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["deadcode", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "orphan" in result.stdout
    assert "dead-code" in result.stdout


def test_cli_deadcode_clean(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    # main calls helper; nothing orphaned (main is an entrypoint, helper is called).
    _make_repo(repo, {"a.py": "def helper():\n    return 1\n\ndef main():\n    return helper()\n"})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["deadcode", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "No dead-code candidates" in result.stdout


def test_cli_deadcode_missing_db(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["deadcode", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout


def test_css_selectors_excluded(runner: CliRunner, tmp_path: Path) -> None:
    """Found stress-testing a real production frontend: CSS rules are parsed
    as EntityType.FUNCTION (the closest existing category), so without an
    explicit exclusion every CSS selector in a real stylesheet -- which is
    never a calls/imports edge destination, since it's referenced by a
    class/id name matched as a string in markup, not called -- was flagged
    as dead code. This was the single largest false-positive source found."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "styles.css": ".widget {\n  color: red;\n}\n.panel {\n  color: blue;\n}\n",
        },
    )
    names = _names(repo, tmp_path / "g.duckdb", runner)
    assert names == set()
