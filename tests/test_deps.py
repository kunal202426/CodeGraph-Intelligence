"""Tests for T2.6 — `codegraph deps` and the find_dependencies/find_entity queries."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.queries import find_dependencies, find_entity_by_name_or_id
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


@pytest.fixture
def indexed(runner: CliRunner, tmp_path: Path) -> Path:
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    return db


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


# ---------- entity lookup ----------


def test_find_entity_by_name_returns_single_hit(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        hits = find_entity_by_name_or_id(store.conn, "authenticate")
    finally:
        store.close()
    assert len(hits) == 1
    assert hits[0].name == "authenticate"
    assert hits[0].file == "auth/login.py"


def test_find_entity_by_entity_id(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        hits = find_entity_by_name_or_id(store.conn, "py:auth/login.py:authenticate")
    finally:
        store.close()
    assert len(hits) == 1
    assert hits[0].entity_id == "py:auth/login.py:authenticate"


def test_find_entity_unknown_returns_empty(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        hits = find_entity_by_name_or_id(store.conn, "no_such_xyzzy")
    finally:
        store.close()
    assert hits == []


def test_find_entity_empty_query_returns_empty(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        assert find_entity_by_name_or_id(store.conn, "") == []
    finally:
        store.close()


def test_find_entity_by_qualified_name(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        hits = find_entity_by_name_or_id(store.conn, "LoginForm.validate")
    finally:
        store.close()
    assert len(hits) == 1
    assert hits[0].name == "validate"


# ---------- BFS ----------


def test_find_dependencies_depth_1_returns_direct_imports(indexed: Path) -> None:
    """main.py imports UserController, LoginForm, authenticate → 3 direct kids."""
    store = GraphStore(indexed)
    try:
        tree = find_dependencies(store.conn, "py:main.py:main", depth=1)
    finally:
        store.close()
    root_kids = tree.children.get("py:main.py:main", [])
    assert len(root_kids) >= 3
    names = {kid.name for kid in root_kids}
    assert "UserController" in names
    assert "LoginForm" in names
    assert "authenticate" in names


def test_find_dependencies_walks_to_depth_2(tmp_path: Path, runner: CliRunner) -> None:
    """A chain where depth 2 truly reveals more than depth 1.

    `import a → a is a module entity that imports b → b is a module entity`.
    With `from X import Y` the dst is a function entity (no outbound edges),
    so we use bare `import` to keep the chain on module entities.
    """
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "def leaf(): return 1\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    store = GraphStore(db)
    try:
        shallow = find_dependencies(store.conn, "py:a.py:a", depth=1)
        deeper = find_dependencies(store.conn, "py:a.py:a", depth=2)
    finally:
        store.close()

    # depth 1: a → b (1 hop, b is in `children` as a kid but not as a key)
    shallow_kid_count = sum(len(v) for v in shallow.children.values())
    deeper_kid_count = sum(len(v) for v in deeper.children.values())
    assert deeper_kid_count > shallow_kid_count
    # And the b → c hop must appear at depth 2.
    assert "py:b.py:b" in deeper.children


def test_find_dependencies_depth_0_is_truncated(indexed: Path) -> None:
    store = GraphStore(indexed)
    try:
        tree = find_dependencies(store.conn, "py:main.py:main", depth=0)
    finally:
        store.close()
    assert tree.children == {}
    assert tree.truncated is True


def test_find_dependencies_externals_are_leaves(indexed: Path) -> None:
    """db/models.py imports dataclasses → external, no further traversal."""
    store = GraphStore(indexed)
    try:
        tree = find_dependencies(store.conn, "py:db/models.py:db.models", depth=3)
    finally:
        store.close()
    # If externals were traversed they'd show up as keys in `children`; they
    # shouldn't, since externals have no outbound edges in our model.
    for kids in tree.children.values():
        for kid in kids:
            if kid.is_external:
                assert kid.entity_id not in tree.children


def test_find_dependencies_cycle_safe(tmp_path: Path, runner: CliRunner) -> None:
    """A → B → A: BFS visits each node once, doesn't infinite-loop."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "from b import bar\ndef foo(): return bar()\n",
            "b.py": "from a import foo\ndef bar(): return foo()\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])
    store = GraphStore(db)
    try:
        # Should complete without hanging.
        tree = find_dependencies(store.conn, "py:a.py:a", depth=5)
    finally:
        store.close()
    assert tree.root == "py:a.py:a"


# ---------- CLI ----------


def test_cli_deps_finds_authenticate(runner: CliRunner, indexed: Path) -> None:
    result = runner.invoke(app, ["deps", "authenticate", "--db", str(indexed)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout
    # The root entity line should be present; deps tree may be empty for a leaf function.


def test_cli_deps_finds_main_module_imports(runner: CliRunner, indexed: Path) -> None:
    result = runner.invoke(app, ["deps", "main", "--db", str(indexed)])
    assert result.exit_code == 0
    # main.py imports authenticate / LoginForm / UserController — at least one must appear.
    assert "authenticate" in result.stdout or "LoginForm" in result.stdout


def test_cli_deps_unknown_entity_errors(runner: CliRunner, indexed: Path) -> None:
    result = runner.invoke(app, ["deps", "no_such_thing", "--db", str(indexed)])
    assert result.exit_code == 1
    assert "No entity" in result.stdout


def test_cli_deps_missing_db_errors(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["deps", "x", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout


def test_cli_deps_depth_flag_is_wired(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A real chain: depth 1 truncates with a note; depth 2 walks the full chain."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {"a.py": "import b\n", "b.py": "import c\n", "c.py": "def f(): return 1\n"},
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])

    shallow = runner.invoke(app, ["deps", "py:a.py:a", "--db", str(db), "--depth", "1"])
    deeper = runner.invoke(app, ["deps", "py:a.py:a", "--db", str(db), "--depth", "3"])
    assert shallow.exit_code == 0 and deeper.exit_code == 0
    # Shallow stops at b → leaves the truncation note.
    assert "truncated" in shallow.stdout.lower()
    # Deeper actually reaches c.py.
    assert "c.py" in deeper.stdout
    assert "truncated" not in deeper.stdout.lower()


def test_cli_deps_disambiguation_when_multiple_matches(runner: CliRunner, tmp_path: Path) -> None:
    """If multiple entities share a name, deps should ask for an entity_id."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "a.py": "def common(): return 1\n",
            "b.py": "def common(): return 2\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])
    result = runner.invoke(app, ["deps", "common", "--db", str(db)])
    assert result.exit_code == 1
    assert "entities match" in result.stdout
    assert "py:a.py:common" in result.stdout
    assert "py:b.py:common" in result.stdout


def test_cli_deps_by_entity_id_works(runner: CliRunner, indexed: Path) -> None:
    """Passing a full entity_id skips name lookup."""
    result = runner.invoke(app, ["deps", "py:auth/login.py:LoginForm", "--db", str(indexed)])
    assert result.exit_code == 0
    assert "LoginForm" in result.stdout
