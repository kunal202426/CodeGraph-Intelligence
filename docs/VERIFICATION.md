# CodeGraph — End-to-End Verification (dogfood report)

CodeGraph indexed and queried **with CodeGraph**, on a real multi-language repo (this
one: Python backend + React/TS frontend + tests). Run locally, no paid LLM calls. The
goal: confirm the product actually works end-to-end at scale and delivers the core
promise — an agent queries the graph instead of re-reading files, at a fraction of the
tokens.

## Scorecard

| # | Claim | Result |
|---|---|---|
| 1 | Build sound | **PASS** — 774 tests pass / 1 live-skip; `ruff check` + `format --check` clean |
| 2 | Indexes real code at scale, 0 errors | **PASS** — 128 files -> 1,507 entities, 6,186 edges, 41.8s; 100% embedded (1,507/1,507) |
| 3 | **Token savings (the core)** | **PASS** — `get_context` summary **9.6x** fewer tokens than reading the files it surfaces (1,108 vs 10,637) on a representative query |
| 4 | Semantic search beats keyword | **PASS** — a meaning-only query ("cap the size of the answer…") surfaces `ai/tokens.py` + the truncation tests, which literal search can't rank by intent |
| 5 | All 9 MCP tools work live | **PASS** — every tool returns valid structured output (`ask_codebase` via its graceful no-key path) |
| 6 | Agent loop is real | **PASS** — `index_status` -> edit -> stale -> `reindex` (1 file, 0 failed) -> fresh |
| 7 | One install, every project | **PASS** — walk-up discovery resolves the index from a subdirectory |
| 8 | Lean default / full on demand | **PASS** — summary omits `raw_source`, caps neighbour lists, reports exact counts; `detail="full"` returns bodies; token budget truncates + flags |

## Headline: token savings

`get_context("the command line interface entry point and its commands")`:

```
entities returned : 5   (across mcp_server.py + 4 others)
get_context summary : 1,108 tokens
reading those files : 10,637 tokens   <- what an agent would otherwise spend
>>> 9.6x fewer tokens
```

The ratio scales with how much code the agent would otherwise read: ~10x on
broad/large-file questions, ~2x on a question answered by one or two small files. Either
way the agent also gets the **call-graph neighbourhood** (callers/callees) that reading
isolated files never provides — the differentiator embedding-less tools can't match.

## Bugs found and fixed by this dogfood pass

Dogfooding on real code surfaced issues that the fixture-based test suite did not:

1. **`reindex` silently did nothing with a relative `--db`.** `_repo_root_for_db()`
   returned a relative `Path(".")` while `find_stale_files` yields absolute paths, so
   `index_one_file`'s `relative_to()` raised, was swallowed, and reindex reported success
   having done nothing. **Fixed** (resolve the root; surface a `failed` count).
2. **Cross-module call resolution broke on src-layout repos.** Module qnames were derived
   from file paths (`packages.codegraph.graph.queries`) but imports omit the source-root
   prefix (`codegraph.graph.queries`), so every internal absolute import — and thus every
   cross-module call — fell through to `external:`, gutting `impact_analysis` and
   `trace_path` on any `src/`/`packages/`/`app/` project. **Fixed** (source-root-stripped
   qname aliases). Measured impact on this repo: resolved imports 1,162 -> 2,046, in-repo
   call edges 1,145 -> 1,735 (**+51%**), `hybrid_search` callers **0 -> 11**.
3. **`get_context` summaries could re-bloat** once hub functions gained many callers.
   **Fixed** (cap neighbour id lists at 8, always report exact counts).

## Known limitation (flagged, not yet fixed)

- **Function-local imports** (`from X import Y` inside a function body) are not captured
  (`python.py` extracts module-level imports only, by design), so calls made via a local
  import resolve as `external:` rather than to the in-repo definition. Impact is partial
  (e.g. `estimate_tokens` resolves 7 of 9 callers; the 2 misses are local imports).
  Fixing it is design-sensitive (conditional / `TYPE_CHECKING`-guarded imports) and is a
  candidate next change at `parsers/python.py:184-186`.

## Reproduce

```bash
uv run ruff check && uv run ruff format --check && uv run pytest -q   # 774 pass / 1 skip
uv run codegraph index . --db .codegraph/graph.duckdb                 # self-index
uv run codegraph status --db .codegraph/graph.duckdb                  # metrics
# Drive the 9 MCP tools via codegraph.server.mcp_server.call_tool (see tests/test_mcp.py)
```
