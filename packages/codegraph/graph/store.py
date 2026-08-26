# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""GraphStore — DuckDB-backed persistence for files / entities / edges.

The store is the only place that touches DuckDB. Parsers emit UIREntity / Edge
streams; the indexer pipes them through `upsert_*` here. Reads happen via
helpers added in later phases (queries.py grows alongside features).

Bulk semantics:
- `upsert_file` / `upsert_entities` use `INSERT OR REPLACE` keyed on
  primary key, so re-indexing the same file overwrites in place.
- `upsert_edges` uses `INSERT OR IGNORE` because edges have a composite
  PK (src, dst, type, line); duplicates from re-parse are silently dropped.

File order matters: insert the file row first (FK on entities.file), then
the entities, then the edges.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from types import TracebackType

import duckdb
import pandas as pd

from codegraph.uir import Edge, Language, UIREntity

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_ENTITY_COLUMNS = (
    "entity_id",
    "type",
    "name",
    "qualified_name",
    "language",
    "file",
    "start_line",
    "end_line",
    "start_col",
    "end_col",
    "raw_source",
    "docstring",
    "signature",
    "is_exported",
    "is_async",
    "parent_id",
    "hash",
    "summary",
)
_EDGE_COLUMNS = ("src_id", "dst_id", "type", "line", "confidence", "is_dynamic")

# Must match the FLOAT[N] width in schema.sql and EMBEDDING_DIM in
# embeddings/pipeline.py.
_EMBEDDING_DIM = 384

# Monotonic counter so concurrent staging registrations never collide.
_stage_counter = itertools.count()


def escape_like(text: str) -> str:
    """Neutralize SQL `LIKE` wildcards in a literal string.

    A file path routinely contains `_` (and could contain `%`), both of which
    are `LIKE` wildcards -- `_` matches any single character. Interpolating a
    raw path into a `%:{path}:%` pattern therefore lets one file's cleanup
    match a *different* file's edges (e.g. clearing `test_resolver.py` also
    matches `testXresolver.py`), silently deleting real edges. Escape the
    wildcards and the escape char itself, then pair with `ESCAPE '\\'` in the
    query. The backslash must be escaped first so the escapes added for `%`/`_`
    aren't themselves doubled.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class GraphStore:
    """DuckDB-backed graph storage."""

    def __init__(self, db_path: Path | str, *, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        if not read_only and str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path), read_only=read_only)

    # ------------------------------------------------------------------
    # Lifecycle

    def init_schema(self) -> None:
        """Apply the schema (idempotent — CREATE … IF NOT EXISTS throughout)."""
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.execute(sql)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Writes

    def upsert_file(
        self,
        path: str,
        language: Language,
        hash_: str,
        loc: int | None = None,
    ) -> None:
        """Insert or replace a single file row keyed on `path`.

        ``indexed_at`` is set to CURRENT_TIMESTAMP explicitly: DuckDB's
        INSERT OR REPLACE does not re-evaluate column defaults on replace, so
        without this an already-indexed file keeps its original timestamp and
        ``count_stale_files`` would report it stale forever after a re-index.
        """
        self.conn.execute(
            """
            INSERT OR REPLACE INTO files (path, language, hash, loc, indexed_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [path, language.value, hash_, loc],
        )

    def upsert_files(self, rows: list[tuple[str, Language, str, int | None]]) -> None:
        """Bulk insert-or-replace file rows: `(path, language, hash, loc)` each.

        Same semantics/columns as `upsert_file`, batched -- a real-repo index
        that called `upsert_file` once per file (alongside `upsert_entities`/
        `upsert_edges`, one DataFrame-register round-trip each) spent most of
        its wall time on that per-file overhead rather than parsing: DuckDB's
        register/execute/unregister cycle costs low-tens-of-ms even for a
        handful of rows, and a real repo has thousands of files. Callers
        should accumulate rows across many files and flush in one batch.
        """
        if not rows:
            return
        data = [(path, language.value, hash_, loc) for path, language, hash_, loc in rows]
        df = pd.DataFrame(  # noqa: F841 — referenced by name in SQL
            data, columns=["path", "language", "hash", "loc"]
        )
        staging = f"_staging_files_{next(_stage_counter)}"
        self.conn.register(staging, df)
        try:
            self.conn.execute(
                f"""
                INSERT OR REPLACE INTO files (path, language, hash, loc, indexed_at)
                SELECT path, language, hash, loc, CURRENT_TIMESTAMP FROM {staging}
                """
            )
        finally:
            self.conn.unregister(staging)

    def upsert_entities(self, entities: list[UIREntity]) -> None:
        """Bulk insert-or-replace entities. Idempotent on entity_id.

        Uses a registered pandas DataFrame + ``INSERT … SELECT`` rather than
        ``executemany``: DuckDB's parameterised executemany has high per-call
        overhead (~30 ms/row in 1.5.x), which made real-repo indexing take
        minutes. The DataFrame path is ~1000x faster.
        """
        if not entities:
            return
        rows = [
            (
                e.entity_id,
                e.type.value,
                e.name,
                e.qualified_name,
                e.language.value,
                e.file,
                e.start_line,
                e.end_line,
                e.start_col,
                e.end_col,
                e.raw_source,
                e.docstring,
                e.signature,
                e.is_exported,
                e.is_async,
                e.parent_id,
                e.hash,
                e.summary,
            )
            for e in entities
        ]
        self._bulk_insert("entities", _ENTITY_COLUMNS, rows, on_conflict="replace")

    def replace_file_entities(self, paths: list[str], entities: list[UIREntity]) -> None:
        """Swap in a fresh parse's entities for `paths`, preserving embeddings.

        Upsert-then-prune, deliberately not delete-then-insert. `INSERT OR
        REPLACE` only writes the columns it names, so an entity_id that survives
        a re-parse keeps its `embedding`/`embedding_hash`; anything the new
        parse no longer produces is then deleted by set difference.

        The old path cleared a file's entities with `DELETE FROM entities WHERE
        file = ?` before re-inserting. That took each row's embedding with it,
        so the indexer's self-healing check (`not has_embedding or stored_hash
        != input_hash`) saw NULL for every entity in the file and re-embedded
        the lot -- even for byte-identical source. Measured on a real 97k-entity
        index: a re-index that re-parsed 0 of 16046 files still re-embedded all
        97,239 entities, ~33 minutes to recompute vectors that could not have
        changed. Same waste per save in watch mode.

        Embeddings still refresh when they should: an entity whose source or
        summary actually changed gets a different `embed_input_hash`, and the
        indexer re-embeds it on that mismatch.
        """
        self.upsert_entities(entities)
        # No stale paths means nothing to prune -- a first index, or a batch of
        # brand-new files. Entities are still written above.
        if not paths:
            return
        keep_ids = [e.entity_id for e in entities]
        df = pd.DataFrame({"path": paths})  # noqa: F841 — referenced by name in SQL
        staging = f"_staging_keep_{next(_stage_counter)}"
        self.conn.register(staging, df)
        try:
            if keep_ids:
                keep_df = pd.DataFrame({"entity_id": keep_ids})  # noqa: F841 — used in SQL
                keep_staging = f"_staging_keepids_{next(_stage_counter)}"
                self.conn.register(keep_staging, keep_df)
                try:
                    self.conn.execute(
                        f"DELETE FROM entities WHERE file IN (SELECT path FROM {staging}) "
                        f"AND entity_id NOT IN (SELECT entity_id FROM {keep_staging})"
                    )
                finally:
                    self.conn.unregister(keep_staging)
            else:
                # A file that now parses to nothing: drop everything it had.
                self.conn.execute(
                    f"DELETE FROM entities WHERE file IN (SELECT path FROM {staging})"
                )
        finally:
            self.conn.unregister(staging)

    def upsert_edges(self, edges: list[Edge]) -> None:
        """Bulk insert edges. Duplicates (same src+dst+type+line) are dropped."""
        if not edges:
            return
        rows = [(e.src_id, e.dst_id, e.type, e.line, e.confidence, e.is_dynamic) for e in edges]
        self._bulk_insert("edges", _EDGE_COLUMNS, rows, on_conflict="ignore")

    def _bulk_insert(
        self,
        table: str,
        columns: tuple[str, ...],
        rows: list[tuple],
        *,
        on_conflict: str,
    ) -> None:
        """Insert `rows` into `table` via a registered DataFrame (fast path).

        `on_conflict` is "replace" (INSERT OR REPLACE) or "ignore"
        (INSERT OR IGNORE). The DataFrame is registered under a unique name
        and unregistered afterwards so connections stay clean.
        """
        if not rows:
            return
        verb = "INSERT OR REPLACE" if on_conflict == "replace" else "INSERT OR IGNORE"
        col_list = ", ".join(columns)
        df = pd.DataFrame(rows, columns=list(columns))  # noqa: F841 — referenced by name in SQL
        staging = f"_staging_{table}_{next(_stage_counter)}"
        self.conn.register(staging, df)
        try:
            self.conn.execute(f"{verb} INTO {table} ({col_list}) SELECT {col_list} FROM {staging}")
        finally:
            self.conn.unregister(staging)

    # ------------------------------------------------------------------
    # Embeddings

    def update_embeddings(self, rows: list[tuple[str, list[float], str]]) -> None:
        """Bulk-set `embedding` (FLOAT[384]) + `embedding_hash` for entities.

        `rows` is a list of (entity_id, vector, embedding_hash) where `vector`
        is a list of EMBEDDING_DIM plain Python floats. Entities not present in
        `rows` keep their existing embedding. Uses a registered DataFrame +
        ``UPDATE … FROM`` join, same fast path as the bulk inserts.
        """
        if not rows:
            return
        df = pd.DataFrame(
            {
                "entity_id": [r[0] for r in rows],
                "emb": [r[1] for r in rows],
                "emb_hash": [r[2] for r in rows],
            }
        )  # noqa: F841 — referenced by name in SQL
        staging = f"_staging_emb_{next(_stage_counter)}"
        self.conn.register(staging, df)
        try:
            self.conn.execute(
                f"""
                UPDATE entities
                   SET embedding = {staging}.emb::FLOAT[{_EMBEDDING_DIM}],
                       embedding_hash = {staging}.emb_hash
                  FROM {staging}
                 WHERE entities.entity_id = {staging}.entity_id
                """
            )
        finally:
            self.conn.unregister(staging)

    def count_embedded(self) -> int:
        """Number of entities that currently have an embedding."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE embedding IS NOT NULL"
        ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Summaries (agent-driven, written back via MCP)

    def update_summaries(self, rows: list[tuple[str, str]]) -> None:
        """Bulk-set the `summary` text for entities.

        `rows` is a list of (entity_id, summary). Entities not present in `rows`
        keep their existing summary. Uses a registered DataFrame + ``UPDATE …
        FROM`` join, the same fast path as `update_embeddings`.
        """
        if not rows:
            return
        df = pd.DataFrame(
            {
                "entity_id": [r[0] for r in rows],
                "summary": [r[1] for r in rows],
            }
        )  # noqa: F841 — referenced by name in SQL
        staging = f"_staging_summary_{next(_stage_counter)}"
        self.conn.register(staging, df)
        try:
            self.conn.execute(
                f"""
                UPDATE entities
                   SET summary = {staging}.summary
                  FROM {staging}
                 WHERE entities.entity_id = {staging}.entity_id
                """
            )
        finally:
            self.conn.unregister(staging)

    def count_summarized(self) -> int:
        """Number of entities that currently have a non-empty summary."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE summary IS NOT NULL AND summary <> ''"
        ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Per-file lookups + cleanup (T2.3 incremental)

    def get_file_hash(self, path: str) -> str | None:
        """Return the stored hash for `path`, or None if the file isn't indexed."""
        row = self.conn.execute("SELECT hash FROM files WHERE path = ?", [path]).fetchone()
        return row[0] if row else None

    def clear_file(self, path: str) -> None:
        """Delete all entities + outbound edges for `path`.

        Used during incremental re-index when a file's hash has changed: drop the
        stale rows before writing the fresh parse, so deleted functions / removed
        imports don't linger in the graph.
        """
        # Outbound edges (anything whose src_id includes this file). Matches
        # any language's entity_id shape (`<lang>:<path>:<qualified_name>`),
        # not just Python's -- a `py:`-only pattern here previously left
        # every non-Python language's stale edges (removed calls, imports,
        # inherits) in the graph forever on every re-index after the first.
        # `escape_like` keeps the path's own `_`/`%` from acting as wildcards.
        self.conn.execute(
            "DELETE FROM edges WHERE src_id LIKE ? ESCAPE '\\'", [f"%:{escape_like(path)}:%"]
        )
        # Entities for this file. FK constraint cascades nothing automatically,
        # but the file row stays so the upsert can update its hash.
        self.conn.execute("DELETE FROM entities WHERE file = ?", [path])

    def clear_file_edges(self, paths: list[str]) -> None:
        """Delete only the outbound edges for `paths`, leaving entities intact.

        The re-parse path for a file that still exists: its edges must be
        rebuilt from scratch (a removed call/import has to disappear), but its
        entity rows should survive so `replace_file_entities` can preserve
        their embeddings. `clear_files` remains the right call for a file that
        genuinely vanished from the repo.
        """
        if not paths:
            return
        df = pd.DataFrame(  # noqa: F841 — referenced by name in SQL
            {"pat": [f"%:{escape_like(p)}:%" for p in paths]}
        )
        staging = f"_staging_clearedges_{next(_stage_counter)}"
        self.conn.register(staging, df)
        try:
            self.conn.execute(
                f"DELETE FROM edges WHERE EXISTS "
                f"(SELECT 1 FROM {staging} s WHERE edges.src_id LIKE s.pat ESCAPE '\\')"
            )
        finally:
            self.conn.unregister(staging)

    def clear_files(self, paths: list[str]) -> None:
        """Bulk version of `clear_file` -- two DELETEs total instead of two
        per path, for the same per-statement-overhead reason `upsert_files`/
        `upsert_entities`/`upsert_edges` batch across files."""
        if not paths:
            return
        # `pat` carries the wildcard-escaped LIKE pattern (see `escape_like`);
        # `path` stays raw for the exact-match entity delete.
        df = pd.DataFrame(  # noqa: F841 — referenced by name in SQL
            {"path": paths, "pat": [f"%:{escape_like(p)}:%" for p in paths]}
        )
        staging = f"_staging_clear_{next(_stage_counter)}"
        self.conn.register(staging, df)
        try:
            self.conn.execute(
                f"DELETE FROM edges WHERE EXISTS "
                f"(SELECT 1 FROM {staging} s WHERE edges.src_id LIKE s.pat ESCAPE '\\')"
            )
            self.conn.execute(f"DELETE FROM entities WHERE file IN (SELECT path FROM {staging})")
        finally:
            self.conn.unregister(staging)

    # ------------------------------------------------------------------
    # Counts (useful for CLI summaries + tests)

    def count_files(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()
        return int(row[0]) if row else 0

    def count_entities(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()
        return int(row[0]) if row else 0

    def count_edges(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()
        return int(row[0]) if row else 0
