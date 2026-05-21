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

from pathlib import Path
from types import TracebackType

import duckdb

from codegraph.uir import Edge, Language, UIREntity

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class GraphStore:
    """DuckDB-backed graph storage."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))

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
        """Bulk insert-or-replace entities. Idempotent on entity_id."""
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
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO entities (
                entity_id, type, name, qualified_name, language,
                file, start_line, end_line, start_col, end_col,
                raw_source, docstring, signature,
                is_exported, is_async, parent_id, hash, summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def upsert_edges(self, edges: list[Edge]) -> None:
        """Bulk insert edges. Duplicates (same src+dst+type+line) are dropped."""
        if not edges:
            return
        rows = [(e.src_id, e.dst_id, e.type, e.line, e.confidence, e.is_dynamic) for e in edges]
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO edges (
                src_id, dst_id, type, line, confidence, is_dynamic
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

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
