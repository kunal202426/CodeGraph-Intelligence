# CodeGraph — Manual Test Report

**Date:** 2026-06-15
**Tester:** Kunal Mathur (project author)
**Environment:** Windows 11 Home, PowerShell, Python 3.11, `uv`-managed venv
**Repo under test:** CodeGraph itself (Python backend + React/TS web UI + test suite)
**Method:** Interactive, command-by-command manual run-through of every user-facing
surface — CLI, web UI, watch daemon, and the MCP server — on a real multi-language
codebase. No paid LLM calls (Claude Pro, not a pay-per-token API key), so live AI
answers were exercised only via their graceful no-key path.

---

## Scorecard

| # | Surface / Feature | Command | Result |
|---|---|---|---|
| 1 | Full index | `codegraph index .` | **PASS** — 128 files → 1,509 entities, 6,199 edges, 100% embedded, 32.5s cold (re-parsed 49/126 changed) |
| 2 | Incremental skip | `codegraph index .` (re-run) | **PASS** — 0 re-parsed, 0 re-embedded, **0.1s** |
| 3 | Status | `codegraph status` | **PASS** — counts + "up to date" staleness |
| 4 | Hybrid search | `codegraph search "resolve_symbols"` | **PASS** — `literal+semantic` fusion, ranked |
| 5 | Semantic search | `search "user authentication" --semantic` | **PASS** — found `authenticate` by meaning (no literal match) |
| 6 | Dependencies | `codegraph deps resolve_symbols` | **PASS** — full call tree, externals marked correctly |
| 7 | Impact / blast radius | `codegraph impact estimate_tokens` | **PASS** — 5 callers across 3 hops |
| 8 | Import cycles | `codegraph cycles` | **PASS** — "No cycles found" (clean) |
| 9 | Code smells | `codegraph smells` | **PASS** — 14 smells, severity-ranked, all 4 heuristics firing |
| 10 | Dead code | `codegraph deadcode` | **PASS** — 111 candidates; correctly excludes `main`/`test_`/dunders |
| 11 | Layer analysis | `codegraph layers` | **PASS** — correctly reports "no recognizable layers" for this repo's dir naming |
| 12 | Ownership | `codegraph owner resolve_symbols --repo .` | **PASS** — git-blame tally |
| 13 | One-shot context | `codegraph context "symbol resolver"` | **PASS** — 5 entities w/ caller/callee counts |
| 14 | Call-path trace | `codegraph trace <a> <b>` | **PASS** — BFS path found (1 hop) |
| 15 | Web UI | `codegraph serve` | **PASS** — frontend built (587 modules, 216ms), graph + search interactive in browser |
| 16 | Watch daemon | `codegraph watch .` | **PASS** (with caveat — see Issue #1) — detects saves, re-indexes changed file |
| 17 | AI ask (no key) | `codegraph ask "..."` | **PASS** — clean "API key not set" message, no crash |
| 18 | MCP dry-run | `install claude --print-config` | **PASS** — valid JSON, venv python, auto-discover DB |
| 19 | MCP install | `install claude -y` | **PASS** — wrote `~/.claude.json` + `CLAUDE.md` |
| 20 | MCP live call | `index_status` tool over MCP | **PASS** — returned live structured data (127 files / 1,506 entities) |
| 21 | MCP uninstall | `uninstall claude -y` | **PASS** — entry + guide block removed cleanly |

**Not exercised:** live `ask` / `summarize` / `ask_codebase` answers — these require a
pay-per-token Anthropic API key (separate from a Claude Pro subscription). Their
no-key failure path is verified (#17); the graph-retrieval half of `get_context` /
`search_code` (which need **no** API key) is verified via MCP (#20).

---

## Headline results

- **Indexing is correct and fast.** 128 files across 9 languages → 1,509 entities /
  6,199 edges, 100% embedded. Cold index 32.5s; warm re-index **0.1s** (hash-based
  incremental skip works perfectly — 0 files re-parsed, 0 re-embedded).
- **Semantic search delivers the core promise.** `"user authentication"` surfaced
  `authenticate` and `login_handler` by meaning, with zero literal token overlap —
  exactly the differentiator over keyword-only tools.
- **The MCP server connects and serves real data.** A live `index_status` call returned
  structured JSON from the local graph. The graph-query MCP tools work without any API
  key.
- **All graph-analysis commands produce sane, ranked output** on a real codebase.

---

## Issues found

Manual testing on a real, concurrently-accessed index surfaced issues the fixture-based
unit suite does not. None are data-corrupting; severities below reflect user impact. All
six were addressed in the follow-up pass on the same day (see **Fixes applied**).

### Issue #1 — DuckDB single-writer lock contention (MEDIUM) — ✅ FIXED
Running `codegraph watch` **while the MCP server (or `serve`) holds the database open**
produces a cascade of:
```
_duckdb.IOException: IO Error: Cannot open file "...graph.duckdb":
The process cannot access the file because it is being used by another process.
```
Each blocked re-index ran on a watcher thread, so the failure surfaced as raw
`Exception in thread Thread-N` tracebacks rather than a handled, user-readable message.
The watcher *recovered* once the lock freed (no data lost), but the UX was alarming.
**Repro:** `watch` in terminal A + an active MCP server (or `serve`) in terminal B, then
edit a file.
**Fix:** the re-index now retries with backoff (DuckDB locks are transient) and, if the
lock never frees, emits a clean `skipped — database busy` event instead of crashing the
thread. The watcher thread can no longer die. Regression test added.

### Issue #2 — Noisy traceback on `serve` shutdown (LOW / cosmetic) — ✅ FIXED
Stopping `codegraph serve` with Ctrl+C printed a multi-frame
`KeyboardInterrupt` / `asyncio.CancelledError` traceback from uvicorn before exiting.
The server itself started and served correctly (browser graph + search confirmed
working) — purely a messy shutdown, but it read like a crash.
**Fix:** the Ctrl+C `KeyboardInterrupt` is now suppressed; shutdown prints
`Server stopped.`

### Issue #3 — Embedding model reloads on every CLI invocation (MEDIUM) — ◑ PARTIAL
Every command that touches semantic search (`search`, `context`, `ask`, first `watch`
re-index) reloads the `all-MiniLM-L6-v2` weights (`Loading weights ... 103/103`). On the
first `watch` change this added a **~27-second** stall; for one-shot CLI calls, a few
seconds each. Because each CLI invocation is a fresh process, the in-memory model
singleton cannot persist across calls.
**Fix (partial):** when the model is cached, it now loads in **offline mode**, skipping the
HuggingFace Hub network round-trip on startup (measurably faster cold load). A full fix —
persisting the loaded model across CLI invocations via a small local service — remains
future work (tracked in the improvement plan).

### Issue #4 — Watch re-indexed the entire repo after a single event (MEDIUM) — ✅ ROOT-CAUSED
A single file change *appeared* to trigger a re-index of 100+ files. **Root cause:** this
was not a watcher over-trigger. A `.gitattributes` line-ending normalization (added
earlier in the session) caused git operations to rewrite many working-tree files on disk;
watchdog correctly saw each as genuinely modified (new content hash), so re-indexing them
was the *correct* response. `index_one_file` already hash-skips files whose content is
unchanged, so no redundant work occurs for untouched files. No code change needed; the #1
fix additionally ensures such a mass event degrades gracefully under lock contention.

### Issue #5 — `HF_TOKEN` warning on every semantic op (LOW) — ✅ FIXED
`Warning: You are sending unauthenticated requests to the HF Hub...` printed on every
embedding load even though the model is cached locally — misleading noise for an
offline-first tool (it originates in the `tokenizers` backend, not CodeGraph).
**Fix:** loading the cached model in offline mode (see #3) stops the Hub request entirely,
so the warning no longer appears. First-run downloads stay online and unaffected.

### Issue #6 — Dead-code report is noisy (LOW) — ✅ FIXED
`deadcode` returned **111** candidates, dominated by *known* false positives:
Typer-decorated CLI commands (reached via decorator registration, invisible to the static
graph), pytest fixtures, and FastAPI routes.
**Fix:** `find_dead_code` now inspects each entity's leading decorator block and excludes
framework-registered entities (`@app.command`, `@app.get`, `@pytest.fixture`, `@task`, …).
On this repo the list drops from **111 → 54**, removing the dominant false-positive class
while keeping genuinely-unreferenced code. Regression test added.

---

## Fixes applied (2026-06-15 follow-up)

| Issue | Commit subject | Tests |
|---|---|---|
| #1 | `watch: survive DB lock contention instead of crashing the thread` | +1 regression |
| #2, #5 | `serve/embeddings: clean Ctrl+C shutdown + silence HF Hub token warning` | +1 unit |
| #3 | (partial) offline model load, same commit as #5 | — |
| #6 | `deadcode: exclude framework-registered entities to cut false positives` | +1 regression |
| #4 | root-caused (external git renormalization); no code change | — |

Full suite after fixes: **778 passed, 1 skipped**; `ruff check` clean.

---

## Verdict

**Core product works end-to-end on a real, multi-language repo.** Indexing, incremental
freshness, hybrid + semantic search, the full graph-analysis suite, the web UI, the watch
daemon, and the MCP server (install → live query → uninstall) all passed. The only
unverified surface is live LLM generation, which is gated on a paid API key, not a
product defect.

Every issue surfaced by this pass was a quality-of-life or robustness gap — none were
correctness bugs in the graph itself — and all six have since been fixed or root-caused.
The one remaining piece of future work is fully eliminating per-invocation model reload
(#3), which needs a persistent local model service.
