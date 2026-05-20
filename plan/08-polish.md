# Phase 8 — Polish & Demo Readiness

> Per-phase plan. Read this + STATUS.md + AGENTS.md.

**Goal:** MVP shipped. STATUS.md says SHIPPED. README is portfolio-grade.
**Estimated:** 2 sessions, ~5h
**Exit:** All 10 "Definition of Shipped" criteria in BUILD_PLAN.md §9 are true.

## Tasks

### T8.1 — README rewrite
**Files:** `README.md`
**Sections in order:**
1. Hero: `docs/demo.gif` + tagline ("Local AI memory layer for your codebase")
2. What it does — 3 bullets
3. Quickstart — 5 commands max
4. Example queries — 3 with expected output
5. Architecture — Mermaid diagram of pipeline
6. Stack — bulleted table
7. Roadmap — what was cut from MVP (link to Phase 9 stretch list)
8. Acknowledgments — tree-sitter, DuckDB, sentence-transformers, Anthropic SDK
**Verify:** README renders on GitHub correctly; demo GIF plays.
**Commit:** `T8.1: rewrite README with demo and quickstart`

### T8.2 — Benchmark + final STATUS update
**Files:** `STATUS.md`, optionally `benchmarks/results.md`
**Steps:** Index `tiangolo/fastapi` (or a comparable real repo), record:
- Files indexed, entities, edges
- Index time (cold)
- Index time (warm = incremental, no changes)
- Embedding time
- Semantic search latency p50/p95
- Ask latency p50/p95 (full GraphRAG)
- DuckDB file size

Append to README. Mark STATUS as `SHIPPED`.
**Verify:** Numbers in README; STATUS shows all phases DONE.
**Commit:** `T8.2: benchmark on fastapi and mark MVP shipped`
