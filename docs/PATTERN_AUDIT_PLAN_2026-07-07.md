# Pattern audit plan — sweeping today's 8 bug-classes across the rest of the product

**Date:** 2026-07-07
**Context:** [Today's manual test session](REAL_WORLD_STRESS_TEST_2026-07-07.md) found and
fixed 8 real bugs against JobHuntPro. Each one isn't a one-off — it's an instance of a
recurring *shape* of mistake. This doc generalizes each fix into a pattern, then lists
concrete candidates elsewhere in the codebase that share the same shape, found by a
dedicated code audit. Nothing below is fixed yet — this is the hit list for the next round
of manual testing, ordered by where to look first.

**How to use this:** pick a candidate, reproduce it against a real repo (same rule as
today — synthetic fixtures don't surface this class of bug), confirm it's real, then fix +
test the same way today's 8 were handled. Update this file's checkbox as you go.

---

## Priority order (do these first)

1. [ ] **Rust macro calls invisible** (Pattern B) — `println!`, `vec!`, and any
   user-defined macro are structurally invisible to impact/trace on any real Rust codebase.
2. [x] **Java `new Foo()` constructor calls invisible** (Pattern B) — confirmed and fixed
   against a real repo (LedgerGuard). Turned out to be 3 compounding bugs, not 1: the
   constructor-call AST shape itself, field initializers never being scanned for calls at
   all, and the resolver not knowing Java same-package classes need no `import`. Full
   writeup: [REAL_WORLD_STRESS_TEST_2026-07-07-ledgerguard.md](REAL_WORLD_STRESS_TEST_2026-07-07-ledgerguard.md).
3. [x] **`codegraph serve` first semantic-search/ask request stalls unexplained** (Pattern E)
   — confirmed (mild in-session, cache was warm; the underlying warm-up gap was real by code
   inspection) and fixed. Also surfaced a second, unplanned bug while testing this live: the
   module graph only drew `imports` edges, hiding real cross-file `calls`-only dependencies
   (LedgerGuard's graph edge count went 7 → 14 after the fix). Writeup:
   [REAL_WORLD_STRESS_TEST_2026-07-07-ledgerguard.md](REAL_WORLD_STRESS_TEST_2026-07-07-ledgerguard.md).
4. [x] **Resolver source-root allowlist misses `backend`/`apps`/`server`** (Pattern A) —
   fixed via code inspection + unit tests, not a live repro: checked both open projects
   first (JobHuntPro's Python backend uses only relative imports, already handled;
   LedgerGuard is Java, this allowlist is Python-only) and neither happened to exercise this
   exact path today. The gap itself was real and clearly demonstrable in isolation, so fixed
   proactively rather than left unverified. `_SOURCE_ROOT_SEGMENTS` in
   [`resolver.py`](../packages/codegraph/graph/resolver.py) now also includes `apps`,
   `backend`, `server`, `services`, `internal`, `pkg`, `cmd`. 2 new tests in
   `test_resolver.py` (helper-level + an end-to-end `backend/routers/auth.py` case matching
   JobHuntPro's actual layout shape).

Everything else below is real but lower-confidence or smaller blast radius — worth
sweeping once the above are resolved.

---

## Pattern A — assumes a flat/root-level directory, but real repos nest under a project folder

**Today's fixes:** `walker.py` didn't stop at a nested `.git`; `analysis/patterns.py`'s
`classify_layer` only checked a file's first path segment.

**Shape:** any code that reasons about "the top-level directory" or "the source root"
without walking every path segment breaks the moment a repo nests things under a
project/workspace folder — which is the common case for monorepos, not the exception.

**New candidates:**
- `ai/graphrag.py:257` — its own separate `_top_dir` (used at `:304` in
  `select_representatives`, feeding `summarize`) buckets every entity by the literal first
  path segment. On a `cold/backend/...`-shaped monorepo, everything collapses into one
  bucket, degrading `summarize`'s per-directory sampling. **Medium confidence.**
- `graph/resolver.py:659` (`_SOURCE_ROOT_SEGMENTS = {src, packages, lib, app, source}`,
  applied by `_strip_source_roots`) — only singular `app` is listed, not `apps`; `backend`,
  `server`, `services`, `internal`, `pkg`, `cmd` aren't listed at all. A repo laid out as
  `cold/backend/routers/auth.py` with an *absolute* internal import (`from backend.routers
  import auth`, not relative) would silently fail to resolve — the exact failure mode
  fixed for bare imports yesterday, just via a different code path. **Medium-high
  confidence** — test directly against JobHuntPro or another `backend/`-rooted repo.
- `graph/resolver.py:1071` — Ruby bare-`require` resolution only probes `("", "lib/",
  "app/")` prefixes; a `require` under `src/` or a nested sub-app won't resolve.
  **Low-medium confidence**, smaller blast radius (Ruby-only).

---

## Pattern B — call-edge extraction only recognizes one AST shape

**Today's fix:** JSX tags (`jsx_element`/`jsx_self_closing_element`) weren't recognized as
calls to the component they name, in `parsers/typescript.py`.

**Shape:** every parser's call extraction walks exactly one node type
(`call_expression` or equivalent). Any language idiom that's semantically "this invokes
that function/constructor" but isn't literally a call-expression node is invisible —
same root cause as the JSX bug, once per language.

**New candidates (ordered by confidence):**
- **Rust** `parsers/rust.py:474` — only `call_expression`. Misses `macro_invocation`
  (`println!`, `format!`, `vec!`, and any user/DSL macro like `lazy_static!`) entirely.
  Macros are pervasive in real Rust; this is the closest analog to the JSX gap. **High.**
- **Java** `parsers/java.py:382` — only `method_invocation`. Misses
  `object_creation_expression` (`new Foo(...)`) — constructing a type is a call to its
  constructor and is currently invisible, plus annotation references (`@Autowired
  MyService`). **High.**
- **C/C++** `parsers/c_cpp.py:577` — only `call_expression`. Misses `new_expression`
  (`new Foo()`), stack-constructor init (`Foo obj(args)` / `Foo obj{args}`), and
  operator-overload invocations. **Medium.**
- **PHP** `parsers/php.py:448` (`_CALL_NODE_TYPES` at `:58`) — no
  `object_creation_expression`; `new Foo()` is invisible. **Medium.**
- **Kotlin / C# / Scala** (`parsers/kotlin.py`, `csharp.py`, `scala.py`) — these emit
  **no `calls` edges at all** (entities + imports only). Bigger than a single-shape gap —
  confirm whether this is intentional ("not yet implemented") or a real gap; if the
  README/docs claim call-graph support for these languages, that's a documentation bug at
  minimum. **Medium — verify intent first.**
- **Python** `parsers/python.py:475` — only `call`. A paren-less decorator
  (`@login_required`, `@retry`) is an `identifier`/`attribute` node, not `call`, so it
  produces no usage edge. (`@app.route("/x")`-style parametrized decorators already work,
  per the README's framework-aware resolution — this is specifically the bare-decorator
  case.) **Low-medium.**
- **Ruby** `parsers/ruby.py:408` — only `call`. Paren-less command calls (`render
  :partial`, `before_action :authenticate`) may parse as `command`/`method_call` nodes
  depending on grammar version, not `call`. **Low-medium**, verify against the actual
  installed tree-sitter-ruby grammar before assuming it's broken.
- **Go** `parsers/go.py:412` — only `call_expression`. Composite-literal construction
  (`Foo{}`) isn't captured. **Low priority.**

---

## Pattern C — a CLI/MCP surface skips the shared name-resolution helper its siblings use

**Today's fix:** `codegraph trace` didn't resolve names like `deps`/`impact`/`owner` do.

**Audit result: no new candidates found.** All four CLI commands that take an entity
argument now route through `_resolve_entity_or_exit` (`cli.py:569,627,802,1125`); the
three entity-taking MCP tools (`get_entity_context`, `impact_analysis`, `trace_path`)
uniformly take a raw `entity_id` by design (the agent is expected to chain from a prior
tool's returned id, not type a name). This pattern is closed — no action needed right now,
but worth re-checking whenever a new CLI command or MCP tool is added.

---

## Pattern D — hash/cache-based incremental logic has no invalidation escape hatch for a tool upgrade

**Today's fix:** `codegraph index`'s content-hash skip vs. a parser upgrade, fixed with
`--force`.

**New candidates:**
- `cli.py:96` (`_embed_changed`) + the `embedding_hash` column — self-heals fine when
  docstrings/input text change (the hash changes with it), but has **no escape hatch for
  an embedding *model* swap**: same input text + a different model = stale vectors with
  nothing to force a re-embed. `index --force` re-parses but never forces re-embedding.
  **Medium** — same shape as the fixed bug, just one layer over (model version, not
  parser version).
- `server/mcp_server.py:1016` (`_get_unsummarized_entities`, `WHERE summary IS NULL OR
  summary = ''`) — once a summary exists it's never regenerated. If the summarization
  prompt or `_SUMMARIZABLE_TYPES` changes, old summaries (which feed the embed input)
  persist with no force option. **Medium-low.**
- `sync/git_hooks.py:42` — the background post-commit hook runs plain `codegraph index .
  --no-embed`, never `--force`. A parser/tool upgrade won't get picked up through the git
  hook path even after you remember to `--force` manually elsewhere. **Low-medium** — easy
  fix if confirmed worth it, but arguably intentional (hooks should be fast, not eat a
  full re-parse on every commit).

---

## Pattern E — a lazy-loaded singleton's first-use cost lands unexplained on an arbitrary trigger

**Today's fix:** `codegraph watch`'s embedding-model warm-up (was landing on whichever
file got saved first).

**New candidates:**
- **`codegraph serve`** (`cli.py:943` → `server/api.py:50` `create_app`) — **no
  embedding-model warm-up at all.** The first `/api/search?semantic=true` or `/api/ask`
  request pays the full torch-import + model-load cost with zero warning — looks exactly
  like a frozen server. Notably, the **MCP server already fixed this** for itself
  (`_warm_embedding_model` in `mcp_server.py:1155`, called at startup) and `watch` just
  got fixed today — `serve` is the one surface still exposed. **High confidence, high
  value** — this is a browser-facing surface, so a 45-second "hang" on first search would
  look especially broken to a first-time user.
- `server/api.py:194` and `mcp_server.py:611` — `LLM()` (the Anthropic client) is
  constructed fresh per request rather than as a warmed singleton. Smaller cost than the
  embedding model, but same shape. **Low.**

---

## Pattern F — the same logic is hand-duplicated between the CLI and the MCP server instead of shared, so they drift

**Today's fix:** `codegraph index` (CLI) never purged orphaned entities; the MCP
`reindex` tool already did via `find_deleted_files`.

**New candidates:**
- **`get_context`** — `mcp_server.py:661` (`_get_context`) vs. `cli.py:1004` (`context`)
  are two separately hand-written implementations of "hybrid search + callers/callees."
  The MCP version enforces a real `max_tokens` budget with a `truncated` flag and a
  `detail=summary|full` toggle; the CLI version only *estimates* a token-savings number
  for display and has neither budget enforcement nor a full-body mode. Real, already-
  existing feature drift, not just a latent risk. **Medium** — decide whether the CLI
  should get the budget/detail features too, or whether that's deliberately MCP-only.
- **`ask`** across three surfaces (`cli.py:490`, `server/api.py:44`, and
  `mcp_server.py:600`'s `_ask_codebase`) — the CLI and HTTP API both expose `k`/`max_tokens`
  explicitly (15/2000); the MCP tool calls `rag.ask_stream(query)` with no arguments,
  silently relying on `GraphRAG.ask_stream`'s own defaults (currently also 15/2000 —
  coincidentally in sync today). If those defaults ever change in `ai/graphrag.py`, the
  MCP surface silently diverges from the CLI/API contract with nothing to catch it.
  **Low** — cheap to fix (just pass the same defaults explicitly) even though it's not
  broken yet.
- **`status`** (`cli.py:1153`) vs. MCP `index_status` (`mcp_server.py:867`) — another
  separately-written pair reporting index state. Not deep-read in this pass — worth a
  direct diff for field/default drift next time either one is touched.

---

## Not yet covered by this audit

This pass focused on the 6 patterns from today's fixes specifically — it is **not** a
general security/quality review. Known, already-documented, separate gaps (JSX still
missing for module-namespace calls, Pydantic-model-as-type-annotation dead-code false
positives, etc.) are tracked in
[yesterday's report](REAL_WORLD_STRESS_TEST_2026-07-06.md) and not repeated here.
