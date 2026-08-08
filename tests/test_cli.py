"""End-to-end CLI tests via typer.testing.CliRunner."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")


def _make_pyrepo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


# ---------- terminal output encoding ----------


@pytest.mark.parametrize("encoding", ["cp1252", "cp437", "ascii", None])
def test_glyphs_fall_back_to_ascii_on_a_narrow_console(encoding: str | None) -> None:
    """Regression: a bare check mark in the index summary crashed a real
    `codegraph index` on a cp1252 console with UnicodeEncodeError, while the
    whole test suite still passed -- CliRunner captures as UTF-8, so no test
    ever saw it. On any encoding that can't take the fancy set, every glyph
    must degrade to something that actually encodes there.
    """
    from codegraph.cli import _pick_glyphs

    fancy, *glyphs = _pick_glyphs(encoding)
    assert fancy is False
    for glyph in glyphs:
        assert glyph.isascii(), f"{glyph!r} is not ASCII"
        glyph.encode(encoding or "ascii")  # raises on regression


def test_glyphs_use_the_fancy_set_on_utf8() -> None:
    """A capable terminal should still get the nicer glyphs."""
    from codegraph.cli import _pick_glyphs

    fancy, ok, _warn, rule = _pick_glyphs("utf-8")
    assert fancy is True
    assert ok == "✔"
    assert rule == "─"


def test_unknown_encoding_name_degrades_instead_of_raising() -> None:
    """An unrecognized encoding must not blow up at import time."""
    from codegraph.cli import _pick_glyphs

    fancy, ok, _warn, _rule = _pick_glyphs("not-a-real-codec")
    assert fancy is False
    assert ok.isascii()


# ---------- non-terminal progress fallback ----------
#
# Regression: Rich's Progress/Live rendering silently produces zero output
# when the console isn't attached to a real terminal (piped, redirected, or
# captured by another process) -- confirmed by direct repro. A `codegraph
# index` run watched through a log file or a background process looked
# completely frozen for 11+ minutes on a large repo even though it was
# actively working, because the whole progress bar was invisible outside a
# real terminal. `_should_emit_plain_progress` throttles a plain-text
# fallback so those contexts still show something.


def test_should_emit_plain_progress_always_true_on_completion() -> None:
    from codegraph.cli import _should_emit_plain_progress

    assert _should_emit_plain_progress(5, 5, 0.0) is True


def test_should_emit_plain_progress_zero_total_counts_as_complete() -> None:
    from codegraph.cli import _should_emit_plain_progress

    assert _should_emit_plain_progress(0, 0, 0.0) is True


def test_should_emit_plain_progress_false_before_interval() -> None:
    from codegraph.cli import _should_emit_plain_progress

    assert _should_emit_plain_progress(2, 10, 0.5, min_interval=2.0) is False


def test_should_emit_plain_progress_true_after_interval() -> None:
    from codegraph.cli import _should_emit_plain_progress

    assert _should_emit_plain_progress(2, 10, 2.5, min_interval=2.0) is True


def test_index_prints_a_plain_progress_line_when_not_a_terminal(
    runner: CliRunner, tmp_path: Path
) -> None:
    """CliRunner's captured stdout isn't a real terminal, so this exercises the
    same non-terminal path a piped/logged `codegraph index` run hits."""
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Parsing... 7/7 (100%)" in result.stdout


# ---------- index ----------


def test_index_writes_entities_to_db(runner: CliRunner, tmp_path: Path) -> None:
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Indexed" in result.stdout
    assert db.exists()

    store = GraphStore(db)
    try:
        assert store.count_files() >= 1
        assert store.count_entities() >= 5  # module + several functions / classes / methods
    finally:
        store.close()


def test_index_is_idempotent(runner: CliRunner, tmp_path: Path) -> None:
    """Re-indexing the same repo must not double the row counts."""
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    store = GraphStore(db)
    try:
        first_entities = store.count_entities()
        first_files = store.count_files()
    finally:
        store.close()

    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0
    # T2.3: every file should be reported as unchanged on second run.
    assert "unchanged" in result.stdout
    store = GraphStore(db)
    try:
        assert store.count_entities() == first_entities
        assert store.count_files() == first_files
    finally:
        store.close()


def test_index_force_reparses_unchanged_files(runner: CliRunner, tmp_path: Path) -> None:
    """`--force` must re-parse every file even when its hash hasn't changed.

    Regression test: hash-based incremental skip only detects source edits, so
    a plain re-index after upgrading codegraph itself (a parser/resolver fix,
    no source file touched) silently kept serving entities/edges from the old
    parse. `--force` is the escape hatch.
    """
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    store = GraphStore(db)
    try:
        first_entities = store.count_entities()
        first_files = store.count_files()
    finally:
        store.close()

    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db), "--force"])
    assert result.exit_code == 0, result.output
    assert "unchanged" not in result.stdout
    assert f"Parsed {first_files} files" in result.stdout
    store = GraphStore(db)
    try:
        # Re-parsing unchanged source must not duplicate rows.
        assert store.count_entities() == first_entities
        assert store.count_files() == first_files
    finally:
        store.close()


def test_index_indexes_python_and_typescript(runner: CliRunner, tmp_path: Path) -> None:
    """T2.4: TS files index alongside Python files in the same DB."""
    repo = tmp_path / "repo"
    _make_pyrepo(
        repo,
        {
            "main.py": "def foo(): return 1\n",
            "front/index.ts": "export function bar() { return 1; }\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0
    store = GraphStore(db)
    try:
        files = {row[0] for row in store.conn.execute("SELECT path FROM files").fetchall()}
        assert "main.py" in files
        assert "front/index.ts" in files
        # Both should produce entities (modules + decls).
        langs = {
            row[0]
            for row in store.conn.execute("SELECT DISTINCT language FROM entities").fetchall()
        }
        assert "python" in langs
        assert "typescript" in langs
    finally:
        store.close()


def test_index_on_empty_dir_prints_nothing_found(runner: CliRunner, tmp_path: Path) -> None:
    empty = tmp_path / "empty_repo"
    empty.mkdir()
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(empty), "--db", str(db)])
    assert result.exit_code == 0
    assert "No indexable files found" in result.stdout


def test_index_missing_repo_errors(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["index", str(tmp_path / "nope"), "--db", str(tmp_path / "g.duckdb")]
    )
    assert result.exit_code != 0  # typer rejects missing path via exists=True


def test_index_purges_entities_for_files_no_longer_walked(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A file that drops out of the walk entirely (deleted, or newly excluded
    e.g. a nested repo boundary) must have its entities/edges/file row
    removed on the next plain `index`, not just files the walk still sees."""
    repo = tmp_path / "repo"
    _make_pyrepo(
        repo,
        {
            "keep.py": "def keep(): pass\n",
            "gone.py": "def gone(): pass\n",
        },
    )
    db = tmp_path / "graph.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db)])
    store = GraphStore(db)
    try:
        assert store.count_files() == 2
        assert any(
            r[0] == "gone.py" for r in store.conn.execute("SELECT path FROM files").fetchall()
        )
    finally:
        store.close()

    (repo / "gone.py").unlink()
    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0
    assert "Removed 1 file no longer present" in result.stdout

    store = GraphStore(db)
    try:
        paths = {r[0] for r in store.conn.execute("SELECT path FROM files").fetchall()}
        assert paths == {"keep.py"}
        names = {r[0] for r in store.conn.execute("SELECT name FROM entities").fetchall()}
        assert "gone" not in names
        assert "keep" in names
    finally:
        store.close()


def test_generated_file_gets_a_files_row_via_bulk_index(runner: CliRunner, tmp_path: Path) -> None:
    """Same regression as watcher.index_one_file, via the bulk `codegraph
    index` path: a skipped generated/minified file must still get a `files`
    row (zero entities) so staleness checks don't flag it as stale forever."""
    from codegraph.sync.watcher import find_stale_files

    repo = tmp_path / "repo"
    _make_pyrepo(
        repo,
        {
            "real.py": "def real(): pass\n",
            "bundle.js": "var a=1;" * 3000,
        },
    )
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Skipped 1 generated/minified" in result.stdout

    store = GraphStore(db)
    try:
        row = store.conn.execute(
            "SELECT count(*) FROM files WHERE path = ?", ["bundle.js"]
        ).fetchone()
        entity_row = store.conn.execute(
            "SELECT count(*) FROM entities WHERE file = ?", ["bundle.js"]
        ).fetchone()
    finally:
        store.close()
    assert row is not None and row[0] == 1
    assert entity_row is not None and entity_row[0] == 0

    assert find_stale_files(repo, db) == []


# ---------- search ----------


@pytest.fixture
def indexed_db(runner: CliRunner, tmp_path: Path) -> Path:
    """Index the sample fixture into a fresh DB and return the path."""
    db = tmp_path / "graph.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    return db


def test_search_finds_entity_by_name(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "authenticate", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout
    assert "auth/login.py" in result.stdout
    assert "Results for" in result.stdout


def test_search_case_insensitive(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "AUTHENTICATE", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout


def test_search_finds_by_docstring(runner: CliRunner, indexed_db: Path) -> None:
    # The fixture's authenticate() docstring contains "Validate user credentials"
    result = runner.invoke(app, ["search", "credentials", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout


def test_search_partial_match(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "Login", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "LoginForm" in result.stdout


def test_search_no_results_yellow_message(runner: CliRunner, indexed_db: Path) -> None:
    # Literal-only mode gives a crisp "no results"; hybrid/semantic always
    # return nearest neighbours (standard vector-search behaviour).
    result = runner.invoke(
        app,
        ["search", "definitely_no_such_symbol_xyzzy", "--no-hybrid", "--db", str(indexed_db)],
    )
    assert result.exit_code == 0
    assert "No results" in result.stdout


def test_search_limit_flag_caps_output(runner: CliRunner, indexed_db: Path) -> None:
    result = runner.invoke(app, ["search", "form", "--db", str(indexed_db), "--limit", "1"])
    assert result.exit_code == 0
    # "form" matches both LoginForm and _PrivateForm; with limit 1 only the better-ranked one appears.
    assert result.stdout.count("Form") <= 2  # may show in row + title


def test_search_missing_db_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["search", "x", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout


def test_search_semantic_flag_runs(runner: CliRunner, indexed_db: Path) -> None:
    # --semantic now performs real vector search (T3.4). If the model is
    # unavailable it degrades to literal; either way the command succeeds.
    result = runner.invoke(app, ["search", "authenticate", "--semantic", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "authenticate" in result.stdout


# ---------- --version (sanity) ----------


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "codegraph 0.1.0" in result.stdout
