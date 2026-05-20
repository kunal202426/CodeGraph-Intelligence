# Phase 1 — Thin Vertical Slice

> Per-phase plan. Read this + STATUS.md + AGENTS.md in your session.

**Goal:** End-to-end Python-only pipeline. Index a directory, get rows in DuckDB, search literally by name.
**Estimated:** 5–6 sessions, ~12h
**Exit criteria:** All verifications below pass on `tests/fixtures/sample_repo_py/`.

```bash
uv run codegraph index tests/fixtures/sample_repo_py/
# → "Indexed 8 entities across 4 files in 0.6s"
uv run codegraph search "authenticate"
# → table showing the authenticate function with file:line and docstring
```

## Tasks

### T1.1 — UIR dataclasses
**Files:** `packages/codegraph/uir.py` (~80 LOC), `tests/test_uir.py` (~30 LOC)
**Steps:** Define `UIREntity` and `Edge` Pydantic models per BUILD_PLAN §3. Add `make_entity_id(language, file, qualified_name) -> str` helper. Entity ID format: `<lang>:<file>:<qualified_name>` — locked, do not change after this task.
**Verify:** `uv run pytest tests/test_uir.py` — serialization round-trip, ID format, hash determinism
**Commit:** `T1.1: define UIREntity and Edge schemas`

### T1.2 — IParser protocol
**Files:** `packages/codegraph/parsers/base.py` (~30 LOC)
```python
from typing import Protocol
from pathlib import Path
from pydantic import BaseModel
from codegraph.uir import UIREntity, Edge

class ParseResult(BaseModel):
    entities: list[UIREntity]
    edges: list[Edge]
    errors: list[str] = []

class IParser(Protocol):
    language: str
    def parse(self, path: Path, source: str) -> ParseResult: ...
```
**Verify:** type-check passes, no runtime tests yet
**Commit:** `T1.2: add IParser protocol and ParseResult`

### T1.3 — Python parser emits entities
**Files:** `packages/codegraph/parsers/python.py` (~150 LOC), `parsers/queries/python.scm`, `tests/test_python_parser.py` (~100 LOC), `tests/fixtures/sample_repo_py/auth/login.py`
**Tree-sitter query (`python.scm`):**
```scheme
(function_definition name: (identifier) @function.name) @function.def
(class_definition name: (identifier) @class.name) @class.def
(decorated_definition (decorator) @decorator (function_definition name: (identifier) @function.name)) @function.def
```
**Steps:**
1. `lang = tree_sitter_languages.get_language("python")`
2. `parser = Parser(); parser.set_language(lang); tree = parser.parse(source.encode())`
3. Run query, iterate matches
4. For each function/class node: extract name, span, signature (first line of body slice), docstring (first string literal in body)
5. Build qualified_name by walking parent `class_definition` chain
6. Emit UIREntity with `hash = hashlib.sha256(raw_source.encode()).hexdigest()`

**Test cases:**
- Top-level function `def authenticate(email, password):`
- Class with method (method has `parent_id`)
- Async function (`is_async=True`)
- Decorated function (decorator captured in raw_source)
- Docstring extraction (Google-style triple-quoted)
**Verify:** `uv run pytest tests/test_python_parser.py -v` — 5+ tests pass
**Commit:** `T1.3: emit UIREntity from Python parser via tree-sitter`

### T1.4 — DuckDB schema
**Files:** `packages/codegraph/graph/schema.sql` (~50 LOC), `packages/codegraph/graph/store.py` (~60 LOC), `tests/test_graph_store.py` (~50 LOC)
**Schema (write to `schema.sql`):**
```sql
CREATE TABLE IF NOT EXISTS files (
  path VARCHAR PRIMARY KEY,
  language VARCHAR NOT NULL,
  hash VARCHAR NOT NULL,
  loc INTEGER,
  indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entities (
  entity_id VARCHAR PRIMARY KEY,
  type VARCHAR NOT NULL,
  name VARCHAR NOT NULL,
  qualified_name VARCHAR NOT NULL,
  language VARCHAR NOT NULL,
  file VARCHAR NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  raw_source TEXT,
  docstring TEXT,
  signature TEXT,
  is_exported BOOLEAN DEFAULT TRUE,
  is_async BOOLEAN DEFAULT FALSE,
  parent_id VARCHAR,
  hash VARCHAR NOT NULL,
  summary TEXT,
  embedding FLOAT[384],          -- populated in Phase 3
  embedding_hash VARCHAR,         -- drift detection in Phase 3
  FOREIGN KEY (file) REFERENCES files(path)
);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_file ON entities(file);
CREATE INDEX IF NOT EXISTS idx_entities_qname ON entities(qualified_name);
CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_id);

CREATE TABLE IF NOT EXISTS edges (
  src_id VARCHAR NOT NULL,
  dst_id VARCHAR NOT NULL,
  type VARCHAR NOT NULL,
  line INTEGER NOT NULL,
  confidence FLOAT DEFAULT 1.0,
  is_dynamic BOOLEAN DEFAULT FALSE,
  PRIMARY KEY (src_id, dst_id, type, line)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
```
`GraphStore` class in `store.py` with `__init__(db_path)`, `init_schema()`, `upsert_file(...)`, `upsert_entities(list[UIREntity])`, `upsert_edges(list[Edge])`, `close()`.
**Verify:** `pytest tests/test_graph_store.py` — schema initializes, insert + select round-trip
**Commit:** `T1.4: define DuckDB schema and GraphStore`

### T1.5 — Bulk + idempotent writer
**Files:** `graph/store.py` (extend), `tests/test_graph_store.py` (extend)
**Steps:** Use DuckDB `INSERT OR REPLACE` for entities (key on `entity_id`). For edges `INSERT OR IGNORE` (composite PK handles dup). Batch with `executemany`.
**Verify:** Test inserts 100 entities twice, asserts count == 100; updated `raw_source` overwrites.
**Commit:** `T1.5: bulk idempotent graph writes`

### T1.6 — Walker with .gitignore
**Files:** `packages/codegraph/walker.py` (~80 LOC), `tests/test_walker.py` (~50 LOC)
**Steps:**
1. `walk(root: Path) -> Iterator[tuple[Path, Language]]`
2. Use `pathspec.PathSpec.from_lines("gitwildmatch", gitignore_text)` if `.gitignore` exists
3. Always-exclude: `.git/`, `.codegraph/`, `node_modules/`, `__pycache__/`, `.venv/`, `venv/`, `.mypy_cache/`, `.pytest_cache/`, `dist/`, `build/`
4. Language detection by ext: `.py → PYTHON`, `.ts/.tsx → TYPESCRIPT`, `.js/.jsx → JAVASCRIPT`
5. Skip binary files (`if b'\0' in chunk: skip`)
**Verify:** Walks fixture, gets expected count; .gitignored files excluded
**Commit:** `T1.6: file walker with gitignore support`

### T1.7 — Wire CLI `index`
**Files:** `packages/codegraph/cli.py` (extend index command)
```python
@app.command()
def index(repo: Path = typer.Argument(...), db: Path = typer.Option(".codegraph/graph.duckdb")):
    store = GraphStore(db); store.init_schema()
    parsers = {Language.PYTHON: PythonParser()}
    with Progress() as progress:
        files = list(walk(repo))
        task = progress.add_task("Indexing", total=len(files))
        for path, lang in files:
            if lang not in parsers: continue
            source = path.read_text(errors="replace")
            result = parsers[lang].parse(path.relative_to(repo), source)
            store.upsert_file(path.relative_to(repo), lang, sha256(source))
            store.upsert_entities(result.entities)
            store.upsert_edges(result.edges)
            progress.advance(task)
    rich.print(f"[green]Indexed {n_entities} entities across {n_files} files in {elapsed:.1f}s")
```
**Verify:** `uv run codegraph index tests/fixtures/sample_repo_py/` writes a `.duckdb` file with non-zero entity count.
**Commit:** `T1.7: wire end-to-end index command`

### T1.8 — Wire CLI `search` (literal)
**Files:** `cli.py` (extend), `graph/queries.py` (new)
```python
def search_literal(store, query, limit=20):
    return store.conn.execute("""
      SELECT entity_id, name, file, start_line, docstring
      FROM entities
      WHERE name ILIKE '%' || ? || '%'
         OR docstring ILIKE '%' || ? || '%'
      LIMIT ?
    """, [query, query, limit]).fetchall()
```
Pretty-print results in a Rich Table.
**Verify:** `uv run codegraph search authenticate` returns ≥1 row.
**Commit:** `T1.8: implement literal search command`

### T1.9 — E2E smoke test
**Files:** `tests/test_e2e_index.py` (~60 LOC), expand `tests/fixtures/sample_repo_py/`
**Steps:** Fixture: ≥4 files, ≥8 entities (functions, classes, methods). Test:
1. Index fixture into temp DuckDB
2. Assert entity count ≥ 8
3. Assert specific entity (`py:auth/login.py:authenticate`) exists with correct file + line range
4. Literal search returns it
**Verify:** `uv run pytest tests/test_e2e_index.py -v`
**Commit:** `T1.9: add E2E indexing smoke test`

**Phase 1 exit:** All 6 verification commands pass. Update STATUS.md: Phase 1 DONE, Phase 2 NEXT.
