# CodeGraph — Status

## Current

- **Status:** ACTIVE — Phases 10-13 "best of both" roadmap in progress.
- **Phase:** 12 — Richer MCP tools [IN PROGRESS 1/4]
- **Next task:** T12.2 — `trace_path` MCP tool
- **Last session:** 2026-06-03
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

### Phase 5 — GraphRAG + Anthropic LLM [DONE 5/5]
- [x] T5.1 — Anthropic SDK wrapper (LLM.stream/complete, claude-sonnet-4-6, prompt-cached system block, SDK retries, LLMError wrapping; 9 tests, fake-client injection, no live calls)
- [x] T5.2 — Hybrid graph+vector retrieval (vector seeds → 1-hop calls/imports expansion → dedupe → re-rank 0.6·sim+0.3·log-degree+0.1·recency; RetrievedEntity + GraphRAG wrapper; 12 model-free tests via one-hot embeddings)
- [x] T5.3 — `ask` system prompt + context assembly (ask_system.md grounding/citation rules; format_entity_block + build_user_message with char budget; 10 tests)
- [x] T5.4 — CLI `ask` with streaming (GraphRAG.ask_stream wires retrieve→assemble→LLM.stream; cp1252-safe + markup-free token emit; missing-db/no-embeddings/LLMError guards; 6 tests + 1 live-skip)
- [x] T5.5 — `summarize` multi-pass architecture summary (degree-based select_representatives per top-dir → per-subsystem LLM summary → final synthesis → SUMMARY.md; 8 model-free tests)

**Phase 5 result: AI layer complete. `ask` streams grounded, citation-style answers via hybrid GraphRAG retrieval over claude-sonnet-4-6 (prompt-cached system); `summarize` writes a multi-pass architecture overview. All AI wiring is testable without a live key or the embedding model (injected fakes + one-hot vectors). 325 tests passing, 1 live-skip.**
- [ ] T5.5 — Repo architecture summary (`summarize`)

### Phase 6 — Minimal Web UI [IN PROGRESS 1/6]
- [x] T6.1 — FastAPI skeleton: create_app(db) with /api health/graph(module+entity)/search/entity/impact + SSE /api/ask; per-request read-only DuckDB conn; CORS for Vite; GraphStore read_only flag added (12 tests via TestClient, no model/API)
- [x] T6.2 — Vite+React 19+TS 6 scaffold under packages/web; Tailwind v4 via @tailwindcss/vite; d3 + @types/d3; typed api client (src/api); App shell (search/graph/chat/entity regions + /api/health indicator); vite build → packages/codegraph/server/static (gitignored), dev proxy /api→:8765. `npm run build` + `npm run lint` green
- [x] T6.3 — D3 force-directed module graph (components/Graph.tsx): /api/graph?type=module → forceManyBody+forceLink+forceCenter, drag + zoom, click→onSelect; callback-ref avoids sim rebuild; error/empty states; wired into App left pane, selection shown in footer. build+lint green
- [x] T6.4 — SearchBar (debounced 250ms, literal/semantic toggle, results dropdown) + EntityPanel (fetch /api/entity → name/sig/docstring/source); shared entity_id selection highlights graph node. API change: module-graph nodes now keyed by module entity_id (label=file) so node clicks + search results both feed EntityPanel; test_api updated. 337 py tests + build/lint green
- [x] T6.5 — ChatPanel + askStream SSE consumer (api/index.ts parses data: {token|error|done}); transcript with you/codegraph turns, streaming cursor, [entity_id] citations rendered as clickable spans → onSelect (highlights graph + opens entity). build+lint green
- [x] T6.6 — `codegraph serve` (build frontend → uvicorn → mount SPA at / + open browser; --dev skips build for Vite; --no-open flag); create_app mounts StaticFiles after /api routes. 15 API tests (SPA mount, /api precedence, serve guard) + live smoke (serve → / 200 SPA, /api/health + /api/graph 200) verified

**Phase 6 result: full web UI live. `codegraph serve` builds the React/D3 frontend and serves it + the FastAPI graph API on one origin. Module graph (D3 force, drag/zoom), debounced search (literal/semantic), entity detail panel, and an SSE-streaming AI chat with clickable [entity_id] citations — all sharing one selection. 340 tests passing.**
- [ ] T6.4 — Search bar + entity details panel
- [ ] T6.5 — AI chat panel with SSE streaming + citation links
- [ ] T6.6 — `codegraph serve` packages frontend + opens browser
### Phase 7 — MCP Server (killer demo) [IN PROGRESS 1/3]
- [x] T7.1 — MCP server skeleton (mcp 1.27 low-level Server): 4 tools declared (search_code/get_entity_context/impact_analysis/ask_codebase) via tool_definitions(); stdio runner `python -m codegraph.server.mcp_server --db ...`; get_db_path (--db > CODEGRAPH_DB > default). 7 tests + live stdio client roundtrip listed all 4 tools
- [x] T7.2 — call_tool wired: search_code→hybrid_search (embeds only if vectors exist), get_entity_context→entity+depends_on/called_by, impact_analysis→find_callers, ask_codebase→GraphRAG.ask_stream; sync handlers via anyio.to_thread, per-call read-only store, errors→{"error":...} JSON. 14 tests + live client roundtrip (search_code→authenticate, impact→3)
- [x] T7.3 — README MCP section (quickstart + `claude mcp add codegraph -- uv run python -m codegraph.server.mcp_server --db ...`, CODEGRAPH_DB, 4-tool table, demo placeholder docs/demo.gif). Entry point verified (`python -m ... --help`). GIF is a manual recording step (left to repo owner)

**Phase 7 result: MCP server live — Claude Code (or any MCP agent) can call CodeGraph's 4 tools over stdio against an indexed repo. Validated with real MCP client roundtrips. Install documented in README. 354 tests passing.**

### Phase 8 — Polish & Demo Readiness [DONE 2/2]
- [x] T8.1 — README rewrite: hero + docs/demo.gif, what-it-does bullets, quickstart, 3 example queries with output (search/impact/ask), Mermaid architecture diagram, MCP section, stack table, roadmap, acknowledgments
- [x] T8.2 — Benchmarked fastapi (1122 files / 6065 entities / 14601 edges): cold 67s, warm 1.9s, literal query <1ms p50, embed ~690 ent/s, DB 34MB; benchmark table added to README; marked SHIPPED

**Phase 8 result: MVP shipped. README has hero/quickstart/examples/architecture/MCP/benchmarks; STATUS marked SHIPPED. 354 tests passing, 1 live-skip. All 9 CLI commands + web UI + MCP server working on fixtures and real repos (fastapi).**

### Phase 9 — Stretch (optional, post-ship) [IN PROGRESS]
- [x] T9.6 — Dead-code detection: analysis/refactor.py find_dead_code (functions/classes never an edge dst; excludes main/test_/dunders; methods opt-in) + `codegraph deadcode` command. 7 tests + live demo (sample_repo flags fetch_user/make_token/_PrivateForm/etc.). Feature-envy half deferred (needs attribute-access data)
- [x] T9.1 — Git-blame ownership: analysis/ownership.py entity_ownership (git blame --line-porcelain, per-line author tally) + `codegraph owner <entity> --repo <root>` (table + primary owner). 8 tests (throwaway repo, no global config touched) + live demo. --repo must match indexed root; web panel deferred
- [x] T9.3 — Layered-architecture analysis: analysis/patterns.py classify_layer + analyze_layers (file import graph → cross-layer flows + violations where lower imports higher) + `codegraph layers` command. 7 tests (layered fixture: data→presentation violation flagged, downward clean)
- [ ] T9.2/T9.4/T9.5/T9.7/T9.8 — backlog (see plan/09-stretch.md)

### Phase 10 — Language breadth [IN PROGRESS 1/7]
- [x] T10.1 — Go parser: Language.GO enum + .go walker ext + parsers/go.py (function/method/struct/interface/imports/calls via tree-sitter) + queries/go.scm + sample_repo_go fixture + 24 tests. 401 tests passing.
- [x] T10.2 — Rust parser: Language.RUST enum + .rs walker ext + parsers/rust.py (fn/struct/enum/impl/trait/use/calls via tree-sitter) + queries/rust.scm + sample_repo_rust fixture + 24 tests. 426 tests passing.
- [x] T10.3 — Java parser: Language.JAVA enum + .java walker ext + parsers/java.py (class/enum/interface/method/constructor/imports/calls via tree-sitter) + queries/java.scm + sample_repo_java fixture + 24 tests. 451 tests passing.
- [x] T10.4 — Ruby parser: Language.RUBY enum + .rb walker ext + parsers/ruby.py (class/module/def/private-tracking/require/calls via tree-sitter) + queries/ruby.scm + sample_repo_ruby fixture + 21 tests. 473 tests passing.
- [x] T10.5 — PHP parser: Language.PHP enum + .php walker ext + parsers/php.py (class/trait/interface/method/function/use/require/calls via tree-sitter) + queries/php.scm + sample_repo_php fixture + 22 tests. 496 tests passing.
- [x] T10.6 — C/C++ parser: Language.C + Language.CPP enums + .c/.h/.cpp/.cc etc walker exts + parsers/c_cpp.py (CParser + CppParser via shared _CCppMixin; functions/structs/typedef/classes/methods/access-specifier-tracking/#include/calls) + queries/c_cpp.scm + sample_repo_c_cpp fixture + 25 tests. 528 tests passing.
- [x] T10.7 — Cross-language import resolution: extended resolver SQL patterns + _path_to_module_qname + per-language resolution (Go heuristic dir-match, Rust crate::/std:: detection, Java PSR-style path, Ruby require_relative, PHP PSR-4/require, C/C++ local include probe). 17 new tests. 545 tests passing.

**Phase 10 result: 3 → 9 languages (Go, Rust, Java, Ruby, PHP, C, C++ added). All emit into shared embedding/search/ask pipeline automatically. resolver extended for all 7 new languages. 545 tests passing.**

### Phase 11 — Freshness / Watch daemon [DONE 3/3]
**Phase 11 result: Full watch daemon stack. sync/watcher.py with RepoWatcher + index_one_file + delete_one_file (T11.1); codegraph watch CLI (T11.2); staleness guard on serve/MCP startup (T11.3). 41 new tests. 586 tests passing.**

### Phase 12 — Richer MCP tools [IN PROGRESS 1/4]
- [x] T12.1 — `get_context` MCP tool (tool #5): one call = hybrid search + full source + callers/callees for each result. Replaces 3-4 round-trips. `_get_context` handler + `_ENTITY_COLUMNS` fields + `depends_on`/`called_by`/`via` per entity. Limit clamped 1-10. 5 new tests (updated test_mcp.py: `_EXPECTED` set, renamed `test_five_tools_declared`, added `get_context` schema check + 4 behavior tests). 591 tests passing.
- [ ] T12.2 — `trace_path` MCP tool (BFS shortest call path between two symbols)
- [ ] T12.3 — `list_files` + `index_status` tools
- [ ] T12.4 — Mirror as CLI subcommands (`context`, `trace`, `status`)
- [x] T11.1 — `sync/watcher.py` module: `watchdog>=3.0` added; `packages/codegraph/sync/` subpackage with `RepoWatcher`, `index_one_file`, `delete_one_file`, `_DebounceHandler`, `ChangeEvent`. Debounce 300 ms default. Respects ALWAYS_EXCLUDE + .gitignore. Language-agnostic edge cleanup on re-index. 21 new tests. 566 tests passing.
- [x] T11.2 — `codegraph watch <repo>` CLI command: long-running, ASCII status lines ([green]modified[/green] / [red]deleted[/red] with entity count + elapsed ms), Ctrl-C clean shutdown (stop + join with timeout). --no-embed, --debounce, --db flags. Note if index missing. Added "watch" to smoke expected set. 11 new tests. 577 tests passing.
- [x] T11.3 — Staleness guard: `count_stale_files(repo, db)` in sync/watcher.py compares file mtimes vs max(indexed_at). Wired into `codegraph serve` (yellow warning) and MCP `main()` (stderr). CWD used as repo root (best-effort heuristic). 9 new tests. 586 tests passing.

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
- **`watchdog` added as a hard dependency** (Phase 11, T11.1): filesystem watcher for `codegraph watch`. Added `watchdog>=3.0` (installed 6.0.0) to pyproject.toml and BUILD_PLAN.md §1.
- **No Unicode in CLI text output**: Windows cp1252 console can't encode chars like `✓` (U+2713) and crashes with `UnicodeEncodeError` even when stdout is captured by typer.CliRunner inside a UTF-8 buffer (the test environment hides this). Stick to ASCII text in console.print() messages. Rich style tags (`[green]...[/green]`) are fine. (T1.7)
- **Embedding tests skip when model unavailable**: `test_embeddings.py` loads `all-MiniLM-L6-v2` (~80 MB, downloaded from HuggingFace on first use, cached at `~/.cache/huggingface/`). A module-scoped autouse fixture skips the whole module if the model can't load (no network + not cached) instead of failing. CI will download it fresh each run (~45s, occasionally flaky — first attempt 500'd, retry succeeded) until we add an HF cache step. (T3.1)

## Future (defer until MVP shipped)

- (nothing yet)

## Metrics (filled at end of each phase)

- Phase 1 fixture (7 files / 28 entities): index 0.9s
- Phase 2 fastapi (1122 files / 6057 entities / 4405 edges): cold 38.6s, warm re-index 0.8s
  - resolver: 287 in-repo imports resolved, 4118 external (stdlib + pydantic/starlette etc.), 0 wildcard
  - search `get_swagger_ui_html` → fastapi/openapi/docs.py:40 ✓
- Phase 3 embedding throughput: ~690 entities/s (all-MiniLM-L6-v2, CPU)
- Phase 5 ask latency (p50): depends on Anthropic API (not benchmarked offline)
- Phase 8 final benchmarks (fastapi, 1122 files / 6065 entities / 14601 edges):
  - cold index (graph only) ~67s; warm re-index ~1.9s (hash-skip)
  - literal search query <1ms p50 / ~16ms p95 (in-process)
  - graph DB size ~34MB on disk
  - (edge count up vs Phase 2's 4405 → call edges added in Phase 4)
