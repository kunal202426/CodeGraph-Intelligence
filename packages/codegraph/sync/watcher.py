# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Debounced filesystem watcher — keeps the CodeGraph index current on save.

Watches a repository root and re-parses only the changed file(s) whenever a
source file is created, modified, or deleted.  Uses the same indexing pipeline
as ``codegraph index`` (parse -> upsert -> resolve -> embed) but scoped to a
single file, so each update completes within a few hundred milliseconds.

Quick-start::

    from pathlib import Path
    from codegraph.sync.watcher import RepoWatcher, ChangeEvent

    def on_change(evt: ChangeEvent) -> None:
        print(f"[{evt.action}] {evt.path}  {evt.n_entities} entities  {evt.elapsed_ms:.0f} ms")

    watcher = RepoWatcher(
        repo=Path("my_repo"),
        db=Path(".codegraph/graph.duckdb"),
        on_change=on_change,
    )
    watcher.start()
    try:
        watcher.join()          # blocks; Ctrl-C exits
    except KeyboardInterrupt:
        watcher.stop()
        watcher.join(timeout=5)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pathspec

from codegraph.graph.resolver import resolve_symbols
from codegraph.graph.store import GraphStore
from codegraph.uir import hash_source
from codegraph.walker import ALWAYS_EXCLUDE, detect_language, walk

# --------------------------------------------------------------------------- #
# Public data-transfer object
# --------------------------------------------------------------------------- #


@dataclass
class ChangeEvent:
    """Emitted to the on_change callback after each successful re-index."""

    path: str  # repo-relative POSIX path, e.g. "src/auth/login.py"
    action: str  # "modified" | "created" | "deleted"
    n_entities: int  # entity count for the file after the update (0 if deleted)
    elapsed_ms: float  # wall-clock time for the re-index in milliseconds


# --------------------------------------------------------------------------- #
# Gitignore / path helpers
# --------------------------------------------------------------------------- #


def _load_gitignore(repo: Path) -> pathspec.PathSpec | None:
    """Load <repo>/.gitignore as a pathspec, or None if it does not exist."""
    gi = repo / ".gitignore"
    if not gi.is_file():
        return None
    try:
        text = gi.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return pathspec.PathSpec.from_lines("gitignore", text.splitlines())


def _in_excluded_dir(abs_path: Path, repo: Path) -> bool:
    """True if abs_path sits inside one of the always-excluded directories."""
    try:
        rel = abs_path.relative_to(repo)
    except ValueError:
        return True  # outside the repo root entirely
    # Check every *directory* component (not the filename itself).
    return any(part in ALWAYS_EXCLUDE for part in rel.parts[:-1])


def _is_gitignored(abs_path: Path, repo: Path, spec: pathspec.PathSpec | None) -> bool:
    """True if the path matches the repo's .gitignore spec."""
    if spec is None:
        return False
    try:
        rel = abs_path.relative_to(repo).as_posix()
    except ValueError:
        return True
    return spec.match_file(rel)


# --------------------------------------------------------------------------- #
# Lazy parser registry (avoids circular import with cli.py)
# --------------------------------------------------------------------------- #

_PARSERS_CACHE: dict | None = None
_PARSERS_INIT_LOCK = threading.Lock()


def _get_parsers() -> dict:
    global _PARSERS_CACHE  # noqa: PLW0603
    if _PARSERS_CACHE is None:
        with _PARSERS_INIT_LOCK:
            if _PARSERS_CACHE is None:
                from codegraph.parsers.c_cpp import CParser, CppParser
                from codegraph.parsers.go import GoParser
                from codegraph.parsers.java import JavaParser
                from codegraph.parsers.php import PHPParser
                from codegraph.parsers.python import PythonParser
                from codegraph.parsers.ruby import RubyParser
                from codegraph.parsers.rust import RustParser
                from codegraph.parsers.typescript import TypeScriptParser
                from codegraph.uir import Language

                _ts = TypeScriptParser()
                _PARSERS_CACHE = {
                    Language.PYTHON: PythonParser(),
                    Language.TYPESCRIPT: _ts,
                    Language.JAVASCRIPT: _ts,
                    Language.GO: GoParser(),
                    Language.RUST: RustParser(),
                    Language.JAVA: JavaParser(),
                    Language.RUBY: RubyParser(),
                    Language.PHP: PHPParser(),
                    Language.C: CParser(),
                    Language.CPP: CppParser(),
                }
    return _PARSERS_CACHE


# --------------------------------------------------------------------------- #
# Core per-file re-index functions (public — used by CLI watch command)
# --------------------------------------------------------------------------- #


def index_one_file(
    repo: Path,
    abs_path: Path,
    db: Path,
    *,
    no_embed: bool = False,
    _parsers: dict | None = None,
) -> int:
    """Re-parse one file and upsert it into the DB.

    Returns the number of entities emitted (including the MODULE entity), or 0
    if the language is unsupported, content is unchanged (hash-skip), or a
    parse error occurs.

    This is the hot path for every debounced watcher event.  It mirrors the
    body of the ``codegraph index`` loop but for a single file.
    """
    lang = detect_language(abs_path)
    if lang is None:
        return 0

    parsers = _parsers if _parsers is not None else _get_parsers()
    parser = parsers.get(lang)
    if parser is None:
        return 0

    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    rel_path = abs_path.relative_to(repo).as_posix()
    current_hash = hash_source(source)

    store = GraphStore(db)
    try:
        store.init_schema()
        prev_hash = store.get_file_hash(rel_path)

        if prev_hash == current_hash:
            # Content unchanged — return the current entity count without re-parsing.
            row = store.conn.execute(
                "SELECT count(*) FROM entities WHERE file = ?", [rel_path]
            ).fetchone()
            return int(row[0]) if row else 0

        # Drop stale rows when re-indexing an already-indexed file.
        if prev_hash is not None:
            # Use a language-agnostic edge pattern (store.clear_file uses py:-only).
            store.conn.execute("DELETE FROM edges WHERE src_id LIKE ?", [f"%:{rel_path}:%"])
            store.conn.execute("DELETE FROM entities WHERE file = ?", [rel_path])

        try:
            result = parser.parse(Path(rel_path), source)
        except Exception:  # noqa: BLE001 — malformed source must not crash the watcher
            return 0

        store.upsert_file(
            path=rel_path,
            language=lang,
            hash_=current_hash,
            loc=source.count("\n") + 1,
        )
        store.upsert_entities(result.entities)
        store.upsert_edges(result.edges)
        resolve_symbols(store)

        if not no_embed:
            _embed_file(store, rel_path)

        return len(result.entities)
    finally:
        store.close()


def _embed_file(store: GraphStore, rel_path: str) -> None:
    """(Re-)embed entities for one file. Non-fatal on model failure."""
    try:
        from codegraph.embeddings.chunking import build_embed_input_from_fields, embed_input_hash
        from codegraph.embeddings.pipeline import embed_batch
    except Exception:  # noqa: BLE001 — torch/model unavailable
        return

    rows = store.conn.execute(
        "SELECT entity_id, type, qualified_name, signature, docstring, raw_source, "
        "embedding_hash, (embedding IS NOT NULL) "
        "FROM entities WHERE file = ?",
        [rel_path],
    ).fetchall()

    pending: list[tuple[str, str, str]] = []
    for eid, etype, qname, sig, doc, raw, stored_hash, has_emb in rows:
        text = build_embed_input_from_fields(etype, qname, sig, doc, raw)
        ihash = embed_input_hash(text)
        if not has_emb or stored_hash != ihash:
            pending.append((eid, text, ihash))

    if not pending:
        return

    try:
        vectors = embed_batch([p[1] for p in pending])
        store.update_embeddings(
            [(pending[i][0], vectors[i].tolist(), pending[i][2]) for i in range(len(pending))]
        )
    except Exception:  # noqa: BLE001 — embedding failure is non-fatal
        pass


def delete_one_file(rel_path: str, db: Path) -> None:
    """Remove all entities, edges, and the file record for a deleted source file."""
    store = GraphStore(db)
    try:
        store.init_schema()
        # Outbound edges from any entity in this file (language-agnostic pattern).
        store.conn.execute("DELETE FROM edges WHERE src_id LIKE ?", [f"%:{rel_path}:%"])
        store.conn.execute("DELETE FROM entities WHERE file = ?", [rel_path])
        store.conn.execute("DELETE FROM files WHERE path = ?", [rel_path])
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Debounce handler
# --------------------------------------------------------------------------- #


class _DebounceHandler:
    """Receives filesystem events, debounces them, and fires re-index actions.

    Intentionally does NOT extend watchdog's FileSystemEventHandler so it can
    be unit-tested without starting a watchdog Observer.  RepoWatcher wraps it
    in a thin bridge when wiring up the Observer.
    """

    def __init__(
        self,
        repo: Path,
        db: Path,
        *,
        no_embed: bool,
        debounce_sec: float,
        gitignore: pathspec.PathSpec | None,
        on_change: Callable[[ChangeEvent], None] | None,
        lock: threading.Lock,
        _parsers: dict | None = None,
    ) -> None:
        self._repo = repo
        self._db = db
        self._no_embed = no_embed
        self._debounce_sec = debounce_sec
        self._gitignore = gitignore
        self._on_change = on_change
        self._lock = lock
        self._parsers = _parsers

        self._timers: dict[str, threading.Timer] = {}
        self._timer_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Called by RepoWatcher's bridge for each watchdog event.

    def dispatch(self, event: object) -> None:
        """Accept a watchdog-style event object and schedule a debounced action."""
        if getattr(event, "is_directory", False):
            return

        abs_path = Path(str(getattr(event, "src_path", ""))).resolve()

        if detect_language(abs_path) is None:
            return
        if _in_excluded_dir(abs_path, self._repo):
            return
        if _is_gitignored(abs_path, self._repo, self._gitignore):
            return

        event_type = getattr(event, "event_type", "modified")
        if event_type in ("created", "modified"):
            self._schedule(abs_path, "modified")
        elif event_type == "deleted":
            self._schedule(abs_path, "deleted")
        elif event_type == "moved":
            # Treat as: delete the old path, index the new path.
            self._schedule(abs_path, "deleted")
            dest = Path(str(getattr(event, "dest_path", ""))).resolve()
            if detect_language(dest) is not None and not _in_excluded_dir(dest, self._repo):
                self._schedule(dest, "modified")

    # ------------------------------------------------------------------

    def _schedule(self, abs_path: Path, action: str) -> None:
        """Debounce: cancel any pending timer for this path and start a new one."""
        key = str(abs_path)
        with self._timer_lock:
            existing = self._timers.pop(key, None)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(self._debounce_sec, self._fire, args=(abs_path, action))
            t.daemon = True
            self._timers[key] = t
            t.start()

    def _fire(self, abs_path: Path, action: str) -> None:
        """Called after the debounce delay — perform the actual DB update."""
        with self._timer_lock:
            self._timers.pop(str(abs_path), None)

        rel_path = abs_path.relative_to(self._repo).as_posix()
        t0 = time.monotonic()

        with self._lock:
            if action == "deleted":
                delete_one_file(rel_path, self._db)
                n_entities = 0
            else:
                n_entities = index_one_file(
                    self._repo,
                    abs_path,
                    self._db,
                    no_embed=self._no_embed,
                    _parsers=self._parsers,
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if self._on_change is not None:
            self._on_change(
                ChangeEvent(
                    path=rel_path,
                    action=action,
                    n_entities=n_entities,
                    elapsed_ms=elapsed_ms,
                )
            )


# --------------------------------------------------------------------------- #
# Public watcher class
# --------------------------------------------------------------------------- #


class RepoWatcher:
    """Watches a repository and keeps its CodeGraph index current.

    Example::

        watcher = RepoWatcher(repo=Path("my_repo"), db=Path(".codegraph/graph.duckdb"))
        watcher.start()
        watcher.join()   # blocks; Ctrl-C to exit

    The watchdog Observer runs as a daemon thread — the process exits cleanly
    even without explicit stop()/join().  Best practice is to call both in a
    finally block or signal handler.
    """

    def __init__(
        self,
        repo: Path,
        db: Path,
        *,
        no_embed: bool = False,
        debounce_sec: float = 0.3,
        on_change: Callable[[ChangeEvent], None] | None = None,
    ) -> None:
        self.repo = Path(repo).resolve()
        self.db = Path(db)
        self.no_embed = no_embed
        self.debounce_sec = debounce_sec
        self.on_change = on_change

        self._lock = threading.Lock()
        self._gitignore = _load_gitignore(self.repo)
        self._handler = _DebounceHandler(
            repo=self.repo,
            db=self.db,
            no_embed=no_embed,
            debounce_sec=debounce_sec,
            gitignore=self._gitignore,
            on_change=on_change,
            lock=self._lock,
        )
        self._observer = None

    def start(self) -> None:
        """Start the filesystem observer in a daemon thread (returns immediately)."""
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        handler = self._handler

        class _Bridge(FileSystemEventHandler):
            def dispatch(self, event):  # type: ignore[override]
                handler.dispatch(event)

        obs = Observer()
        obs.schedule(_Bridge(), str(self.repo), recursive=True)
        obs.daemon = True
        obs.start()
        self._observer = obs

    def stop(self) -> None:
        """Signal the observer to stop (non-blocking). Call join() after."""
        if self._observer is not None:
            self._observer.stop()

    def join(self, timeout: float | None = None) -> None:
        """Block until the observer thread exits."""
        if self._observer is not None:
            self._observer.join(timeout)

    def is_alive(self) -> bool:
        """True while the observer thread is running."""
        return self._observer is not None and self._observer.is_alive()


# --------------------------------------------------------------------------- #
# Staleness check (T11.3)
# --------------------------------------------------------------------------- #


def find_stale_files(repo: Path, db: Path) -> list[Path]:
    """Return source files in repo modified more recently than the last index.

    Compares each file's mtime against ``max(indexed_at)`` in the files table.
    Returns an empty list when the DB is missing, has no indexed files, or
    cannot be opened. The repo path is used as-is so callers can pass
    ``Path(".")`` to check relative to CWD (the common usage from ``serve`` /
    MCP startup). This is the list form behind ``count_stale_files``; the
    ``reindex`` path re-parses exactly these files.
    """
    if not db.exists():
        return []

    try:
        store = GraphStore(db)
        try:
            store.init_schema()
            row = store.conn.execute("SELECT max(indexed_at) FROM files").fetchone()
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — DB locked, corrupt, etc.
        return []

    if row is None or row[0] is None:
        return []

    last_indexed = row[0]
    # DuckDB returns TIMESTAMP as datetime.datetime; guard against string fallback.
    if isinstance(last_indexed, str):
        from datetime import datetime as _dt

        try:
            last_indexed = _dt.fromisoformat(last_indexed)
        except ValueError:
            return []

    try:
        last_indexed_ts = last_indexed.timestamp()
    except Exception:  # noqa: BLE001
        return []

    stale: list[Path] = []
    try:
        for path, _lang in walk(repo):
            try:
                if path.stat().st_mtime > last_indexed_ts:
                    stale.append(path)
            except OSError:
                continue
    except Exception:  # noqa: BLE001 — repo missing, permission error, etc.
        return []

    return stale


def count_stale_files(repo: Path, db: Path) -> int:
    """Count source files in repo modified more recently than the last index.

    Thin wrapper over :func:`find_stale_files`; returns 0 on any error.
    """
    return len(find_stale_files(repo, db))
