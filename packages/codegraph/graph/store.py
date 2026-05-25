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
        """Insert or replace a single file row keyed on `path`."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO files (path, language, hash, loc)
            VALUES (?, ?, ?, ?)
            """,
            [path, language.value, hash_, loc],
        )

    def upsert_entities(self, entities: list[UIREntity]) -> None:
        """Bulk insert-or-replace entities. Idempotent on entity_id.

        Uses a registered pandas DataFrame + ``INSERT … SELECT`` rather than
        ``executemany``: DuckDB's parameterised executemany has high per-call
        overhead (~30 ms/row in 1.5.x), which made real-repo indexing take
        minutes. The DataFrame path is ~1000x faster (T2.7).
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
    # Embeddings (T3.2)

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
        # Outbound edges (anything whose src_id includes this file).
        self.conn.execute("DELETE FROM edges WHERE src_id LIKE ?", [f"py:{path}:%"])
        # Entities for this file. FK constraint cascades nothing automatically,
        # but the file row stays so the upsert can update its hash.
        self.conn.execute("DELETE FROM entities WHERE file = ?", [path])

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
