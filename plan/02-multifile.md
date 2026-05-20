# Phase 2 — Multi-file + Symbol Resolution + TypeScript

> Per-phase plan. Read this + STATUS.md + AGENTS.md.

**Goal:** Index real Python and TypeScript repos. Cross-file imports resolve. `codegraph deps <entity>` traces dependencies.
**Estimated:** 5 sessions, ~12h
**Exit:** TS + Python indexed in same DB; cross-file imports resolve; incremental skip works.

## Tasks

### T2.1 — Python import statement extraction
**Files:** `parsers/python.py` (extend), `parsers/queries/python.scm` (extend), test additions
**Steps:** Tree-sitter query captures `import_statement` and `import_from_statement`. Emit one `Edge` per imported name with type=`"imports"`, dst_id provisionally = `py:??:<imported_name>` (unresolved; resolver fixes in T2.2). Also emit a placeholder Module entity per file.
**Verify:** Parse a file with `from auth.login import authenticate`; assert edge `(file_entity)-[imports]->(py:??:authenticate)`.
**Commit:** `T2.1: extract Python import statements`

### T2.2 — Symbol resolver
**Files:** `packages/codegraph/graph/resolver.py` (~120 LOC), `tests/test_resolver.py`
**Steps:**
1. After all files parsed and inserted, run a resolution pass.
2. Build name → entity_id index from `qualified_name` and `(file, name)`.
3. For each edge with unresolved `dst_id` (matching `?:??:*`), attempt resolution:
   - `from x.y import z`: candidate qnames are `x.y.z`, `x/y.py::z`
   - Relative imports (`from . import foo`): resolve relative to source file's package
4. Update edge `dst_id` if resolved; lower `confidence` for heuristic matches.
5. Unresolved edges get a final dst_id like `external:numpy.array` with confidence 0.5.
**Verify:** Test creates 3 files importing each other; resolver pass closes all `?` placeholders.
**Commit:** `T2.2: cross-file symbol resolution pass`

### T2.3 — Incremental hash-based skip
**Files:** `cli.py` (extend index), `tests/test_e2e_index.py` (extend)
**Steps:** Before parsing a file, compute SHA-256. Look up `files.hash`. If unchanged, skip parse + write. Summary at end: "Re-parsed 3 of 312 files".
**Verify:** Indexing the fixture twice — second run is significantly faster and writes zero new rows.
**Commit:** `T2.3: incremental indexing via file hashing`

### T2.4 — TypeScript parser
**Files:** `parsers/typescript.py` (~150 LOC), `parsers/queries/typescript.scm`, `tests/test_typescript_parser.py`, `tests/fixtures/sample_repo_ts/`
**Steps:** `tree_sitter_languages.get_language("typescript")`. Capture `function_declaration`, `class_declaration`, `interface_declaration`, `arrow_function` (when assigned to const), `method_definition`. JSX-aware (`.tsx`) — tree-sitter handles it.
**Verify:** Parse fixture TS file with exported function, default export, class with method, interface. All 4 entities emit correctly.
**Commit:** `T2.4: TypeScript/JSX parser via tree-sitter`

### T2.5 — TypeScript import resolution
**Files:** `parsers/typescript.py` (extend), `resolver.py` (extend)
**Steps:** Capture `import_statement`. Relative paths resolve against source file directory; add `.ts/.tsx/.js/.jsx/index.ts` extensions. Reading `tsconfig.json` `paths` is **deferred**; leave a TODO.
**Verify:** TS fixture has `import { authenticate } from './auth/login'`; resolver creates correct edge.
**Commit:** `T2.5: TypeScript import resolution`

### T2.6 — CLI `deps`
**Files:** `cli.py` (extend), `graph/queries.py` (extend)
**Steps:** BFS from a starting entity following `imports`/`calls` edges, depth-limited (default 3). Render with `rich.tree.Tree`.
**Verify:** `uv run codegraph deps authenticate --depth 2` prints tree of dependencies.
**Commit:** `T2.6: add deps command (transitive dependency tree)`

### T2.7 — Real-repo smoke
**Files:** None new. Update STATUS.md with metrics.
**Steps:** `git clone tiangolo/fastapi /tmp/fastapi-smoke; uv run codegraph index /tmp/fastapi-smoke`. Record: # files, # entities, # edges, elapsed time. Target: <30s for fastapi.
**Verify:** Manual — search a known fastapi function (`get_swagger_ui_html`) and find it.
**Commit:** `T2.7: smoke test on real repo (fastapi)`
