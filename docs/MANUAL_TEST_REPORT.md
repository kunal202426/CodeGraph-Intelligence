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
unit suite does not. None are data-corrupting; severities below reflect user impact.

### Issue #1 — DuckDB single-writer lock contention (MEDIUM)
Running `codegraph watch` **while the MCP server (or `serve`) holds the database open**
produces a cascade of:
```
_duckdb.IOException: IO Error: Cannot open file "...graph.duckdb":
The process cannot access the file because it is being used by another process.
```
Each blocked re-index runs on a watcher thread, so the failure surfaces as raw
`Exception in thread Thread-N` tracebacks rather than a handled, user-readable message.
The watcher *recovers* once the lock frees (it later re-indexed successfully), so no data
is lost — but the UX is alarming and the behaviour is only documented in the README's
"limitations" prose, not handled in code.
**Repro:** `watch` in terminal A + an active MCP server (or `serve`) in terminal B, then
edit a file.

### Issue #2 — Noisy traceback on `serve` shutdown (LOW / cosmetic)
Stopping `codegraph serve` with Ctrl+C prints a multi-frame
`KeyboardInterrupt` / `asyncio.CancelledError` traceback from uvicorn before exiting.
The server started and served correctly (browser graph + search confirmed working); this
is purely a messy shutdown on Windows, but it reads like a crash to a new user.

### Issue #3 — Embedding model reloads on every CLI invocation (MEDIUM)
Every command that touches semantic search (`search`, `context`, `ask`, first `watch`
re-index) reloads the `all-MiniLM-L6-v2` weights from scratch (`Loading weights ... 103/103`).
On the first `watch` change this added a **~27-second** stall before the re-index
completed. For one-shot CLI calls it adds a few seconds each. The model is cached on
disk but re-loaded into memory per process.

### Issue #4 — Watch re-indexed the entire repo after a single event (MEDIUM)
After the lock errors in Issue #1, a single file change appears to have triggered a
re-index of **100+ files** (the whole tree), not just the changed file. Whether this was
the watcher's recovery path re-scanning everything or an over-broad event match, a single
save should only re-index the file(s) that actually changed.

### Issue #5 — `HF_TOKEN` warning on every semantic op (LOW)
`Warning: You are sending unauthenticated requests to the HF Hub...` prints on every
embedding load. The model is already cached locally, so this network-tinged warning is
misleading noise for an offline-first tool.

### Issue #6 — Dead-code report is noisy (LOW)
`deadcode` returned 111 candidates, but a large share are *known* false positives:
Typer-decorated CLI command functions (reached via decorator registration, invisible to
the static graph), pytest fixtures, and framework entrypoints. The footer warns about
this, but the signal-to-noise ratio limits the feature's usefulness as-is.

---

## Verdict

**Core product works end-to-end on a real, multi-language repo.** Indexing, incremental
freshness, hybrid + semantic search, the full graph-analysis suite, the web UI, the watch
daemon, and the MCP server (install → live query → uninstall) all passed. The only
unverified surface is live LLM generation, which is gated on a paid API key, not a
product defect.

The issues above are quality-of-life and robustness gaps — chiefly around **concurrent
DB access (#1, #4)** and **process-level UX polish (#2, #3, #5)** — not correctness bugs
in the graph itself. They are the natural next work item; see the improvement plan that
follows from this report.
