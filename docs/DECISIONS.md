# Decisions & history

The README covers what you need to use Kortex. This is the "why" — full changelog, roadmap,
and the reasoning behind the cost-savings numbers, moved out of the README so it stays a
quick read instead of a book.

## Cost numbers: what they mean and how they were reached

Kortex's `get_context` reports something like *"~1288 vs ~4519 tokens (3.5x less)"* — an
estimate of *reading/context* tokens (4-chars/token heuristic, baseline = reading the full
files the answer came from). That number is internally correct but doesn't capture your
actual $ cost, which also depends on round-trip count (each tool call re-reads the whole
accumulated conversation from cache) — a session making more, smaller tool calls can pay a
compounding cache-read cost that a session making fewer, larger direct reads doesn't.

A real controlled A/B, measuring actual session `/usage` cost instead of estimated tokens,
first caught this directly: a mandatory extra tool call and a bloated response payload made
Kortex cost **34% more** real money than not using it at all on a 47-file repo (LedgerGuard),
despite the "Nx less" number looking good the whole time. Both were implementation bugs,
fixed the same investigation — a re-measurement landed within ~3% either way (noise, not a
real gap) on that same repo.

**The headline results, current best evidence:**

- On a ~1300-entity, 4-service repo (JobHuntPro): **14% cheaper overall**, winning the two
  harder cross-file questions by a growing margin, losing only the one a well-written README
  already answered.
- On a 97,000-entity repo (Grafana): **58% cheaper** on the deep blast-radius question that
  matters most for real engineering work — after finding and fixing a genuine capability gap
  the same day testing exposed it (`impact_analysis` was call-graph-only, so it had nothing
  to say about "what breaks if I change this struct's shape" until it also learned to search
  for type usages). The full round this question came from landed as a near-tie before that
  fix — logged honestly below, not smoothed over.

$ cost savings are genuinely **scale-dependent** — closer to break-even on a small/medium
repo, a clear win once a codebase (and the session count against it) gets large. That tracks
the same shape the field's most mature comparable tool reports in its own published numbers,
and is no longer just a claim borrowed from someone else's benchmark.

**Every round, including the ones that didn't go well, is logged in full in
[COST_EFFICIENCY_FINDINGS_2026-07-10.md](COST_EFFICIENCY_FINDINGS_2026-07-10.md)** — the
34%-worse first result, the round that closed the gap, the isolated `project_brief` test, the
JobHuntPro win, and the full Grafana story (near-tie → gap found → fixed → 58% win on
re-test). Nothing swept under the rug.

## Changelog

**Aug 2026**

- **Fixed the real reason Kortex sometimes "wasn't being used" on a big repo — three
  compounding bugs, none of them in the agent.** For weeks the symptom was that an agent
  given an *editing* task on Grafana went straight to grep, ignoring the MCP tools; two
  rounds of guide-wording changes did nothing, because the tools were never callable.
  Measuring MCP boot directly found why:
  - Boot took **25.6s cold** against Claude Code's **30s connect timeout** — `main()` loaded
    sentence-transformers/torch synchronously before it could serve. Warm cache connected;
    cold cache got dropped *silently*, so the agent had no tools and no error to report.
    Embeddings now run in a **worker subprocess** spawned at boot and never waited on. That
    also makes the old main-thread deadlock structurally impossible: torch is never imported
    in the server process at all.
  - `get_context` ran a **full repo walk synchronously, twice** (stale count + stale paths)
    on a cold cache, while the boot thread ran a third copy — **225 seconds** on 16k files.
    Staleness is now single-flight, shared between both caches from one walk, and bounded by
    a 1s first-call budget: small repos keep the immediate warning, big ones answer now and
    warn on the next call.
  - Those three concurrent walks **crashed the interpreter outright** (`Fatal Python error:
    PyEval_SaveThread: the function must be called with the GIL held`), killing the server
    mid-session — which a client surfaces only as a dropped connection.
  - The walk itself was slow because it opened and read 8 KiB of *every* source file to
    sniff for binaries. Staleness only needs paths and mtimes, and an already-indexed file
    was proven non-binary when it was indexed, so the sniff is now skipped for known files
    and kept only for genuinely new ones.

  Measured on Grafana after: **boot 25.6s → 0.3s**, **staleness walk 225s → 6.2s**, first
  `get_context` **19.9s**, warm calls **0.5s**, clean exit. Every earlier "codegraph wasn't
  invoked for editing tasks" data point is void — that A/B needs re-running from scratch.
- **Same day, found live while testing the fix above:** `reindex` hit the identical deadlock
  through a door the worker-subprocess rewrite didn't cover. `index_one_file` (which
  `reindex` calls) imports and calls the embedding pipeline **in-process** — right for the
  CLI and `codegraph watch` (no event loop, nothing to deadlock), wrong for the MCP server's
  `reindex` handler, which runs on an anyio worker thread while the server's own asyncio
  loop runs on the main thread. First-ever torch import there, off the main thread, mid-loop
  — exactly the hazard the worker subprocess exists to remove, just missed at this call site.
  Caught live: the actual server process behind this session sat hung on a real `reindex`
  call for 45+ minutes (9.6s CPU used the whole time, still "Responding", DB file still
  locked) after Claude Code's client gave up waiting at its own 1800s idle timeout. Fixed by
  giving `index_one_file`/`_embed_file` an injectable `embed_fn` — CLI/watch keep the fast
  in-process default, `reindex` now passes the worker-subprocess client. Verified on an
  isolated repo: a real reindex of a genuinely changed file completed in 12.7s, no hang.
  **A server already running the old code can't self-heal from this — it's holding the
  deadlock in memory. Needs a Claude Code restart, not just a fresh git pull.**
- Real, controlled A/B on an even bigger repo — Grafana (97,239 entities, 16,046 files). The
  round came back a near-tie (-2.8%) until digging into *why* one question lost surfaced a real
  gap: `impact_analysis` only walked the call graph, so a struct/interface (no callers, only
  field/type references) always reported `total: 0` — correct given what it tracked, but easy
  to misread as "safe to change." Fixed the same day: `impact_analysis` now searches for type
  usages when the resolved entity has no callers at all. Re-testing the exact question that
  exposed the gap: **58% cheaper** with codegraph, comparable answer quality both sides.
  [Full writeup →](COST_EFFICIENCY_FINDINGS_2026-07-10.md)
- Fixed a genuine deadlock: loading the embedding model off the main thread while the MCP
  server's own event loop was running could freeze the whole process indefinitely — confirmed
  by direct reproduction (~31s completing synchronously vs. hanging 280s+ with zero progress
  dispatched through the real request-handling path). Now always loads synchronously before
  the event loop starts; the server no longer blocks its own handshake on a large repo's
  staleness scan either (backgrounded), so connecting doesn't time out just because the repo
  is big.
- `impact_analysis` and `trace_path` now accept a plain-text query instead of requiring a
  pre-resolved `entity_id` on both tools — collapses a `search_code` round-trip into the same
  call for the exact "what breaks" / "how does A reach B" questions that cost the most on a
  large, unfamiliar repo.
- `codegraph index` shows real progress — file-scan count, parse percentage, embedding ETA —
  instead of going silent for minutes on a large repo. Previously invisible outside a real
  terminal: a piped or logged run looked completely frozen even while it was actively working.
- `codegraph uninstall --purge` also removes `.codegraph/` and any installed git hooks, not
  just the MCP registration and guide — the cleanup a real test pass on someone else's repo
  actually needs, verified to never touch a foreign `CLAUDE.md` that predates codegraph.
- Real, controlled A/B on a genuinely large repo — JobHuntPro (1321 entities, 187 files, 4
  sub-apps, cross-language: JS Chrome extension + Node/Express + Python/FastAPI + React) —
  instead of the 47-file repo every earlier cost measurement used: **codegraph won overall,
  -14% $ cost**. It lost only the one question a well-written README already answered, and
  won both harder cross-file questions by a growing margin. First real evidence for the
  scale-dependence claim this project has cited since its own competitor research, not an
  assumption borrowed from someone else's numbers.
  [Full writeup →](COST_EFFICIENCY_FINDINGS_2026-07-10.md)
- MCP server now watches its own parent PID and self-exits if the parent dies, instead of
  relying solely on stdio EOF — the root-cause fix for orphaned server processes holding the
  DB lock, which stdio EOF misses when a parent process tree is killed abruptly.
- `get_context` now accepts a list of up to 5 queries in one call — merged, deduped, fair
  round-robin across queries — instead of one call per name. The first fix aimed at cutting
  round-trip *count*, not just response size, which is what every earlier efficiency pass
  had been optimizing.
- New `project_brief` tool: a cheap, one-call session-start orientation summary (architecture
  layers, hot-path entities by call fan-in, HTTP entry points) — measured as a real, if
  modest, ~7% cost win in its own isolated A/B, with a higher cache-hit rate across the board.
- tsconfig/jsconfig `paths` alias resolution (`@/foo` imports) — previously an explicitly
  deferred TODO in this project's own resolver, silently losing cross-file edges on every
  Next/Nuxt/Vite-scaffolded repo indexed.
- Search re-ranked with identifier segmentation (`OrderStateMachine` now matches "state
  machine"), multi-term co-occurrence boosting, and down-ranking of test/fixture and
  generated files on a name collision — plus a bounded fuzzy-match fallback so a typo'd
  symbol name still finds its target instead of returning nothing.
- Fixed a real staleness bug found via manual testing: a repo with any vendored/minified
  file (extremely common) used to show a permanent "N file changed — re-index recommended"
  that no amount of re-indexing could ever clear, because the file was never given a row to
  begin with.
- `ask_codebase` is now omitted from the advertised tool list entirely when no
  `ANTHROPIC_API_KEY` is set, instead of being advertised-but-broken — measured ~9.7% off
  total tool-schema overhead for the common case.
- 1276 tests passing (up from 1114), zero regressions.

**Jul 2026**

- Battle hardening against real-world failure patterns (each confirmed reproducible before
  fixing): C++ forward declarations no longer indexed as duplicate classes; methods returning
  a reference (`const X& Get() const`) and conversion operators (`operator Type()`) — both
  previously dropped from the index entirely — now indexed correctly; `git blame` bounded by
  a timeout so a wedged git can't hang `codegraph owner`; the watcher quarantines a file
  after 3 consecutive re-index failures instead of retrying forever; generated/minified
  files (any source line over 10k chars) are skipped by both index paths.
- Inheritance-aware method resolution: `obj.method()` now also resolves when `method` is
  declared only on a base class/interface, not `obj`'s own type — a base-class edge per
  class (`extends`/`implements`/`< Base`/`: public Base`, plus Go's embedded-struct method
  promotion) resolved before calls, then a same-file-preferred walk up the chain. Covers the
  6 languages with real inheritance syntax plus Go; Rust has no inheritance concept.
- Receiver-type inference: `obj.method()` now resolves to the exact declared method instead
  of matching on the callee name alone, across all 8 OO-capable languages (Python, TS/JS,
  Java, Go, Rust, PHP, Ruby, C/C++). Infers the receiver's type from a local variable's
  constructor call or annotation, a typed parameter, `self`/`this`, or a tracked
  `self.attr`/`this.attr`/`@attr` — so two unrelated classes sharing a method name no longer
  risk a call edge pointing at the wrong one. Falls back to the old name-only resolution
  whenever the type can't be confidently inferred, so this never makes a result worse.
- 1114 tests passing (up from 1001), zero regressions across the pass — verified on both
  local runs and GitHub Actions (Linux), which caught and led to a fix for a genuine
  cross-platform ordering bug in multi-base inheritance resolution.
- Framework-aware call resolution: a route handler invoked only through Flask, FastAPI,
  Express, Django, Spring, or Rails routing now has a real `calls` edge instead of showing
  up as false-positive dead code with zero callers in `impact_analysis`. Resolves same-file
  and cross-file (the common case — `routes.rb` → a controller file, `urls.py` → `views.py`).
- Cross-language HTTP edges: a TS/JS `fetch()`/`axios.*()` call with a statically-known URL
  now resolves straight through to the backend handler that serves it, across both files and
  languages in one edge.
- `codegraph hooks install` adds an opt-in git-hook fallback (`post-commit`/`post-merge`/
  `post-checkout`) that re-indexes in the background, for environments where filesystem-watch
  events aren't reliable (mounted network drives, some WSL2 `/mnt` paths).
- Agent installer support doubled: 4 → 8 targets, adding Kiro, opencode, Hermes Agent, and
  Antigravity alongside Claude Code, Cursor, Codex, and Gemini.
- 1001 tests passing (up from 778), zero regressions across the pass.
- `get_context` now warns when your index is stale, and tells you how many files changed and
  to run `reindex` before trusting results. Previously you had to call `index_status`
  yourself to find this out, which most agents skip.
- 778 tests, 0 failures. Added 4 tests for the stale warning.
- Ran proper token savings numbers across queries on this repo: **101x average**
  (12x on a tiny single-function file at worst, 190x best case). One example:
  1,108 vs 10,637 tokens. [Bench notes →](QUALITY_REPORT_2026-07-01.md)
- Hit@1 was 7/7 on symbol lookups where the function name doesn't appear in the
  query at all (pure semantic match).
- Warm query latency ~15ms; stale check is <1ms once the TTL cache is warm.
- `reindex` now cleans up entities for files deleted outside of `watch` (a plain `rm`, a
  branch switch), and the staleness cache is keyed on git HEAD so a branch switch doesn't
  hide staleness for up to 5 minutes. Full suite: 892 passing.

## Roadmap

Phases 10-13 ("best of both"), 14-18 ("actually usable"), and the 19-22/24/26-28 competitive
hardening pass are complete:

- **Phase 10**: 9 languages: Go, Rust, Java, Ruby, PHP, C, C++ added to Python + TS/JS; extended to 19 with Kotlin, C#, Scala, Bash, Elixir, R, Julia, Haskell, OCaml; further to 22 with HTML, CSS, SQL
- **Phase 11**: `codegraph watch`: debounced file watcher re-indexes in ~300 ms; staleness guard on `serve`/MCP startup
- **Phase 12**: richer MCP tools + CLI mirrors (`context`, `trace`, `status`)
- **Phase 13**: agent installer for Claude Code, Cursor, Codex, Gemini
- **Phase 14**: *adoption gate*, directive tool descriptions + auto-written `CLAUDE.md` so agents actually use the tools
- **Phase 15**: *value gate*, token-lean `get_context` (summaries + token budget), readable labels; calling it is genuinely cheaper than reading files
- **Phase 16**: multi-project: walk-up DB discovery so one install serves every repo
- **Phase 17**: self-healing: a `reindex` MCP tool the agent can call to refresh a stale index from the chat
- **Phase 18**: first-run legibility (model-download notice), `codegraph init` one-shot, PyPI metadata
- **Phase 19**: precise per-file staleness signal in `get_context`, plus a real DuckDB connection-conflict fix
- **Phase 20-21**: framework-aware call resolution (Flask/FastAPI/Express/Django/Spring/Rails), cross-file route resolution, and cross-language HTTP edges (`fetch`/`axios` → backend handler)
- **Phase 22**: git-hook fallback (`codegraph hooks install`) for environments where filesystem watching isn't reliable
- **Phase 24**: agent installer breadth doubled, 4 → 8 targets (added Kiro, opencode, Hermes Agent, Antigravity)
- **Phase 26**: receiver-type inference — `obj.method()` resolves to the exact declared method (not just callee name) across all 8 OO-capable languages
- **Phase 27**: inheritance-aware method resolution — walks base classes/interfaces (or Go's embedded-struct promotion) when a method isn't declared on the receiver's own type
- **Phase 28**: battle hardening — C++ forward-decl/reference-return/conversion-operator index corruption fixed, git-blame timeout, watcher failure quarantine, generated/minified file skip

Deliberately **deferred**: deep TypeScript type resolution via `tsc`, Ruby `include`-mixin
inheritance and Rust trait default methods (receiver-type inference through inheritance
covers the rest), and a shared multi-client MCP daemon (one process per agent window today,
scoped and explicitly not built — a process-model change with more risk than the wins above).
See [STATUS.md](../STATUS.md).
