# CodeGraph — Status

## Current

- **Phase:** 5 — GraphRAG + Anthropic LLM
- **Next task:** T5.3 — Prompt template + context assembly for `ask`
- **Last session:** 2026-05-25
- **Repo:** https://github.com/kunal202426/CodeGraph-Intelligence

## Phase progress

### Phase 0 — Setup [DONE 6/6]
- [x] T0.1 — Init Python project with uv (pyproject.toml, .gitignore, .python-version, uv.lock)
- [x] T0.2 — AGENTS.md, STATUS.md, BUILD_PLAN.md, source spec at root
- [x] T0.3 — Scaffold package layout (25 stub modules under packages/codegraph/)
- [x] T0.4 — CLI entry point with 8 command stubs + --version
- [x] T0.5 — Pytest skeleton + GitHub Actions CI (3 smoke tests passing)
- [x] T0.6 — Per-phase plan files in `plan/` (10 files, 3.5KB avg, 8.5KB max)

### Phase 1 — Thin Vertical Slice [DONE 9/9]
- [x] T1.1 — UIREntity, Edge, EntityType, Language, make_entity_id, hash_source (19 tests)
- [x] T1.2 — IParser Protocol + ParseResult envelope
- [x] T1.3 — Python parser via tree-sitter (13 tests; fixture sample_repo_py)
- [x] T1.4 — DuckDB schema + GraphStore (15 tests; files/entities/edges with FK + idempotent upserts)
- [x] T1.5 — Bulk-at-scale stress tests (50 entities, 100 edges; perf note logged)
- [x] T1.6 — Walker with .gitignore + language detection (27 tests)
- [x] T1.7 — Wire CLI `index` end-to-end (Rich progress, 6 CLI tests, real-fixture demo)
- [x] T1.8 — Wire CLI `search` literal (ranked ILIKE, Rich Table, 8 tests)
- [x] T1.9 — E2E smoke test (multi-file fixture; 11 acceptance tests)

**Phase 1 result: 28 entities across 7 fixture files indexed in 0.9s. End-to-end `index` + `search` working. 105 tests passing in ~56s.**

### Phase 2 — Multi-file + Symbol Resolution + TypeScript [DONE 7/7]
- [x] T2.1 — Python import statement extraction (15 tests; 7 fixture import edges)
- [x] T2.2 — Symbol resolver (13 tests; 6/7 fixture imports resolved, 1 external)
- [x] T2.3 — Hash-based incremental skip (7 tests; 1.2s → 0.0s on re-index)
- [x] T2.4 — TypeScript / TSX / JS / JSX parser (18 tests; sample_repo_ts indexes)
- [x] T2.5 — TypeScript import resolution (19 tests; named/default/namespace/side-effect + index file probing)
- [x] T2.6 — CLI `deps` command (17 tests; BFS imports+calls + Rich Tree)
- [x] T2.7 — Real-repo smoke (fastapi) + pandas bulk-write perf fix

**Phase 2 result: fastapi (1122 files) → 6057 entities, 4405 edges. Cold index 38.6s, warm re-index 0.8s. `search get_swagger_ui_html` and `deps APIRouter` work. 195 tests passing in ~21s.**

### Phase 3 — Local Embeddings + Semantic Search [IN PROGRESS 1/5]
- [x] T3.1 — sentence-transformers wrapper (all-MiniLM-L6-v2, 384d, 6 tests)
- [x] T3.2 — Embedding storage + cosine vector_search (10 tests; real-embedding round-trip)
- [x] T3.3 — Chunking + auto-embed during index (8 tests; --no-embed flag, graceful skip)
- [x] T3.4 — Hybrid search literal+vector RRF (15 tests; "user authentication"→authenticate via semantic)
- [x] T3.5 — Incremental re-embed via embedding_hash (3 tests; re-index 0.1s, 0 re-embedded)

**Phase 3 result: local semantic search live. `search "user authentication"` → `authenticate` via meaning. First index embeds all; unchanged re-index re-embeds nothing (0.1s, no model load); editing a file re-embeds only its entities. 231 tests passing.**

### Phase 4 — Call Graph + Impact + Smells [DONE 5/5]
- [x] T4.1 — Python call-edge extraction + resolution (10 tests; same-file/imported/external)
- [x] T4.2 — TypeScript call-edge extraction (13 tests; identifier/member/arrow + same-file/imported resolution)
- [x] T4.3 — CLI `impact` reverse-call BFS (9 tests; direct/transitive callers, cycle-safe, blast-radius count)
- [x] T4.4 — Cycle detection via iterative Tarjan SCC (10 tests; file import graph, 3-file cycle, 5000-node chain no overflow)
- [x] T4.5 — God-class / large-class / high-coupling / complex-function smells (11 tests; configurable thresholds, severity-ranked)

**Phase 4 result: full graph-analysis suite live — `search`, `deps`, `impact`, `cycles`, `smells`. impact gives reverse-call blast radius; cycles uses iterative Tarjan SCC (safe on 1000+ file repos); smells flags 4 heuristics ranked by how far over threshold. 280 tests passing.**

### Phase 5 — GraphRAG + Anthropic LLM [IN PROGRESS 2/5]
- [x] T5.1 — Anthropic SDK wrapper (LLM.stream/complete, claude-sonnet-4-6, prompt-cached system block, SDK retries, LLMError wrapping; 9 tests, fake-client injection, no live calls)
- [x] T5.2 — Hybrid graph+vector retrieval (vector seeds → 1-hop calls/imports expansion → dedupe → re-rank 0.6·sim+0.3·log-degree+0.1·recency; RetrievedEntity + GraphRAG wrapper; 12 model-free tests via one-hot embeddings)
- [ ] T5.3 — Prompt template + context assembly for `ask`   ← NEXT
- [ ] T5.4 — CLI `ask` with streaming
- [ ] T5.5 — Repo architecture summary (`summarize`)

### Phase 6 — Minimal Web UI [PENDING]
### Phase 7 — MCP Server (killer demo) [PENDING]
### Phase 8 — Polish & Demo Readiness [PENDING]
### Phase 9 — Stretch (optional, post-ship) [PENDING]

## Blockers / Notes

- (none)

## Plan deviations from BUILD_PLAN.md

- **typer dep**: changed `typer[all]>=0.12` → `typer>=0.12`. The `[all]` extra was removed in typer 0.25+; rich integration is bundled by default now. (T0.1)
- **All MD files in repo root**: BUILD_PLAN.md, AGENTS.md, STATUS.md, README.md, and source spec all live at root, not in `docs/` or `../`. AGENTS.md paths updated accordingly. (T0.2)
- **Boot doc filename is AGENTS.md, not CLAUDE.md**: brand-neutral, agent-agnostic convention. AGENTS.md is honored by multiple MCP-compatible agent tools. Original BUILD_PLAN.md referenced CLAUDE.md; renamed throughout. (T0.2)
- **Editable install rebuild**: After scaffolding `packages/codegraph/`, the editable install from T0.1 (built against empty source) needs `uv pip install -e . --force-reinstall --no-deps` to pick up the new package. Future `uv sync` runs should be fine since the wheel target now matches reality. (T0.3)
- **Ruff ignores B008**: typer.Option() / FastAPI Depends() in argument defaults is the intended usage; B008 false-positives the whole CLI. Globally ignored in pyproject.toml. (T0.4)
- **Dev deps require explicit extra**: `uv sync` alone does NOT install `[project.optional-dependencies].dev`. Run `uv sync --extra dev` to get pytest/ruff/httpx in the venv. Without it, `uv run pytest` may fall through to a global Python install. CI workflow uses `--extra dev`. (T0.5)
- **No AI attribution rule (strict)**: No `Co-Authored-By`, "Generated by ..." tags, or mentions of any AI agent / coding assistant in commit messages, PR descriptions, code comments, or docs. The `anthropic` SDK and `claude-sonnet-4-6` model ID are allowed as dependency/API identifiers. Codified in AGENTS.md "Conventions". (workflow rule)
- **Push-every-commit workflow rule**: Every atomic task ends with `git push` to keep `origin/main` current and CI active. Codified in AGENTS.md. (workflow rule)
- **Commit email fixed to kunal.levitate2024@gmail.com**: Earlier commits used `mathurkunal000@gmail.com` (unverified on GitHub), which prevented the Contributors graph from rendering. All 4 prior commits rewritten via `git filter-branch --env-filter`, local repo config now hardcodes the author. Force-pushed to origin/main. SHAs changed: T1.2 a9b9a91 → cbc7c42, T1.1 eafe8a6 → 084e748, T0.6 cb56645 → 67f4f9d, initial 0f052a8 → 8d00ebc. (workflow fix, post-T1.2)
- **`tree-sitter-languages` FutureWarning suppressed**: The package internally calls a deprecated `Language(path, name)` form; warning is noisy and unactionable until upstream migrates. Suppressed via `warnings.catch_warnings()` around the import + first call in `parsers/python.py`. Revisit if/when we move to tree-sitter ≥ 0.22 (will need API migration). (T1.3)
- **`tests/fixtures/` excluded from ruff**: Fixture files may intentionally carry "bad" code patterns (cycles, dead code, god classes) for future test cases. Added `extend-exclude = ["tests/fixtures"]` in pyproject. (T1.3)
- **DuckDB bulk-insert perf — RESOLVED at T2.7**: `executemany` was ~30 ms/row (per-call overhead), making the first fastapi index take 439s. Added `pandas` and switched `GraphStore._bulk_insert` to a registered-DataFrame `INSERT … SELECT` (~1000x faster: 6000 rows in 0.09s). Also batched the resolver from per-edge DELETE+INSERT (2N round-trips) into one bulk DELETE + one bulk insert, and skipped `clear_file` on cold index. Result: fastapi 439s → 38.6s cold, 0.8s warm. (T1.5 → T2.7)
- **No Unicode in CLI text output**: Windows cp1252 console can't encode chars like `✓` (U+2713) and crashes with `UnicodeEncodeError` even when stdout is captured by typer.CliRunner inside a UTF-8 buffer (the test environment hides this). Stick to ASCII text in console.print() messages. Rich style tags (`[green]...[/green]`) are fine. (T1.7)
- **Embedding tests skip when model unavailable**: `test_embeddings.py` loads `all-MiniLM-L6-v2` (~80 MB, downloaded from HuggingFace on first use, cached at `~/.cache/huggingface/`). A module-scoped autouse fixture skips the whole module if the model can't load (no network + not cached) instead of failing. CI will download it fresh each run (~45s, occasionally flaky — first attempt 500'd, retry succeeded) until we add an HF cache step. (T3.1)

## Future (defer until MVP shipped)

- (nothing yet)

## Metrics (filled at end of each phase)

- Phase 1 fixture (7 files / 28 entities): index 0.9s
- Phase 2 fastapi (1122 files / 6057 entities / 4405 edges): cold 38.6s, warm re-index 0.8s
  - resolver: 287 in-repo imports resolved, 4118 external (stdlib + pydantic/starlette etc.), 0 wildcard
  - search `get_swagger_ui_html` → fastapi/openapi/docs.py:40 ✓
- Phase 3 embedding throughput: TBD
- Phase 5 ask latency (p50): TBD
- Phase 8 final benchmarks: TBD
