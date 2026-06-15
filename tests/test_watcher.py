"""Tests for T11.1 — RepoWatcher and per-file index helpers.

All tests run with no_embed=True so the embedding model is never loaded.
Debounce-timer tests use debounce_sec=0 (fires in background thread after ~1ms)
followed by a generous sleep so the test thread sees the final DB state.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from codegraph.graph.store import GraphStore
from codegraph.sync.watcher import (
    ChangeEvent,
    RepoWatcher,
    _DebounceHandler,
    _in_excluded_dir,
    _is_gitignored,
    delete_one_file,
    index_one_file,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _py_parsers() -> dict:
    """Minimal parser dict (Python only) — avoids loading all 9 parsers."""
    from codegraph.parsers.python import PythonParser
    from codegraph.uir import Language

    return {Language.PYTHON: PythonParser()}


class _MockEvent:
    """Minimal watchdog-compatible event for testing _DebounceHandler.dispatch."""

    def __init__(
        self,
        src_path: str | Path,
        event_type: str = "modified",
        is_directory: bool = False,
        dest_path: str | Path | None = None,
    ) -> None:
        self.src_path = str(src_path)
        self.event_type = event_type
        self.is_directory = is_directory
        self.dest_path = str(dest_path or src_path)


def _make_handler(
    tmp_path: Path,
    fired: list,
    debounce_sec: float = 0.0,
) -> tuple[_DebounceHandler, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    db = tmp_path / "graph.duckdb"
    handler = _DebounceHandler(
        repo=repo,
        db=db,
        no_embed=True,
        debounce_sec=debounce_sec,
        gitignore=None,
        on_change=lambda e: fired.append(e),
        lock=threading.Lock(),
        _parsers=_py_parsers(),
    )
    return handler, repo, db


# --------------------------------------------------------------------------- #
# ChangeEvent
# --------------------------------------------------------------------------- #


def test_change_event_fields() -> None:
    evt = ChangeEvent(path="src/foo.py", action="modified", n_entities=3, elapsed_ms=42.0)
    assert evt.path == "src/foo.py"
    assert evt.action == "modified"
    assert evt.n_entities == 3
    assert evt.elapsed_ms == 42.0


# --------------------------------------------------------------------------- #
# _in_excluded_dir
# --------------------------------------------------------------------------- #


def test_in_excluded_dir_node_modules(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    p = repo / "node_modules" / "lodash" / "index.js"
    assert _in_excluded_dir(p, repo) is True


def test_in_excluded_dir_venv(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    p = repo / ".venv" / "lib" / "python3.11" / "site.py"
    assert _in_excluded_dir(p, repo) is True


def test_in_excluded_dir_normal_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    p = repo / "src" / "server" / "main.py"
    assert _in_excluded_dir(p, repo) is False


def test_in_excluded_dir_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    p = tmp_path / "other" / "file.py"
    assert _in_excluded_dir(p, repo) is True


# --------------------------------------------------------------------------- #
# _is_gitignored
# --------------------------------------------------------------------------- #


def test_is_gitignored_no_spec(tmp_path: Path) -> None:
    assert _is_gitignored(tmp_path / "src" / "foo.py", tmp_path, None) is False


def test_is_gitignored_matched(tmp_path: Path) -> None:
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitignore", ["*.pyc"])
    p = tmp_path / "src" / "foo.pyc"
    assert _is_gitignored(p, tmp_path, spec) is True


def test_is_gitignored_not_matched(tmp_path: Path) -> None:
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitignore", ["*.pyc"])
    p = tmp_path / "src" / "foo.py"
    assert _is_gitignored(p, tmp_path, spec) is False


# --------------------------------------------------------------------------- #
# index_one_file
# --------------------------------------------------------------------------- #


def test_index_one_file_adds_entities(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    src = repo / "utils.py"
    src.write_text("def greet(name):\n    pass\n", encoding="utf-8")

    n = index_one_file(repo, src, db, no_embed=True, _parsers=_py_parsers())
    assert n >= 1

    with GraphStore(db) as store:
        store.init_schema()
        rows = store.conn.execute(
            "SELECT name FROM entities WHERE file = 'utils.py' AND type != 'module'"
        ).fetchall()
    assert {r[0] for r in rows} == {"greet"}


def test_index_one_file_unknown_extension_returns_zero(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    src = repo / "data.csv"
    src.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

    n = index_one_file(repo, src, db, no_embed=True, _parsers=_py_parsers())
    assert n == 0


def test_index_one_file_hash_skip_returns_same_count(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    src = repo / "app.py"
    src.write_text("def run():\n    pass\n", encoding="utf-8")

    parsers = _py_parsers()
    n1 = index_one_file(repo, src, db, no_embed=True, _parsers=parsers)
    n2 = index_one_file(repo, src, db, no_embed=True, _parsers=parsers)
    assert n1 >= 1
    assert n1 == n2


def test_index_one_file_updated_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    src = repo / "app.py"
    src.write_text("def run():\n    pass\n", encoding="utf-8")
    parsers = _py_parsers()
    index_one_file(repo, src, db, no_embed=True, _parsers=parsers)

    # Add a second function — entity count must grow.
    src.write_text("def run():\n    pass\n\ndef stop():\n    pass\n", encoding="utf-8")
    n2 = index_one_file(repo, src, db, no_embed=True, _parsers=parsers)
    assert n2 >= 2

    with GraphStore(db) as store:
        store.init_schema()
        names = {
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM entities WHERE file = 'app.py' AND type != 'module'"
            ).fetchall()
        }
    assert "stop" in names


# --------------------------------------------------------------------------- #
# delete_one_file
# --------------------------------------------------------------------------- #


def test_delete_one_file_removes_entities_and_file_record(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    src = repo / "utils.py"
    src.write_text("def greet():\n    pass\n", encoding="utf-8")
    index_one_file(repo, src, db, no_embed=True, _parsers=_py_parsers())

    delete_one_file("utils.py", db)

    with GraphStore(db) as store:
        store.init_schema()
        n_ent = store.conn.execute(
            "SELECT count(*) FROM entities WHERE file = 'utils.py'"
        ).fetchone()[0]
        n_file = store.conn.execute(
            "SELECT count(*) FROM files WHERE path = 'utils.py'"
        ).fetchone()[0]
    assert n_ent == 0
    assert n_file == 0


# --------------------------------------------------------------------------- #
# RepoWatcher interface
# --------------------------------------------------------------------------- #


def test_repoWatcher_interface(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    w = RepoWatcher(repo=repo, db=tmp_path / "graph.duckdb")
    assert callable(w.start)
    assert callable(w.stop)
    assert callable(w.join)
    assert callable(w.is_alive)
    # Observer not started yet.
    assert w.is_alive() is False


def test_repoWatcher_stores_params(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    w = RepoWatcher(repo=repo, db=db, no_embed=True, debounce_sec=0.5)
    assert w.repo == repo.resolve()
    assert w.db == db
    assert w.no_embed is True
    assert w.debounce_sec == 0.5


# --------------------------------------------------------------------------- #
# _DebounceHandler — event filtering
# --------------------------------------------------------------------------- #


def test_debounce_handler_ignores_directory_events(tmp_path: Path) -> None:
    fired: list = []
    handler, repo, _ = _make_handler(tmp_path, fired)

    handler.dispatch(_MockEvent(src_path=repo / "src", is_directory=True))
    time.sleep(0.1)
    assert fired == []


def test_debounce_handler_ignores_unknown_extension(tmp_path: Path) -> None:
    fired: list = []
    handler, repo, _ = _make_handler(tmp_path, fired)

    handler.dispatch(_MockEvent(src_path=repo / "data.csv"))
    time.sleep(0.1)
    assert fired == []


def test_debounce_handler_ignores_excluded_dir(tmp_path: Path) -> None:
    fired: list = []
    handler, repo, _ = _make_handler(tmp_path, fired)

    # .venv is in ALWAYS_EXCLUDE.
    p = repo / ".venv" / "lib" / "helpers.py"
    handler.dispatch(_MockEvent(src_path=p))
    time.sleep(0.1)
    assert fired == []


# --------------------------------------------------------------------------- #
# _DebounceHandler — successful re-index and callback
# --------------------------------------------------------------------------- #


def test_debounce_handler_fires_callback_for_python_file(tmp_path: Path) -> None:
    fired: list = []
    handler, repo, _ = _make_handler(tmp_path, fired, debounce_sec=0.0)

    src = repo / "app.py"
    src.write_text("def run():\n    pass\n", encoding="utf-8")

    handler.dispatch(_MockEvent(src_path=src.resolve(), event_type="modified"))
    time.sleep(0.6)  # 0 s debounce + ~200 ms for parse+DB

    assert len(fired) == 1
    assert fired[0].path == "app.py"
    assert fired[0].action == "modified"
    assert fired[0].n_entities >= 1
    assert fired[0].elapsed_ms >= 0


def test_debounce_handler_reports_db_lock_without_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    """A DB held by another process must surface a clean error event, not a
    thread crash (regression for the single-writer lock-contention finding)."""
    import duckdb
    from codegraph.sync import watcher as watcher_mod

    fired: list = []
    handler, repo, _ = _make_handler(tmp_path, fired, debounce_sec=0.0)

    src = repo / "app.py"
    src.write_text("def run():\n    pass\n", encoding="utf-8")

    def _always_locked(*_args, **_kwargs):
        raise duckdb.IOException("Cannot open file: used by another process")

    # Zero backoff so the retry loop completes instantly (sleep(0) is a no-op).
    monkeypatch.setattr(watcher_mod, "index_one_file", _always_locked)
    monkeypatch.setattr(watcher_mod, "_LOCK_RETRY_BACKOFF_SEC", 0.0)

    handler.dispatch(_MockEvent(src_path=src.resolve(), event_type="modified"))
    time.sleep(0.4)

    assert len(fired) == 1
    assert fired[0].error is not None
    assert "busy" in fired[0].error
    assert fired[0].n_entities == 0


def test_debounce_handler_fires_delete_callback(tmp_path: Path) -> None:
    fired: list = []
    handler, repo, db = _make_handler(tmp_path, fired, debounce_sec=0.0)

    # Seed the DB first so the delete has something to remove.
    src = repo / "app.py"
    src.write_text("def run():\n    pass\n", encoding="utf-8")
    index_one_file(repo, src, db, no_embed=True, _parsers=_py_parsers())

    handler.dispatch(_MockEvent(src_path=src.resolve(), event_type="deleted"))
    time.sleep(0.4)

    assert len(fired) == 1
    assert fired[0].action == "deleted"
    assert fired[0].n_entities == 0


def test_debounce_coalesces_rapid_events(tmp_path: Path) -> None:
    fired: list = []
    # 100 ms debounce; fire 5 events 10 ms apart -> should coalesce to 1.
    handler, repo, _ = _make_handler(tmp_path, fired, debounce_sec=0.1)

    src = repo / "app.py"
    src.write_text("def run():\n    pass\n", encoding="utf-8")

    evt = _MockEvent(src_path=src.resolve(), event_type="modified")
    for _ in range(5):
        handler.dispatch(evt)
        time.sleep(0.01)

    time.sleep(0.5)  # let the single debounced timer fire and finish
    assert len(fired) == 1
