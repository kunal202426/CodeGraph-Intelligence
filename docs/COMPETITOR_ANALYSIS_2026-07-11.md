# Competitor analysis: `@colbymchenry/codegraph` v1.4.1 — and what it means for us

**Date:** 2026-07-11
**Source:** `C:\Users\kunal\Downloads\codegraph-main (1)\codegraph-main` (TypeScript/Node,
~4,700-line MCP tool surface, hundreds of referenced production issue numbers — this is a
mature, heavily field-hardened tool, not a prototype). Full source read, not documentation
skimmed.

**Why this matters right now:** we just spent a session finding that our own tool cost more
than not using it in a real A/B test. This tool makes almost the identical claim we do
("fewer tokens, fewer tool calls") and is used widely enough to have 7 real-codebase
published benchmarks. Worth understanding exactly how it achieves what it claims, and where
that differs from what we built.

## The single most important finding: their own README already says what we found empirically

> *"CodeGraph's win on every codebase is precision and speed — fewer tool calls, faster
> answers. It cuts token and dollar cost too, but those savings are **scale-dependent**:
> small and noisy on a modest codebase, and material only once a repo is large and tangled...
> On a 500-file project, adopt CodeGraph for the speed; the cost savings show up when the
> codebase (and the team) gets big."*

Their own published 7-repo benchmark backs this up directly: on Excalidraw (~640 files) and
Tokio (~790 files) — both bigger than LedgerGuard (47 files) — the $ cost result is **"even,"
not cheaper**. Cost only clearly wins on their two largest repos (VS Code ~10k files: 18%
cheaper; Django ~3k: 8% cheaper) plus, notably, their two *smallest* (Gin ~110: 19% cheaper;
Alamofire ~110: 40% cheaper) — so small-repo cost wins aren't impossible, but they're not
guaranteed the way the universal metric (tool-call count) is.

**The metric that's unambiguously true at every single size in their data: 58% fewer tool
calls, 22% faster, file reads cut to ~zero.** Not dollar cost. This should reframe how we
report our own tool's value — [item #2 in the cost-efficiency findings
doc](COST_EFFICIENCY_FINDINGS_2026-07-10.md) already flagged our token-savings metric as
potentially misleading; this is independent, external confirmation from the field-tested
version of the same idea that **tool-call count and latency, not $ cost, are the honest
universal claim** for a small/medium repo. Our 47-file LedgerGuard test showing a net cost
*increase* isn't a damning outlier against this tool's category — it's consistent with what
the most mature tool in the space itself publishes for repos that size.

## What actually produces their result: architecture, not response-size tuning

### 1. One tool, full verbatim source, by default — this is the real lever

`codegraph_explore` is their **entire default MCP surface** (the other 7 tools exist but
aren't even listed to the agent by default — see `server-instructions.ts`). It returns the
**complete verbatim, line-numbered source** of matched symbols grouped by file (capped at
`maxFiles`, adaptively sized 4-8 by repo size), explicitly telling the agent: *"the same
`<n>\t<line>` shape Read gives you... treat the source as already Read; do NOT re-open those
files."*

This is the opposite default from ours. Our `get_context` defaults to a short preview +
summary fields, and our guide (until yesterday) said *"keep get_context in summary mode."*
Their proven mechanism for "file reads cut to ~zero" is precisely the thing our default
actively avoids: **give the agent the full source in the first call so a second round-trip
is never needed for understanding.**

**Important correction to our own thinking:** their "0 file reads" benchmark is specifically
*"answering one architecture question"* — pure Q&A/understanding tasks, not edits. Claude
Code's Edit tool mechanically requires a fresh `Read` on the exact file path before it will
edit it, regardless of what any MCP tool returns — no MCP design bypasses that. So this
finding does **not** contradict yesterday's edit-workflow guide fix (locate with one
`get_context`, then Read+Edit directly) — that stays correct for edits specifically. The gap
this closes is **everything else**: our A/B's Q1-Q3 and Q5 (understanding, search, impact
questions) were exactly the categories where a full-source-by-default response would have
let the agent skip a second round-trip entirely, and those are precisely where our cost gap
was worst.

### 2. Markdown output, not JSON, for the primary tool

`formatContextAsMarkdown` produces a compact prose/list document — entry points as a bulleted
list with inline signatures, related symbols grouped by file as one line each, code blocks
only for key entries. No JSON key-repetition overhead (every JSON object we return repeats
`"entity_id":`, `"type":`, `"start_line":`... per entity; a markdown list amortizes that
structure across the whole response instead of per-item). We did not evaluate this in
yesterday's payload-slimming pass — worth a follow-up: measure a same-content markdown vs.
JSON response side by side.

### 3. Multi-client daemon + local-handshake proxy — solves our "stuck connecting" problem better than our fix did

We hit "MCP server stuck connecting" three separate times this week, with three different
root causes (orphaned processes holding DB locks, an unbounded embedding warm-up, a failed
spawn), and shipped an 8-second timeout cap plus boot breadcrumbs as the fix. Their
architecture solves the *class* of problem, not just bounds it:

- A background **daemon** process is shared across every concurrent session on the machine —
  the embedding-model/index-load cost is paid once per daemon lifetime, not once per Claude
  Code session the way ours is.
- A **local-handshake proxy**: `initialize` and `tools/list` are answered instantly from
  static constants the moment the client asks, before the daemon is even confirmed reachable.
  Real tool *calls* are buffered and forwarded once the daemon connects in the background. The
  agent's tools show as available immediately regardless of backend readiness — our
  "Connecting…" limbo (the exact symptom that blocked testing three times) structurally can't
  happen in their design.
- If the daemon is unreachable or a version mismatch, a **lazily-created in-process engine**
  serves the session directly — never silently running against a stale daemon, never leaving
  a session permanently degraded.
- Extensive hardening for real failure modes we haven't hit yet but plausibly could: a daemon
  dying mid-session and re-serving in-flight requests in-process (their #662), PPID watchdogs
  for a killed parent process on platforms where stdin-close isn't reliable (#277), a
  startup-abandoned backstop (#1185).

**This is high-value and high-effort** — genuine concurrent-systems engineering, not a
guide tweak. Not proposing we build this now. Logging it as the correct long-term direction
for the MCP-connection-reliability problem, since our timeout-cap fix is a bound on the
symptom, not a fix for the underlying "cost paid once per session instead of once per
machine" architecture gap.

### 4. Adaptive sizing by repo size

`defaultMaxFiles` scales 4→8 based on the indexed file count (`getExploreBudget(fileCount)`,
with a dedicated `adaptive-explore-sizing.test.ts`). A tiny repo gets a tighter cap; a large
one gets more room. We use fixed defaults (`limit=5`, `max_tokens=1500`) regardless of repo
size. Low effort, plausible win — worth a follow-up.

## Round 2: deeper pattern mining (2026-07-13)

A follow-up pass went past the MCP-surface files above into the competitor's context-building,
search, and resolution internals (`context/index.ts`, `search/*.ts`, `resolution/*.ts`) and
cross-checked each against our own `resolver.py` / `queries.py` equivalents. Ranked easy-wins
first; implemented the top 6, logged the rest.

### Shipped

1. **tsconfig/jsconfig `paths` alias resolution** (`graph/resolver.py`) — `@/foo` imports used
   to be an explicitly deferred TODO, falling through the bare-specifier branch straight to
   `external:`. Every Next/Nuxt/Vite-scaffolded repo we index was losing cross-file edges for
   every aliased import. Now reads `compilerOptions.paths`/`baseUrl` (JSONC-tolerant: comments,
   trailing commas) and resolves through them before giving up. Ported from
   `resolution/path-aliases.ts`.
2. **Ambiguous-name resolution ceiling** (`graph/resolver.py`) — method-call and inheritance-
   chain resolution now decline same-file disambiguation above 500 same-named candidates,
   mirroring `resolution/name-matcher.ts`'s `DEFAULT_AMBIGUOUS_NAME_CEILING` (their comment
   cites a real 15-28 minute stall on one repo from exactly this). Defensive; no evidence yet
   it's hurting us today, but it's a one-line-per-branch fix against a known failure mode.
3. **Test-file and generated-file down-ranking in search** (`graph/ranking.py`, new module) —
   `UserServiceTest.java` no longer ranks equal to `UserService.java` for a generic query;
   suffix-classified generated files (`.pb.go`, `_pb2.py`, `.g.dart`, ...) rank behind
   hand-written code on a name collision. Ported from `query-utils.ts` / `extraction/
   generated-detection.ts`'s ranking use (distinct from our existing `walker.looks_generated`,
   which is a content-based hard-exclude for minified/bundled files, not a ranking signal).
4. **Identifier segmentation + multi-term co-occurrence re-ranking** (`graph/ranking.py`,
   `graph/queries.py::search_literal`) — names now split into camelCase/snake_case segments
   (`OrderStateMachine` -> order/state/machine) so a query like "state machine" can find a
   compound identifier it previously couldn't see inside; results corroborating 2+ distinct
   query words rank above single-generic-word matches. Single-term queries keep the exact same
   tiering as before. Ported from `context/index.ts` steps 5a-5c + `search/identifier-
   segments.ts`.
5. **Low-confidence match warning** (`server/mcp_server.py::_get_context`) — a multi-word query
   where no single hit corroborates 2+ words now appends a warning instead of presenting weak
   coincidental matches with the same confidence as a strong one. Ported from `context/
   markers.ts`'s low-confidence handoff marker.
6. **Per-file / test-file diversity cap in `get_context`** (`server/mcp_server.py`) — results
   are now over-fetched and capped so one file (~60% of the budget) or test files (~33%) can't
   crowd out the rest of a multi-hit response. Ported from `context/index.ts`'s per-file/
   non-production diversity caps.

`installer/guide.py`'s `detail="full"`-by-default change from round 1 is unaffected by this
pass.

### Not done this pass — logged for later, ranked by leverage

1. **Extraction-version staleness signal** (medium effort) — a monotonic `EXTRACTION_VERSION`
   bumped only when the parser/resolver's output shape improves, surfaced as "re-index
   recommended, richer extraction available." We currently have no way to tell a user who
   upgrades CodeGraph that a full re-index (not just an incremental one) would extract more.
2. **Field-qualified search syntax** (`kind:function name:auth path:...`) (medium effort) —
   `search_code`/`get_context` take only a raw string today; a small tokenizer could let
   structured filters ride alongside free text.
3. **Bounded edit-distance fuzzy fallback** for typo'd symbol names (medium effort) — zero
   results today on a misspelled query; a capped DP edit-distance scan over known names as a
   last resort is cheap even at scale with an early-exit.
4. **tsconfig-alias sibling: JS/TS workspace (monorepo) package resolution** (medium effort) —
   `package.json` `workspaces` / `pnpm-workspace.yaml` member resolution, so a monorepo
   cross-package import doesn't get misclassified as `external:`. Natural pairing with the
   tsconfig-alias fix shipped this pass.
5. **Markdown response format for `get_context`** (medium confidence, medium effort) — measure
   a same-content JSON vs. markdown response size before committing.
6. **Reframe our headline efficiency claim to lead with tool-call count / latency, not $ cost**
   (high confidence, low effort) — extends [cost-efficiency findings doc
   item #2](COST_EFFICIENCY_FINDINGS_2026-07-10.md).
7. **Adaptive default sizing by repo file count** (medium confidence, low effort).
8. **Daemon + local-handshake-proxy architecture** (high confidence it works, high effort to
   build correctly — the real fix for "MCP stuck connecting," not a timeout bound).
9. **A single default-listed tool instead of 11** (low confidence without our own test data).
10. **Dynamic-dispatch edge synthesis** (EventEmitter, React setState->render, vtable-override
    bridging) — real and valuable, but a multi-week feature, not a portable pattern.
11. **WAL/checkpoint growth valve for bulk-index write throughput** — DuckDB's checkpoint
    semantics differ from SQLite's; profile a large-repo `codegraph index` run on non-SSD
    storage before assuming this applies to us.
