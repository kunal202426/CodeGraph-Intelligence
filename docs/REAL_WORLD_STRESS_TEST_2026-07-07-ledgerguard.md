# Real-world stress test: LedgerGuard (Java + JS)

**Date:** 2026-07-07 (same day as the [JobHuntPro pass](REAL_WORLD_STRESS_TEST_2026-07-07.md);
continuation of the [pattern audit plan](PATTERN_AUDIT_PLAN_2026-07-07.md), testing
candidate #2 — "Java `new Foo()` constructor calls invisible.")

**Target:** LedgerGuard — Java (Maven, Spring) backend + JS/Vite frontend, 47 files / 241
entities / ~900 edges.

## Bug found and fixed: Java constructor calls were invisible to impact/trace — three
compounding causes, all found from one real repro

**Repro:** `WelfordStats` (`backend/src/main/java/com/ledgerguard/engine/WelfordStats.java`)
is genuinely used — constructed as a field initializer in `AnomalyScorer` and repeatedly in
tests — but `codegraph impact WelfordStats` showed **zero callers**, and it was flagged as
dead code. Confirmed against `codegraph deadcode` before touching anything.

Three separate, compounding root causes, each fixed and tested independently:

### 1. `new Foo(...)` wasn't recognized as a call at all (parser)
[`parsers/java.py`](../packages/codegraph/parsers/java.py)'s call extraction only walked
`method_invocation` nodes. `object_creation_expression` (`new Foo(...)`) is a structurally
different node — exact same bug shape as today's JSX fix. **Fix:** `_iter_call_nodes`/
`_callee_name` now also recognize `object_creation_expression`, extracting the base class
name (handling the `generic_type` wrapper for `new HashMap<>()`-style parameterized types).
4 new tests in `test_java_parser.py`.

### 2. Field initializers were never scanned for calls at all (parser)
Even after fix #1, the exact real-world line (`private final WelfordStats baseline = new
WelfordStats();`, a field initializer, not inside a method body) still produced zero
edges — `_emit_class` only ever dispatched `method_declaration`/`constructor_declaration`
children for call scanning; `field_declaration` was skipped entirely. **Fix:** field
declarations are now scanned too, with any calls in the initializer attributed to the
enclosing class entity itself (there's no per-field entity, and the class is the natural
owner — a field initializer runs as part of every instance's construction). 2 new tests.

### 3. Java same-package classes need no `import`, but the resolver only checked "same file" or "an explicit import" (resolver)
Even after fixes #1/#2 in an isolated same-file test, the *real* repro still failed:
`WelfordStats` and `AnomalyScorer` are different files in the same package
(`com.ledgerguard.engine`) — idiomatic Java needs no `import` for that. [`resolver.py`](../packages/codegraph/graph/resolver.py)'s
`_resolve_call` only ever checked the caller's own file or its explicit imports, so any
same-package sibling reference fell straight through to `external:`. Likely the dominant
cause of this repo's alarming 819/889 external-import rate. **Fix:** a new
`entities_by_dir` index (file's directory → its exported entities) added to the resolver;
`_resolve_call` now falls back to it, gated to `java:`-prefixed calls only (conf 0.85, one
step below an explicit import's 0.9) so Python/TS/JS resolution — which correctly requires
an explicit import — is untouched. 1 new end-to-end test in `test_resolver_new_langs.py`.

**Verified live:** `codegraph impact WelfordStats` now shows `AnomalyScorer` as a caller;
`codegraph index . --force` went from 849 → 902 edges (framework/methodcall-related count
differs slightly run to run depending on embedding state, edge count is the reliable
signal here).

Full suite green throughout: 1142 passed, 1 skipped, 0 regressions (latest run).

## Bug found and fixed: `codegraph serve` had no embedding-model warm-up (pattern-plan candidate #3)

Live-tested at the browser: first semantic search took 2-3s, later ones near-instant — mild
in this session (OS file cache already warm from extensive testing), but the underlying gap
was real and confirmed by code inspection: `watch` and the MCP server both warm the
embedding model at startup, `serve` didn't. On a genuinely cold cache this is the same class
of unexplained stall `watch` had (fixed earlier today). **Fix:** `codegraph serve`
(`cli.py`) now warms the model before starting, with the same `Loading embedding model
(one-time)...` message, skipped entirely when the index has no embeddings to search. 2 new
tests in `test_staleness.py`.

## Bug found and fixed: the module graph only drew `imports` edges, hiding real import-free dependencies

Noticed live: LedgerGuard's module graph (47 files) rendered as mostly disconnected dots —
only **7 edges**. Root cause: [`server/api.py`](../packages/codegraph/server/api.py)'s
`_module_graph` only queried `edges.type = 'imports'`. But a cross-file `calls` edge with
no matching `imports` edge is a real dependency too — exactly what fix #3 above produces
for Java same-package calls (and what a JS default-import call already produces). Querying
directly against LedgerGuard's index: **7 additional real file-to-file relationships**
existed with zero import edge between them — doubling true connectivity, all invisible in
the graph. **Fix:** `_module_graph` now unions `imports` and cross-file `calls` edges per
file pair, labeling an edge `"imports"` when at least one import edge exists between the
pair (preserving prior behavior exactly), else `"calls"`. Verified: LedgerGuard's graph
edge count went from 7 → 14. 1 new end-to-end test in `test_api.py`.

## Checklist completion: hooks, no-key graceful behavior, and all 9 MCP tools (usable without an API key)

Finished the remaining items from the original manual-test checklist against LedgerGuard,
via a real Claude Code session (not synthetic):

- `codegraph hooks install` / commit-trigger / `hooks uninstall` — PASS (see the
  `.gitignore` bug section above, found while testing this).
- `codegraph ask` / `codegraph summarize` with no `ANTHROPIC_API_KEY` set — both PASS,
  clean error message, exit code 1, no crash or traceback.
- All 9 no-API-key-needed MCP tools, exercised live end-to-end: `index_status`,
  `search_code`, `get_context`, `get_entity_context`, `impact_analysis`, `trace_path`,
  `list_files`, `reindex`, `get_unsummarized_entities` + `store_summaries` — all PASS.
  Notably, `impact_analysis`/`trace_path`/`get_entity_context` on `WelfordStats` all
  correctly reflect today's Java same-package + field-initializer fixes end-to-end
  through the MCP layer, not just the CLI. `reindex` correctly picked up a live file
  edit mid-session. `ask_codebase` (the one tool needing a key) not exercised, same
  no-key limitation as `ask`/`summarize`.

One side observation, not confirmed as a bug: the MCP connection dropped and had to
reconnect once, mid-session, between two consecutive user turns. Self-healed
immediately with no data loss. Logged as a watch-item since a stdio-based server
dropping mid-session (as opposed to being slow to start, which was already fixed) is a
different failure mode — worth a closer look if it recurs.

## Bug found and fixed: complexity heuristic silently undercounted every non-Python language

Running the rest of the checklist against LedgerGuard: `codegraph smells` reported **zero**
smells on a real 47-file codebase. Digging into why: `cyclomatic_complexity`'s
decision-keyword regex was `\b(?:if|elif|for|while|and|or|except|case)\b` -- a
word-boundary pattern. `&&`/`||` are symbols, not words, so `\b` can never match them at
all; `catch` (the non-Python exception keyword) wasn't in the list either, only `except`
was. Confirmed live: 9 `&&`/`||` occurrences in LedgerGuard's main backend source, all
silently worth zero toward complexity. This isn't Java-specific -- every C-family language
this tool supports (Java, C/C++, C#, Go, Rust, JS/TS, PHP) has its boolean-operator
branches and `catch` blocks invisible to this heuristic.

**Fix:** [`smells.py`](../packages/codegraph/analysis/smells.py) — `_DECISION_RE` now also
matches `&&`, `||` (as separate non-word alternatives, since `\b` doesn't apply to them),
and `catch`. 2 new unit tests confirming the previously-unmatchable operators/keyword are
now counted correctly. Re-ran `codegraph smells` on LedgerGuard after the fix: still zero
smells, and that's plausible on its own terms (only 9 `&&`/`||` total across the whole
backend, unlikely to concentrate 15+ decision points in any single method) -- the bug was
real and confirmed regardless, this fix just happened not to change this particular
repo's specific result. Matters more for a less disciplined C-family codebase.

Separately, `codegraph` intermittently failed with `ModuleNotFoundError: No module named
'codegraph'`, later `uv trampoline failed to canonicalize script path`, in the user's
terminal specifically (never reproducible from the assistant's own shell). Root cause: an
orphaned `codegraph.server.mcp_server` process from an earlier session held file locks on
the tool's shared interpreter files, so a `uv tool install --force` run while it was still
alive left the install in a partially-replaced, inconsistent state; a later manual
directory deletion (working around a Windows reparse-point removal error) then desynced
`uv`'s own tool registry from the leftover shim file on disk. Fully resolved by: killing
the stray process, deleting the orphaned `~/.local/bin/codegraph.exe` shim directly, and
reinstalling clean. Documented here as an operational gotcha for this dev workflow (running
`uv tool install --force` while any MCP server / `watch` process from *any* session is still
alive against the same global install), not a product defect.

## Bug found and fixed: the MCP server's own startup could block indefinitely on a slow environment

While verifying the fixes above via a real Claude Code session against LedgerGuard, the
`codegraph` MCP server never finished "connecting" at all. Root cause: `_warm_embedding_model`
(added specifically to make the *first* `get_context` call fast, by pre-loading torch/
sentence-transformers in the main thread before serving starts) has no time bound — on this
machine, with active Windows Defender real-time protection scanning a tool venv rewritten
repeatedly by today's reinstall churn, that import took **~10.7s** (vs. ~0.1s on the
long-lived dev venv), pushing total time-to-ready past ~12.5s. That's not a code defect on
its own, but the architectural risk is real regardless of cause: a synchronous, unbounded
warm-up blocks the MCP handshake itself, not just the first tool call, so *any* slow
environment (antivirus, cold disk, first-time model download) can make the whole server look
permanently stuck to an agent that gives up waiting.

**Fix:** [`mcp_server.py`](../packages/codegraph/server/mcp_server.py) — the warm-up now runs
on a dedicated daemon thread with an 8-second wall-clock budget (`_warm_embedding_model_with_timeout`).
The fast/common case (proven: <1s on a normal venv) is unaffected byte-for-byte. On a slow
environment, the server now starts serving at the budget ceiling regardless of how long the
underlying import actually takes, falling back to the model's own already-documented
lazy-load path for the first real call. Chose a plain daemon thread over
`ThreadPoolExecutor` specifically because the latter's non-daemon workers would otherwise
block process exit if still running. Deliberately did *not* touch the "runs in the main
thread, not `anyio.to_thread`" design itself — that's guarding a real, previously-observed
deadlock risk documented in the original code, and redesigning it blind wasn't worth the
risk for what a bounded timeout already fixes. 3 new tests in `test_mcp.py`.

**Verified live, in the exact environment that showed the problem:** the fallback message now
fires at a bounded ~10.6s (2.6s staleness check + 8s budget) instead of the unbounded ~12.5s+.
Confirmed end-to-end in a real Claude Code session immediately after: MCP connected cleanly,
followed the required `index_status` → `get_context` workflow, reported real token savings
(3.5x, 1.5x, 2.9x across 3 calls), and produced a correct, well-cited, non-hallucinated answer
about the codebase's anomaly-detection pipeline.

## Bug found and fixed: `.codegraph/` was never added to `.gitignore`

Testing `codegraph hooks install` end to end (a disposable throwaway repo, not JobHuntPro or
LedgerGuard) surfaced this by accident: `git commit` after `codegraph init` picked up
`.codegraph/graph.duckdb` (a generated binary index) and `CLAUDE.md` with a plain `git add -A`,
because nothing had ever told git to ignore the former. Confirmed general, not specific to the
throwaway repo: neither JobHuntPro's nor LedgerGuard's `.gitignore` mentions `.codegraph`
either — `codegraph` never writes to `.gitignore` at all, anywhere, despite `init` already
doing comparable one-time setup niceties (writing `CLAUDE.md`, wiring the MCP server).

**Fix:** `codegraph init` (`cli.py`) now ensures `.codegraph/` is in the repo's `.gitignore`,
appending to an existing file (without touching its other contents) or creating one if it
doesn't exist. Idempotent — a simple substring check skips it if any `.codegraph` pattern
already exists, so repeated `init` runs (or a user's own existing entry) never stack a
duplicate. Best-effort: a filesystem error here never blocks the rest of setup. 3 new tests
in `test_cli_init.py`.

Separately, while investigating what first looked like a much more serious bug (a post-commit
hook run that appeared to have *wiped* the index down to 0 entities): that was a red herring
caused by the test setup, not codegraph — `echo "..." >> file` in PowerShell defaults to
UTF-16LE, so appending to a UTF-8 `.py` file produced embedded NUL bytes, which the walker's
existing binary-file detection correctly treated as binary and skipped, and the (already-shipped,
this-session's) orphan-cleanup fix correctly purged its now-unseen entities in response.
Confirmed correct behavior given a genuinely corrupted file, not a defect — recorded here so
it doesn't get miscounted as a bug if this report is skimmed later. One small, real,
lower-priority polish item did fall out of that detour though: `index` reports counts for
"skipped as unsupported language" and "skipped as generated/minified" but not "skipped as
binary," so a real file that ends up looking binary (a bad encoding, a partial write) currently
disappears from the index with zero explanation. Not fixed this pass — logged for later.
