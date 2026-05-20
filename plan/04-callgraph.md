# Phase 4 — Call Graph + Impact + Smells

> Per-phase plan. Read this + STATUS.md + AGENTS.md.

**Goal:** `codegraph impact <function>` shows blast radius. Detect cycles + god classes.
**Estimated:** 4 sessions, ~10h
**Exit:** Five graph-analysis commands available: search, deps, impact, cycles, smells.

## Tasks

### T4.1 — Python call extraction
**Files:** `parsers/python.py` (extend)
**Steps:** Inside each `function_definition`, walk the body subtree, find `call` nodes. For each call: extract callee identifier chain. Best-effort local resolution: look up in same-file qnames; if not found, mark as unresolved string with confidence 0.7.
Tree-sitter query addition:
```scheme
(call function: [(identifier) @callee.name (attribute attribute: (identifier) @callee.attr)]) @call
```
**Verify:** Test function `def login(): authenticate(...)` produces edge `login → authenticate` of type `calls` with correct line.
**Commit:** `T4.1: extract Python call edges`

### T4.2 — TypeScript call extraction
**Files:** `parsers/typescript.py` (extend)
**Steps:** Capture `call_expression`. Method calls captured as `obj.method` qualified name attempts.
**Verify:** TS test for `function login() { authenticate(); }` produces correct edge.
**Commit:** `T4.2: extract TypeScript call edges`

### T4.3 — CLI `impact`
**Files:** `cli.py` (extend), `graph/queries.py` (extend)
**Steps:** Reverse-BFS from target on `calls` edges. Show direct callers (depth 1), then transitive (up to depth 5). Group by file. Add `--depth N` flag.
**Verify:** `uv run codegraph impact authenticate` lists callers across multiple files in tree form.
**Commit:** `T4.3: impact analysis (reverse call BFS)`

### T4.4 — Cycle detection
**Files:** `packages/codegraph/analysis/cycles.py` (~60 LOC), `cli.py cycles` (wired), `tests/test_cycles.py`
**Steps:** Load all `imports` edges. Build adjacency list of files. Run Tarjan SCC. Report SCCs of size ≥ 2.
**Verify:** Fixture with intentional cycle: `a.py → b.py → c.py → a.py`. Assert cycle detected.
**Commit:** `T4.4: detect import cycles via Tarjan SCC`

### T4.5 — God-class heuristic
**Files:** `analysis/smells.py` (~80 LOC), `cli.py smells` (wired), `tests/test_smells.py`
**Heuristics (configurable thresholds):**
- Class with >15 children (methods) → flag `god-class`
- Class spanning >500 LOC → flag `large-class`
- Module with fan-out >20 imports → flag `high-coupling`
- Function with cyclomatic complexity >15 → flag `complex-function` (count if/while/for/elif/and/or in body)

Output sorted by severity.
**Verify:** Test fixture with intentional god class; `codegraph smells` finds it.
**Commit:** `T4.5: detect god classes and coupling smells`
