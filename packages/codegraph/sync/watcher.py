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

import duckdb
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
    """Emitted to the on_change callback after each re-index attempt."""

    path: str  # repo-relative POSIX path, e.g. "src/auth/login.py"
    action: str  # "modified" | "created" | "deleted"
    n_entities: int  # entity count for the file after the update (0 if deleted)
    elapsed_ms: float  # wall-clock time for the re-index in milliseconds
    error: str | None = None  # set when the re-index was skipped/failed (e.g. DB locked)


# How many times to retry a re-index when the DB is locked by another process
# (the MCP server, `serve`, or a concurrent `index`), and the backoff between
# tries. DuckDB is single-writer, so a held lock is transient, not fatal — we
# wait it out rather than letting the watcher thread crash.
_LOCK_RETRY_ATTEMPTS = 4
_LOCK_RETRY_BACKOFF_SEC = 0.25

# After this many consecutive re-index failures for the SAME path (excluding
# transient DB-lock contention, which has its own retry above), the path is
# quarantined and further modify events for it are ignored -- a persistently
# poisoned file must not silently burn a re-index on every save forever.
_QUARANTINE_THRESHOLD = 3


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
        "summary, embedding_hash, (embedding IS NOT NULL) "
        "FROM entities WHERE file = ?",
        [rel_path],
    ).fetchall()

    pending: list[tuple[str, str, str]] = []
    for eid, etype, qname, sig, doc, raw, summary, stored_hash, has_emb in rows:
        text = build_embed_input_from_fields(etype, qname, sig, doc, raw, summary)
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

        # Per-path consecutive-failure tracking. A file that persistently
        # crashes indexing (a parser-killing construct, a permission problem)
        # must not burn a full re-index attempt on every save forever -- after
        # _QUARANTINE_THRESHOLD consecutive failures the path is quarantined:
        # further modify events are dropped until the file is deleted (which
        # clears the record) or the watcher restarts. A success resets the
        # count, so a transient hiccup never quarantines.
        self._fail_counts: dict[str, int] = {}
        self._quarantined: set[str] = set()

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

        # Quarantine gate: a path that has failed _QUARANTINE_THRESHOLD times
        # in a row is dropped silently on further modifications. Deletion
        # still goes through (it clears the poisoned rows AND the quarantine).
        if action == "deleted":
            self._fail_counts.pop(rel_path, None)
            self._quarantined.discard(rel_path)
        elif rel_path in self._quarantined:
            return

        t0 = time.monotonic()

        n_entities = 0
        error: str | None = None
        with self._lock:
            for attempt in range(_LOCK_RETRY_ATTEMPTS):
                try:
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
                    error = None
                    break
                except duckdb.IOException:
                    # DB is held by another process (single-writer limit). Back off
                    # and retry; only report if it never frees up.
                    if attempt < _LOCK_RETRY_ATTEMPTS - 1:
                        time.sleep(_LOCK_RETRY_BACKOFF_SEC * (attempt + 1))
                        continue
                    error = "database busy (held by another process) — change not indexed"
                except Exception as exc:  # noqa: BLE001 — a watcher thread must never die
                    error = f"re-index failed: {exc}"
                    break

        # Consecutive-failure bookkeeping (DB-lock contention doesn't count:
        # it's transient by nature and has its own retry loop above).
        if error is not None and not error.startswith("database busy"):
            count = self._fail_counts.get(rel_path, 0) + 1
            self._fail_counts[rel_path] = count
            if count >= _QUARANTINE_THRESHOLD:
                self._quarantined.add(rel_path)
                error += (
                    f" — giving up on this file after {count} consecutive failures;"
                    " it will be ignored until deleted or the watcher restarts"
                )
        elif error is None:
            self._fail_counts.pop(rel_path, None)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if self._on_change is not None:
            self._on_change(
                ChangeEvent(
                    path=rel_path,
                    action=action,
                    n_entities=n_entities,
                    elapsed_ms=elapsed_ms,
                    error=error,
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
# Staleness check
# --------------------------------------------------------------------------- #


def _row_timestamp(value: object) -> float | None:
    """Convert a DuckDB TIMESTAMP column value to a Unix epoch float, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        from datetime import datetime as _dt

        try:
            value = _dt.fromisoformat(value)
        except ValueError:
            return None
    try:
        return value.timestamp()
    except Exception:  # noqa: BLE001
        return None


def find_stale_files(repo: Path, db: Path) -> list[Path]:
    """Return source files in repo modified more recently than they were indexed.

    Compares each file's mtime against *its own* ``indexed_at`` row, not a
    single repo-wide ``max(indexed_at)``. The watcher and ``reindex`` both
    re-index one file at a time, which advances that file's ``indexed_at``
    without touching any other file's — a single-column max would then hide
    an older file that was edited before the most recent per-file re-index
    but never itself re-indexed. A file with no row at all (new, never
    indexed) is always considered stale. Returns an empty list when the DB
    is missing, has no indexed files, or cannot be opened. The repo path is
    used as-is so callers can pass ``Path(".")`` to check relative to CWD
    (the common usage from ``serve`` / MCP startup). This is the list form
    behind ``count_stale_files``; the ``reindex`` path re-parses exactly
    these files.

    Opens its connection ``read_only=True`` and skips ``init_schema()`` (a
    DDL write) -- this is a pure read on a DB that ``db.exists()`` already
    confirmed is present, and callers (notably ``get_context``) may already
    hold their own read-only connection open on the same file. DuckDB allows
    multiple concurrent read-only connections but rejects a read-write one
    opened alongside them, so a read-write open here would silently raise
    and get swallowed by the except below, making staleness checks go dark
    exactly when something else has the file open.
    """
    if not db.exists():
        return []

    try:
        store = GraphStore(db, read_only=True)
        try:
            rows = store.conn.execute("SELECT path, indexed_at FROM files").fetchall()
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — DB locked, corrupt, etc.
        return []

    if not rows:
        return []

    indexed_at: dict[str, float] = {}
    for path, ts in rows:
        ts_epoch = _row_timestamp(ts)
        if ts_epoch is not None:
            indexed_at[path] = ts_epoch

    stale: list[Path] = []
    try:
        root = Path(repo).resolve()
        for abs_path, _lang in walk(repo):
            try:
                rel = abs_path.relative_to(root).as_posix()
            except ValueError:
                continue
            last_indexed_ts = indexed_at.get(rel)
            if last_indexed_ts is None:
                stale.append(abs_path)  # never indexed
                continue
            try:
                if abs_path.stat().st_mtime > last_indexed_ts:
                    stale.append(abs_path)
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


def find_deleted_files(repo: Path, db: Path) -> list[str]:
    """Return repo-relative paths recorded in the DB that no longer exist on disk.

    Neither the watcher's per-file update nor ``reindex`` ever asks "does this
    already-indexed file still exist?" — they only look at files that are
    still there. A file removed outside of ``codegraph watch`` (``git
    checkout``, a branch switch, a plain ``rm``) leaves its entities and
    edges in the graph indefinitely, since nothing else purges them. This
    compares the ``files`` table against a fresh directory walk and returns
    whatever is missing, so ``reindex`` can clean it up. Returns an empty
    list on any error so a broken check never blocks reindexing files that
    do still exist.

    Opens ``read_only=True`` and skips ``init_schema()`` for the same reason
    as ``find_stale_files``: a read-write open here can collide with a
    caller's already-open read-only connection to the same file and get
    silently swallowed below.
    """
    if not db.exists():
        return []

    try:
        store = GraphStore(db, read_only=True)
        try:
            rows = store.conn.execute("SELECT path FROM files").fetchall()
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — DB locked, corrupt, etc.
        return []

    db_paths = {r[0] for r in rows}
    if not db_paths:
        return []

    try:
        root = Path(repo).resolve()
        existing = {abs_path.relative_to(root).as_posix() for abs_path, _lang in walk(repo)}
    except Exception:  # noqa: BLE001 — repo missing, permission error, etc.
        return []

    return sorted(db_paths - existing)


def git_head(repo: Path) -> str | None:
    """Cheap fingerprint of the repo's current git commit -- no subprocess, no walk.

    Reads ``.git/HEAD`` (and, for a symbolic ref, the ref file it points at)
    instead of shelling out to git. Used to invalidate a TTL cache on a
    branch switch or checkout: those change which files are "current"
    without necessarily touching any file's mtime in a way a plain
    modified-since-indexed check would catch reliably within a still-warm
    cache window. Returns None for a non-git directory, a corrupt ``.git``,
    or any read error -- callers treat that as "can't tell, TTL alone".
    """
    git_path = Path(repo) / ".git"
    try:
        if git_path.is_file():
            # Worktree / submodule: ".git" is a file pointing at the real gitdir.
            content = git_path.read_text(encoding="utf-8", errors="replace").strip()
            if not content.startswith("gitdir:"):
                return None
            git_dir = Path(content.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = (Path(repo) / git_dir).resolve()
        elif git_path.is_dir():
            git_dir = git_path
        else:
            return None

        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None

    if head.startswith("ref:"):
        ref_rel = head.split(" ", 1)[1].strip()
        try:
            return (git_dir / ref_rel).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return head  # packed-refs: no standalone ref file, but the ref name still differs per branch
    return head  # detached HEAD: raw commit hash
