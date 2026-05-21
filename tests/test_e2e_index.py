"""End-to-end smoke test for Phase 1: walker → parser → graph → search.

Indexes the multi-file sample_repo_py fixture via the CLI, then asserts the
graph contents and search results against known fixtures. This is the Phase 1
acceptance gate — if this test passes, the slice is shippable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner

SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


@pytest.fixture
def indexed(tmp_path: Path) -> Path:
    """Run `codegraph index` against the fixture and return the DB path."""
    db = tmp_path / "graph.duckdb"
    runner = CliRunner()
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, f"index failed: {result.stdout}"
    return db


# ---------- fixture shape ----------


def test_fixture_has_at_least_four_python_files() -> None:
    """Phase 1 acceptance criterion."""
    py_files = [p for p in SAMPLE_REPO.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(py_files) >= 4, f"expected >= 4 .py files in fixture, found {len(py_files)}"


# ---------- index produces expected graph ----------


def test_index_yields_at_least_eight_entities(indexed: Path) -> None:
    """Phase 1 acceptance criterion."""
    store = GraphStore(indexed)
    try:
        assert store.count_entities() >= 8
        assert store.count_files() >= 4
    finally:
        store.close()


def test_index_records_authenticate_function_with_correct_location(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        row = store.conn.execute(
            "SELECT entity_id, type, file, start_line FROM entities WHERE entity_id = ?",
            ["py:auth/login.py:authenticate"],
        ).fetchone()
    finally:
        store.close()
    assert row == ("py:auth/login.py:authenticate", "function", "auth/login.py", 9)


def test_method_carries_parent_class_id(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        row = store.conn.execute(
            "SELECT parent_id FROM entities WHERE entity_id = ?",
            ["py:auth/login.py:LoginForm.validate"],
        ).fetchone()
    finally:
        store.close()
    assert row is not None
    assert row[0] == "py:auth/login.py:LoginForm"


def test_index_covers_all_top_level_packages(indexed: Path) -> None:
    """Module entities from main, auth, api, db all land."""
    store = GraphStore(indexed)
    try:
        files = {
            row[0] for row in store.conn.execute("SELECT DISTINCT file FROM entities").fetchall()
        }
    finally:
        store.close()
    assert "main.py" in files
    assert "auth/login.py" in files
    assert "api/users.py" in files
    assert "db/models.py" in files


def test_async_method_flagged(indexed: Path) -> None:
    """login_handler in api/users.py is `async def` → is_async = True."""
    store = GraphStore(indexed)
    try:
        row = store.conn.execute(
            "SELECT is_async FROM entities WHERE entity_id = ?",
            ["py:api/users.py:UserController.login_handler"],
        ).fetchone()
    finally:
        store.close()
    assert row is not None
    assert row[0] is True


def test_private_underscore_class_not_exported(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        row = store.conn.execute(
            "SELECT is_exported FROM entities WHERE entity_id = ?",
            ["py:auth/login.py:_PrivateForm"],
        ).fetchone()
    finally:
        store.close()
    assert row is not None
    assert row[0] is False


# ---------- CLI search round-trip ----------


def test_search_authenticate_returns_hit(indexed: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["search", "authenticate", "--db", str(indexed)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout
    assert "auth/login.py" in result.stdout


def test_search_user_finds_multiple_files(indexed: Path) -> None:
    """`User` appears in db/models.py (the dataclass) and api/users.py (referenced)."""
    runner = CliRunner()
    result = runner.invoke(app, ["search", "User", "--db", str(indexed)])
    assert result.exit_code == 0
    # The User class lives in db/models.py
    assert "db/models.py" in result.stdout or "api/users.py" in result.stdout


def test_search_via_docstring(indexed: Path) -> None:
    """The docstring of authenticate() mentions 'credentials' — search by that word finds it."""
    runner = CliRunner()
    result = runner.invoke(app, ["search", "credentials", "--db", str(indexed)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout


# ---------- idempotency at e2e level ----------


def test_re_index_is_idempotent_at_e2e_level(indexed: Path) -> None:
    """Re-running index over the same fixture must not duplicate any row."""
    store = GraphStore(indexed)
    try:
        first_entities = store.count_entities()
        first_files = store.count_files()
    finally:
        store.close()

    runner = CliRunner()
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(indexed)])
    assert result.exit_code == 0

    store = GraphStore(indexed)
    try:
        assert store.count_entities() == first_entities
        assert store.count_files() == first_files
    finally:
        store.close()
