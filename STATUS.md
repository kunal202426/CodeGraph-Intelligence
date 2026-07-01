# CodeGraph — Status

## Current

- **Status:** ACTIVE — roadmap complete; product audit + E2E + manual test + usability passes done.
- **Phase:** Maintenance & hardening (post-audit fixes, usability, repo hygiene)
- **Next task:** (optional: persistent model service to kill per-CLI reload — see manual test #3; capture function-local imports — parsers/python.py:184-186; PyPI publish (manual))
- **Last session:** 2026-07-01
- **Repo:** https://github.com/kunal202426/CodeGraph-Intelligence

### Session 2026-07-01 — staleness/reindex fixes, comment cleanup
- `mcp: get_context warns automatically when the index is stale`: previously an agent only
  found out by proactively calling `index_status`. A 300s TTL-cached staleness check now
  injects a warning straight into `get_context` results, naming the stale file count.
- `reindex now purges entities for files deleted outside of watch`: a plain `rm` or a branch
  switch used to leave dead entities in the graph indefinitely. `find_deleted_files` diffs
  the DB against a fresh directory walk and cleans up anything missing.
- `find_stale_files` now compares each file against its own `indexed_at` row instead of a
  single repo-wide max, fixing a case where re-indexing one file could mask a different
  file's real staleness.
- Staleness cache is now keyed on the repo's git HEAD (read directly from `.git/HEAD`, no
  subprocess) so a branch switch inside the TTL window forces a fresh check instead of
  reusing the previous branch's cached answer.
- Full suite: 892 passing, 1 live-skip.
- README: added Kortex as the product's brand name alongside CodeGraph in the title and
  prose; the actual package, CLI commands, and env vars are unchanged.
- Cleaned up leftover internal task-tracker references (`(T5.2)`, `(T12.3)`, etc.) in
  docstrings/comments across 17 files. No behavior change.

### Session 2026-06-15 (eve) — honest docs + MCP hang fix
- `mcp: warm embedding model at startup to fix get_context hang` — first get_context loaded the model in an anyio worker thread; first-time import of torch/sklearn off the main thread deadlocked. Now preloaded in the main thread at server startup. Verified over real stdio: hang -> 0.1s. Auto-use + savings reporting confirmed live on a restarted agent.
- README rewrite for honesty: added "In plain words" (ELI5 library analogy) + "How the token saving actually works" — the key point that CodeGraph cuts *reading/context* tokens, NOT the AI's *writing/output* tokens (which dominate the chat counter), so a single small query barely shows it; value compounds on big repos + long sessions. Added matching caveat to "What it cannot do".

### Session 2026-06-15 (pm) — usability & auto-use pass
Goal from owner feedback: connect a repo once → agent auto-uses CodeGraph → user sees the token savings → one command confirms setup. Three pillars:
- `guide: make CLAUDE.md a required workflow + savings reporting` — the managed agent guide is now a REQUIRED workflow (call get_context before reading files) and tells the agent to report `~N vs ~M tokens (Xx less)`. This is what makes auto-use real.
- `context: surface token savings vs reading files` — `read_baseline_tokens` helper in graph/queries; `get_context` MCP returns tokens_if_read/tokens_saved/savings_ratio; CLI `context` prints a savings footer.
- `cli: add doctor health-check + init self-verify` — new `codegraph doctor` (index/MCP/guide/freshness with fix hints); `init` self-verifies and points to doctor.

### Session 2026-06-15 — manual test pass + fixes
Full interactive manual test of every user-facing surface (CLI, web UI, watch, MCP install→query→uninstall). 21/21 surfaces passed; report at [docs/MANUAL_TEST_REPORT.md](docs/MANUAL_TEST_REPORT.md). Six issues found, all fixed or root-caused:
- `watch: survive DB lock contention` — retry-with-backoff + clean `skipped` event instead of thread-crash tracebacks (issue #1).
- `serve/embeddings: clean Ctrl+C shutdown + silence HF Hub token warning` — suppress KeyboardInterrupt traceback (#2); offline model load when cached, kills the misleading HF unauthenticated warning + speeds cold load (#3 partial, #5).
- `deadcode: exclude framework-registered entities` — skip @app.command/@app.get/@pytest.fixture/@task; candidates 111 → 54 on this repo (#6).
- #4 (mass re-index) root-caused to external git line-ending renormalization, not a watcher bug — no change needed.
- Remaining future work: persistent model service to remove per-invocation reload (#3 full).

### Session 2026-06-14 — maintenance round
- `fix: extend entity-id prefix list` — `_ENTITY_ID_PREFIXES` now covers all 9 indexed languages so Go/Rust/Java/etc entity IDs are exact-matched in `find_entity_by_name_or_id` instead of falling through to name lookup.
- `walker: exclude target/, .eggs, htmlcov` — Rust/Cargo/Maven build output and Python egg dirs are now pruned during traversal, preventing build artefacts from polluting the index.
- `resolver: expand C/C++ stdlib header exclusion list` — ~50 missing C++ stdlib headers (optional, variant, span, ranges, concepts, semaphore, expected, …) no longer get probed against user files.
- `tokens: add truncate_to_tokens() helper` — callers that budget with `estimate_tokens` can now enforce the budget consistently with the same heuristic.

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

### Phase 12 — Richer MCP tools [DONE 4/4]
- [x] T12.1 — `get_context` MCP tool (tool #5): one call = hybrid search + full source + callers/callees for each result. Replaces 3-4 round-trips. `_get_context` handler + `_ENTITY_COLUMNS` fields + `depends_on`/`called_by`/`via` per entity. Limit clamped 1-10. 5 new tests (updated test_mcp.py: `_EXPECTED` set, renamed `test_five_tools_declared`, added `get_context` schema check + 4 behavior tests). 591 tests passing.
- [x] T12.2 — `trace_path` MCP tool: `analysis/traversal.py` `find_shortest_path` (BFS, directed call edges, max_hops cap, external/provisional filtered); `_trace_path` MCP handler returns `{found, hops, path}`; 10 BFS unit tests + 4 MCP integration tests. 605 tests passing.
- [x] T12.3 — `list_files` + `index_status` MCP tools: `_list_files` (path/language/loc/entity_count, optional language filter) + `_index_status` (file/entity/edge/embedded counts + staleness indicator). 5 new MCP tests. 610 tests passing.
- [x] T12.4 — Mirror as CLI subcommands (`context`, `trace`, `status`): `context` (hybrid search + caller/callee counts table), `trace` (BFS shortest call path with arrow chain), `status` (files/entities/edges/embedded + staleness row). Smoke expected-set updated. 13 new tests. 623 tests passing.

**Phase 12 result: MCP surface grew from 4 to 8 tools; 3 new CLI subcommands mirror the most useful tools for standalone use without an MCP client. 623 tests passing.**

### Phase 13 — Multi-agent installer [IN PROGRESS 1/4]
- [x] T13.1 — Installer core + target registry: `codegraph/installer/` subpackage with `Target` ABC, `McpEntry` dataclass, JSON read-modify-write helpers (`_write_entry`/`_remove_entry`/`is_configured`), `_make_entry(db)` default entry builder (uses `sys.executable`), and registry (`register_target`/`get_target`/`list_targets`). Smoke importability list updated. 25 tests. 648 tests passing.
- [x] T13.2 — Claude Code, Cursor, Codex, Gemini targets: `installer/targets/` subpackage with 4 classes auto-registered on `import codegraph.installer`. ClaudeCode: `~/.claude.json` / `.mcp.json`. Cursor: `~/.cursor/mcp.json` / `.cursor/mcp.json`. Codex: `~/.codex/config.json`. Gemini: `~/.gemini/settings.json`. `is_available()` checks `shutil.which` + dir heuristic. Smoke importability list updated. 42 tests. 690 tests passing.
- [x] T13.3 — `codegraph install`/`uninstall` CLI: `install <target> [--db] [--location global|local] [--yes/-y] [--print-config]`; `uninstall <target> [--location] [--yes/-y]`. `--print-config` dry-run uses `_emit()` to avoid Rich line-wrapping JSON. Registry patched via fixture for tests (never touches real agent configs). Smoke expected-set updated. 15 tests. 705 tests passing.
- [x] T13.4 — README install section: "Agent installer" section (4-target table, install/uninstall examples, --location/--yes/--print-config); MCP tools section expanded to 8 tools with get_context as primary; Stack table updated (9 languages, watchdog row); Roadmap updated to reflect Phases 10-13 completion.

**Phase 13 result: `codegraph install <target>` wires the MCP server into Claude Code, Cursor, Codex, or Gemini in one command. Idempotent read-modify-write JSON; never clobbers other config entries. 705 tests passing.**

### Phase 14 — Adoption gate (make Claude actually use it) [DONE 4/4]
- [x] T14.1 — Directive MCP tool descriptions: rewrote all 8 `description=` strings in `tool_definitions()` to say WHEN to use each tool and to prefer it over file-reading/grep, with token framing. `get_context` = "START HERE before reading any source file"; `index_status` = "Call this once at session start". New test asserts every description contains a directive marker. 706 tests.
- [x] T14.2 — CLAUDE.md agent-guide writer: `installer/guide.py` with `write_agent_guide`/`remove_agent_guide`/`has_agent_guide`. Wraps a <400-token CodeGraph block in `<!-- BEGIN/END CODEGRAPH -->` markers; creates CLAUDE.md if absent, replaces only the marked block if present, never clobbers other content; remove deletes the file if it becomes empty. 13 tests.
- [x] T14.3 — Wire guide into install/uninstall: `install` writes the guide to ./CLAUDE.md (`--no-guide` to skip); `uninstall` strips it (`--no-guide` to leave). Test fixture chdirs into tmp_path so the guide never lands in the repo root. 6 new tests. 725 tests passing.
- [x] T14.4 — STATUS.md update (this entry).

**Phase 14 result: the adoption gate. Tool descriptions now direct Claude to prefer CodeGraph over reading files, and `install` drops a CLAUDE.md managed block that tells the agent to call `index_status` at session start and `get_context` before opening files. 725 tests passing.**

### Phase 15 — Value gate (lean get_context) [DONE 5/5]
- [x] T15.1 — `detail` param on get_context: summary (default) returns signature + docstring + 8-line `source_preview` + neighbour ids, omitting `raw_source`; `detail='full'` returns complete bodies. `_SUMMARY_COLUMNS` + `_source_preview` helper. Response reports `detail`. 6 tests.
- [x] T15.2 — Token-aware budget: `ai/tokens.py` `estimate_tokens` (~4 chars/token, dependency-free). get_context `max_tokens` param (default 1500) caps entities by running token estimate; response adds `tokens_estimated` + `truncated` (first entity always included). `graphrag.build_user_message` retrofit to token budget (char_budget kept as back-compat alias). 7 tests.
- [x] T15.3 — Readable labels: `_labels_for(conn, ids)` -> 'name (file:line)'; `trace_path` returns a parallel `labels` list (path stays ids). Not added to get_context neighbour lists (would re-inflate tokens). 1 test.
- [x] T15.4 — CLI `context` leanness verified: regression test asserts body-only `_PRIVATE_TOKEN` never leaks into the counts-only table. 1 test.
- [x] T15.5 — STATUS.md update (this entry).

**Phase 15 result: the value gate. get_context defaults to token-lean summaries (~10x smaller than dumping bodies), enforces a token budget, and reports its own size; full source is opt-in. GraphRAG budgets by tokens, not chars. trace_path output is human-readable. This is what makes calling CodeGraph genuinely cheaper than reading files. 737 tests passing.**

### Phase 16 — Multi-project (one install, every project) [DONE 3/3]
- [x] T16.1 — Walk-up DB discovery: `graph/locate.py` `discover_db(start)` climbs from CWD to root for the nearest `.codegraph/graph.duckdb`. Wired into `get_db_path()` below `CODEGRAPH_DB`: `--db` > `CODEGRAPH_DB` > discovered > default. 5 locate + 3 precedence tests.
- [x] T16.2 — Installer defaults to discovery: `_make_entry(None)` omits `--db` so the server resolves per project; one install serves every repo. `install --db` still pins a DB; CLI prints which mode. `Target` methods accept `Path | None`. 4 tests.
- [x] T16.3 — STATUS.md update (this entry). Note: CLI-from-subdirectory discovery (so `codegraph search` works below the repo root) deferred as low-value — the CLI is normally run from the repo root and `--db` is always available; the agent-facing MCP path is what needed discovery.

**Phase 16 result: a single `codegraph install <agent>` (no `--db`) now works across every project on the machine — the MCP server discovers the nearest index from its working directory. 748 tests passing.**

### Phase 17 — Self-healing freshness [DONE 3/3]
- [x] T17.1 — `reindex` MCP tool (9th tool): re-parses only files changed since the last index (new `find_stale_files` + reuse `index_one_file`), capped at 500 files (suggests CLI beyond that). Derives repo root from the DB path. **Also fixed a latent bug**: DuckDB `INSERT OR REPLACE` doesn't re-evaluate the `indexed_at` DEFAULT, so the watcher's `index_one_file` never advanced a file's timestamp — `count_stale_files` reported it stale forever after a re-index. `upsert_file` now sets `indexed_at = CURRENT_TIMESTAMP` explicitly. 3 reindex tests + end-to-end verification.
- [x] T17.2 — Degraded-search warning: `get_context` returns a `warnings` array when the index has no embeddings (semantic silently degrades to literal). Staleness stays in `index_status` to keep the search hot path off the per-call repo walk. `search_code` keeps its bare-array contract. 2 tests.
- [x] T17.3 — STATUS.md update (this entry).

**Phase 17 result: an agent can refresh a stale index from within the chat (`reindex`) and is told when semantic search is unavailable. Fixed a real staleness bug along the way. 754 tests passing.**

### Phase 18 — First-run legibility + distribution [DONE 5/5]
- [x] T18.1 — Model-download UX: `pipeline.model_is_cached()` best-effort HF-cache probe; `index` prints "Downloading embedding model (~80 MB, first run only)..." before the otherwise-silent download. Network/SSL embed failures now point at `--no-embed` for offline use. 4 tests (mocked, no real download).
- [x] T18.2 — `codegraph init` one-shot: index (DB inside the repo for discovery) + register MCP entry (discovery mode) + write CLAUDE.md guide + print next steps. Fails fast on unknown target before indexing. Added to smoke expected-set. 5 tests.
- [x] T18.3 — PyPI packaging metadata: `keywords`, trove `classifiers` (MIT, Python 3.11/3.12), `[project.urls]`; MIT `LICENSE` file. `uv build` produces a valid wheel + sdist; console script resolves. 6 metadata tests. (`twine upload` left as a manual owner step.)
- [x] T18.4 — README refresh: `init` onboarding, discovery (one install/every repo), the CLAUDE.md mechanism, 9-tool MCP table (incl. `reindex`), Phases 14-18 roadmap, 9-language intro.
- [x] T18.5 — STATUS.md update; roadmap marked complete (this entry).

**Phase 18 result: zero-to-first-query is one command (`codegraph init`), the first-run model download is legible, and the package carries full PyPI metadata + a LICENSE ready to publish. 769 tests passing, 1 live-skip.**

---

## "Actually usable" roadmap (Phases 14-18) — COMPLETE

The post-Phase-13 push that turned a feature-complete project into a tool a solo dev would
leave installed. The two gates that decided it:

- **Adoption (14):** agents now reach for CodeGraph — directive tool descriptions + an
  auto-written `CLAUDE.md` ("call `index_status` at session start, `get_context` before
  reading files").
- **Value (15):** when they do, it's ~10x leaner — `get_context` returns summaries with a
  token budget by default; full bodies are opt-in.

Then: one install serves every project (16, walk-up discovery), agents self-heal a stale
index (17, `reindex` — and a real `indexed_at` bug fixed), and onboarding is one command
with a legible first run + publishable packaging (18). 705 -> 769 tests.

---

## Product audit + E2E verification (dogfood CodeGraph on CodeGraph)

Indexed this repo with itself (128 files, 1,507 entities, 6,186 edges, 100% embedded) and
ran the full agent workflow locally (no paid LLM). Full report: [docs/VERIFICATION.md](docs/VERIFICATION.md).
Headline: `get_context` summary is **9.6x** fewer tokens than reading the files it surfaces
(1,108 vs 10,637) on a representative query. All 9 MCP tools verified live.

Dogfooding surfaced issues the fixture suite missed — **3 fixed, 1 flagged**:
- **[fixed]** `reindex` silently no-op'd with a relative `--db` (`_repo_root_for_db()` returned a
  relative `Path('.')` vs absolute stale paths -> `relative_to()` raised, swallowed). Resolve
  the root; surface a `failed` count.
- **[fixed]** Cross-module call resolution broke on **src-layout** repos (`src/`/`packages/`/`app/`):
  file-derived qnames keep the source-root prefix that imports omit, so internal imports/calls
  fell to `external:`. Source-root-stripped qname aliases. **Impact on this repo: in-repo call
  edges 1,145 -> 1,735 (+51%), `hybrid_search` callers 0 -> 11, impact blast radius works.**
- **[fixed]** `get_context` summaries could re-bloat on hub functions -> cap neighbour lists at 8,
  always report exact counts.
- **[flagged]** Function-local imports (`from X import Y` inside a function) aren't captured
  (`parsers/python.py:184-186`, module-level only by design), so calls via local imports resolve
  external. Design-sensitive (conditional / TYPE_CHECKING imports); candidate next fix.

Also confirmed solid (no action): MCP `call_tool` wraps every handler (never crashes); keyless
`ask` degrades gracefully; entity_ids are Windows-safe (`.as_posix()` + validator); walker excludes
`node_modules`/`.venv`. Removed dead `_stub` helper. 774 tests pass / 1 live-skip.

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
