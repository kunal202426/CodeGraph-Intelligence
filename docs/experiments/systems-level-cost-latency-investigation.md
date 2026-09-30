# Systems-level cost/latency investigation: Claude Code + CodeGraph execution loop

Working document, not committed (per CLAUDE.md rule 10: plans/specs/design notes live here).
Scope: reduce total frontier-model work (tokens, turns, latency) for the whole
Claude/Codex + MCP + CodeGraph + native-tools loop, without reducing task quality.
Laya is evaluated as one candidate mechanism among several, not the goal.

## 0. What's real vs. proposed vs. requires a live session

This document mixes three kinds of claims. Marked throughout:
- **[MEASURED]**, I ran it and am reporting the actual number.
- **[FROM LOG]**, already established in `docs/COST_EFFICIENCY_FINDINGS_2026-07-10.md`,
  cited not re-derived.
- **[PROPOSED, unmeasured]**, an architecture/design idea with a rationale, no A/B yet.

Anything requiring real Claude Code session cost/quality (actual `/usage` deltas, task
success rate on a real edit) is **[PROPOSED, unmeasured]** and flagged as needing the
live protocol in `docs/MANUAL_TEST_PROMPT.md`. I did not fabricate any session-level
dollar or quality numbers in this document.

## 1. Cost model, what's already established [FROM LOG]

Don't re-derive, the log already isolated this correctly:

1. **Cache reads, not response size, dominate.** Every turn re-reads the entire
   accumulated context. A session with more, smaller tool calls pays a compounding
   cache-read cost that fewer, larger direct reads don't.
2. **Two levers exist: round-trip count, and permanent context growth per response.**
   Response-size fixes (payload slimming -36%, the six unbounded-response caps, the
   512,000-token `list_files` fix) are already largely done and hitting diminishing
   returns.
3. **The proven, unresolved failure mode is additive tool use, not tool cost.** Round 8:
   codegraph calls happened *alongside* ~30 native greps for the same symbols, not
   instead of them. More tool calls of any kind in one session cost more, mechanically.
4. **Cost and correctness can decouple.** Round 11 was the cheapest AND most broken run
   (two real regressions, one where the agent explicitly claimed to have verified and
   was wrong). Any new lever must be checked against this, not just against $ cost.
5. **Item 5 (the log's own open question):** there may be a structural floor on how few
   turns any tool-use pattern can hit if Claude Code's caching re-reads full context
   every turn, unconfirmed, needs Claude Code internals visibility nobody here has.

## 2. Tool-schema token analysis [MEASURED]

Direct from `tool_definitions()` in `packages/codegraph/server/mcp_server.py`, just now:

| Tool | Tokens (~) | Tier |
|---|---|---|
| `get_context` | 456 | Core, "START HERE", used in nearly every session per the log |
| `impact_analysis` | 360 | Core, the "who calls/uses this" answer |
| `trace_path` | 253 | Situational, "how does A reach B" |
| `get_unsummarized_entities` | 291 | Maintenance |
| `store_summaries` | 251 | Maintenance |
| `list_files` | 210 | Core-adjacent, layout/orientation |
| `search_code` | 185 | Core, candidate-list search |
| `project_brief` | 179 | Core, session-start only, called once |
| `get_entity_context` | 157 | Core, single-entity detail |
| `reindex` | 120 | Maintenance |
| `index_status` | 88 | Maintenance, log already says "usually unnecessary" |
| **Total (11 tools)** | **~2,554** | |

**Grew from round 1's ~1,600-token baseline (same 11-tool count) not because tools were
added, but because descriptions got more defensive prose** fixing specific past mistakes
(`get_context`'s 456 tokens alone carry an essay on `tokens_estimated` vs. `$` cost and
qualified-names-vs-ids). Real tension: the guidance that fixed agent behavior costs
static tokens every turn of every session, forever, whether needed that session or not.

**Maintenance tier** (`index_status`, `reindex`, `get_unsummarized_entities`,
`store_summaries`) = 750 tokens, ~29% of the static budget, needed only occasionally
(staleness, index enrichment), the clearest dynamic-exposure candidate.

**Mutual exclusivity:** `search_code` and `get_context` overlap heavily, `get_context`
is described as replacing `search_code` for most cases ("START HERE... replaces 3-4
round-trips"). `search_code`'s only distinct value is a plain candidate list without the
per-hit source/graph cost `get_context` pays (see §4). Worth testing whether `search_code`
is still pulling its weight as a separate tool or should collapse into a `get_context` mode.

**Usage frequency [FROM LOG, not independently re-measured]:** round 8/9/10/11 call logs
show `get_context` used in nearly every round; `impact_analysis`/`trace_path`/`find_callers`
called **zero times** across three consecutive rounds (9-11) despite being the textbook
tool for the question asked. `index_status` was removed from the mandatory guide path in
round 1 specifically because it was always redundant. This is the strongest signal in the
whole log: **tool availability is not the bottleneck, tool selection is.**

## 3. Real profiling: query-count audit of get_context / trace_path / search_code / get_entity_context / impact_analysis [MEASURED]

Per your ask (deliverable 10 / section 10): these four hadn't had the same N+1 audit
`impact_analysis`/`find_callers` got after the 896-sequential-query bug. I built a real
index (CodeGraph's own engine source, `packages/codegraph`: 102 files, 865 entities, 4995
edges, no embeddings, scratch DB, nothing touched in the tracked repo) and wrapped the
live DuckDB connection to count `execute()` calls per tool invocation, in-process, no
synthetic mocks.

| Tool call | DB queries | Wall time (warm) |
|---|---|---|
| `get_context("GraphStore")`, limit=5, summary | 18 | 29ms (2nd call; 1st paid cold-start) |
| `get_context("GraphStore")`, limit=10, summary | 18 (same real hit count) | 29ms |
| `get_context("GraphStore")`, detail=full | **9** | 26ms |
| `search_code("GraphStore")`, limit=10 | 3 | 22ms |
| `get_entity_context(<id>)` | 3 | 70ms |
| `impact_analysis("GraphStore")` | 4 | 98ms |
| `trace_path(<id>, <id>)` | 19 | 134ms |

**Finding 1, `get_context` has the same N+1 shape `impact_analysis` had, just capped
lower.** For every hit (bounded at `limit`, ≤10) it runs up to 4 sequential single-row
queries: entity row, a *second* query for `raw_source` (summary mode only), outbound
`deps`, inbound `callers`. `_caller_counts` in the same file already demonstrates the
right pattern (one batched `IN (...) GROUP BY`, comment literally says "not N+1"). `get_context` doesn't follow it. Worst case at the current cap: ~41 queries in one call.
This fires on the single most-used tool in the whole surface ("START HERE").

**Finding 2, summary mode (the default) does *more* round-trips than full mode, not
fewer.** `_SUMMARY_COLUMNS` explicitly excludes `raw_source` to keep the row lean, then a
second query re-fetches `raw_source` alone, for the same `entity_id`, just to slice the
first N lines for `source_preview`. Full mode already has `raw_source` in its first query
and skips this, hence 9 queries vs. 18. This is a pure, provable waste: one column
missing from a `SELECT`, refetched via a whole extra round-trip, per hit, on the default
path every session uses.

**Finding 3, `trace_path`'s BFS is genuinely unbounded in query count**, one query per
frontier node expanded, no cap analogous to `DEFAULT_CALLER_NODE_CAP`. The 19-query result
above is not a bug in the termination check (verified: `find_shortest_path` checks
`next_id == dst_id` at discovery time, not at dequeue, in `analysis/traversal.py:72`), it
reflects the specific edge I sampled pointing to an `external:`/unresolved target that
`_CALLS_SQL` deliberately filters out, forcing a real broader search. The honest finding
is narrower but still real: **nothing bounds how many nodes a `trace_path` BFS can expand**
before hitting `max_hops`; cheap on a narrow/shallow search, potentially expensive on a
wide one, and it's the one tool of the five with no query-count discipline at all.

**Finding 4, `search_code`, `get_entity_context`, `impact_analysis` are clean.** Flat
query count regardless of hit/caller count, matching the fix already shipped for the
original `impact_analysis` bug. These don't need work.

**[PROPOSED, unmeasured] concrete fix for Finding 1+2:** collapse `get_context`'s per-hit
loop into 3 batched queries total, regardless of hit count, one `entity_id IN (...)`
fetch for rows (with `raw_source` always included, summary mode just doesn't emit it),
one batched `deps` query, one batched `callers` query, same shape `_caller_counts`
already proves works in this file. Zero response-content change, zero token change, pure
latency fix. This is the highest-confidence, lowest-risk item in this whole document:
proven pattern already in the codebase, measured problem, no quality surface at all
(returns identical data).

## 4. Additive vs. substitutive re-analysis [FROM LOG]

Re-mined from existing transcripts in `COST_EFFICIENCY_FINDINGS`, classified per your
taxonomy:

| Instance | Classification | Evidence |
|---|---|---|
| Round 8: 5 codegraph calls + ~30 native greps, same symbols | **ADDITIVE** | Log's own words: "additive, not substitutive... used alongside an equally long grep chain, not instead of it" |
| Round 9: 1 batched `get_context` (3 queries) → 2-3 targeted greps to pin exact function | **SUBSTITUTIVE + COMPLEMENTARY** | The batched call surfaced enough (a test mutator name, a field name) that greps became targeted, not exploratory, cheapest round of all four |
| Rounds 9-11: `impact_analysis`/`trace_path` called 0 times on textbook "who calls this" questions | **REDUNDANT AVOIDANCE** | Tool existed, was appropriate, wasn't chosen, this is the tool-selection failure, not a cost failure |
| Round 3: guide fix removing mandatory `index_status` call | **fixed REDUNDANT → removed** | `get_context` already surfaces staleness in its own `warnings` field |
| Round 2: MCP fetch of full source, then a second native Read of the same file (Edit tool requires a fresh Read) | **REDUNDANT, structural** | Paid for the same source twice; fixed by a guide rule (locate via one `get_context`, then Read+Edit directly, never pull full source over MCP first) |

**The single biggest lever isn't a tool problem, it's a routing/selection problem**, every round where cost went up, the tool that would have answered the question in one
call was available and unused; every round where cost went down came from *fewer, denser*
calls, not from the tools getting individually cheaper. This directly supports your
reframing: the highest-value work is making CodeGraph replace exploration turns, not
making individual CodeGraph turns cheaper (which is now mostly done).

## 5. Native-vs-CodeGraph decision matrix [FROM existing tool descriptions + log]

| Question shape | Right answer | Why |
|---|---|---|
| "Does this file exist / what's in this directory" | Native `list_files`-equivalent or CodeGraph `list_files` | Tie at small scale; CodeGraph wins once a directory has an agent-written summary cached |
| "Where is X defined" (single, known name) | Either; near-parity, log's Q1 finding | Grep is nearly free on a repo grep already handles well (e.g. Grafana's own strong docs, round 7) |
| "Who calls/uses X" (structural, transitive) | **CodeGraph `impact_analysis`** | One call resolves the full transitive answer; grep chains scale badly, and round 7 showed a 58% cost win here specifically |
| "How does A reach B" | **CodeGraph `trace_path`** | BFS over resolved edges vs. manual chain-following |
| Simple, isolated file edit, well-understood already | **Native Read + Edit directly** | Round 2's fixed bug: don't pull source over MCP then Read again |
| Broad "how does this subsystem work" | CodeGraph `get_context`/`project_brief`, unless the repo has strong existing docs (round 7's Grafana caveat) | |
| Type/interface impact (not a function) | **CodeGraph `impact_analysis`** (fixed round 7) | Was blind to this shape until the kind-check fix; now handles it |

The matrix itself isn't new, it's implicit in the tool descriptions already. What's
missing is a mechanism that makes the agent *actually follow it*, which is §7 below.

## 6. MCP architecture options [PROPOSED, unmeasured, technical analysis]

Current shape: `Claude → MCP (stdio) → CodeGraph process → DuckDB → MCP → Claude`.

| Option | Latency impact | Complexity | Notes |
|---|---|---|---|
| **A. Current (MCP + long-running server process)** | Already has the IPC hop, but the process is already fixed to stay warm (torch import backgrounded, staleness non-blocking per `docs/ONBOARDING.md`) | None, already exists | Baseline |
| **B. Persistent local daemon (already effectively this)** | No change | None | The MCP server *is* already a persistent daemon per session; this isn't a new option, it's what exists |
| **C. In-process library (no MCP, no subprocess)** | Removes the IPC hop entirely | High, breaks the "any MCP client can connect" model, Claude Code specifically expects MCP | Not viable without abandoning MCP compatibility; contradicts your own constraint (§13 of your brief: don't propose hacks that violate platform assumptions) |
| **D. Local Unix/named pipe transport instead of stdio** | Marginal, stdio is already local IPC, a named pipe wouldn't meaningfully beat it | Low payoff for the complexity | Not worth pursuing; stdio isn't the bottleneck, query count is (per §3) |
| **E. Batched RPC (multiple logical operations, one MCP round-trip)** | **Real reduction**, this is what `get_context`'s multi-query batching and `project_brief` already do, and what §7's smart-context tool would extend | Low-medium, extends an existing pattern | Highest-leverage option, already validated (round 9's win) |
| **F. Hybrid: MCP + persistent server-side cache of recent query results** | Could remove re-computation, not re-transmission (Claude Code still re-reads the response from its own context every turn regardless) | Medium | Addresses local CPU/RAM, not frontier token cost, a distinct, smaller win (see §9's context-reuse discussion) |

**Conclusion: the IPC transport itself is not the bottleneck.** stdio-based MCP is already
local and fast (the profiling above shows warm per-call latency in tens of milliseconds,
dominated by query count, not transport). The real lever is E (batched RPC / fewer logical
operations per call), not a transport change.

## 7. Design proposals

### 7a. Dynamic tool exposure [PROPOSED, unmeasured]

`tool_definitions()` already implements this pattern for one tool (`ask_codebase` vanishes
without an API key, re-evaluated on every `list_tools()` call). Extend it:

- **Static profile, simplest version:** hide the 4 maintenance tools (§2, 750 tokens)
  behind a signal already available server-side, e.g. only list `reindex`/`index_status`/
  `get_unsummarized_entities`/`store_summaries` once `_get_stale_count() > 0` or once
  `project_brief` has been called and reports low summary coverage. Removes ~29% of the
  static budget for the common case (a session that never touches staleness/enrichment).
- Do NOT collapse to a single meta-tool (`codegraph({operation: ...})`) as a first move. That trades static schema tokens for a larger per-call parameter surface and loses the
  per-tool descriptions the model currently uses to decide when to call at all; the log's
  own finding (§4) is that selection, not schema size, is the live problem, and a meta-tool
  makes selection *harder* (one giant tool with an internal `operation` enum to reason
  about) not easier. Rank this option (your "C") lowest of the four in your brief.
- Two-stage discovery (your "D": `codegraph_discover` → then the specific tool) adds a
  round-trip for every session that already knows what it wants, actively regressive for
  the common case per §1's round-trip-count finding. Reject unless static+dynamic (A+B)
  prove insufficient.

**Recommended shape: your "E" (hybrid)**, keep `get_context`, `search_code`,
`impact_analysis`, `project_brief` always visible (the four the log shows are actually
used); dynamically hide the 4 maintenance tools. Smallest change, matches proven usage
data, no new selection burden.

### 7b. Smart/batched context tool (`get_task_context`) [PROPOSED, unmeasured]

Directly extends the mechanism that already won (round 9's batching). Task-shaped bundles,
per your spec:

| Task mode | Bundle |
|---|---|
| `bugfix` | implementation + direct callers + tests exercising it (the exact gap round 9's log flagged as unanswered: "what existing test fixtures would violate a constraint I'm about to tighten") |
| `refactor` | target symbol + callers + dependencies + interfaces |
| `feature` | architecture brief + analogous implementation + integration points |
| `test_failure` | failing test + implementation + dependency neighborhood |

**Important caveat your brief already anticipated:** do not blindly bundle everything. A bundle that's wrong for the task shape just becomes new unused context weight, the same
compounding-cost problem as an oversized single response. This needs task-mode
classification as an input, which is exactly the routing decision from the Laya
discussion (§8), a `mode` parameter the agent supplies, or a cheap local classifier if
the agent won't reliably supply it.

**This is the one item in this whole document that most directly targets item 5's
turn-count-floor hypothesis**, if there is a hard floor on turns, the only way past it is
fewer *logical* operations per call, and this is that.

### 7c. Minimum sufficient context [PROPOSED, unmeasured]

Already substantially implemented, `get_context`'s summary/full split, the
`source_preview` truncation, `_neighbor_label` (qualified names instead of full ids in
summary mode), and the diversity cap are all this principle already in production. What's
NOT yet done: no per-task-type policy for *which representation* is sufficient. A
`bugfix` task on a well-named, short function may need full source; a `refactor` impact
question may need only the call graph, never source. Currently `detail` is a manual
agent choice with no task-awareness. Folds into 7b: task mode should also select
representation depth, not just which entities to include.

### 7d. Context reuse / active project state [PROPOSED, unmeasured, higher effort]

Your example (auth found in turn 1, still needed in turn 4) is real but **already
partially mitigated by the existing mechanism**: MCP tool results "stay in context for
the rest of the session" per Claude Code's own documented caching behavior, so within
one session, re-discovery literally cannot happen for anything already returned, it's
already there. The actual gap is **cross-session**: a fresh session re-discovers
everything a prior session already found. `store_summaries`'s cached NL tier is the
existing answer to this at the file/directory granularity; nothing today caches
symbol-level "recently resolved" state across sessions. Building a persistent
`active_project_state` (recently resolved symbols, git state, context hashes) is a real
idea but higher effort and lower confidence than 7a/7b, recommend deferring until 7a/7b
are measured, since they're cheaper and target the same proven problem (round-trip count)
more directly.

### 7e. Context hashing / dedup [PROPOSED, unmeasured]

Legitimate question, but note: within a single Claude Code session, exact-duplicate tool
responses can't happen from CodeGraph's side without CodeGraph being asked the identical
query twice, and if the agent does that, deduping the *response* doesn't save anything,
Claude Code still re-reads its own accumulated context every turn regardless of whether
CodeGraph resent the bytes or not. Hashing has real value for one thing: detecting when
a *cached* NL summary (`store_summaries`) is stale relative to current content, which
`content_hash` matching already does (per `docs/PROJECT_CONTEXT.md`). No further application-level
dedup layer is evidently needed beyond what exists, absent a concrete counter-example.

### 7f. Decision-before-tool-call layer, Laya revisited [PROPOSED, unmeasured, see §8]

## 8. Laya, under "must earn its place"

Per your framing: justified only if `Laya inference cost + complexity < work it prevents`.

**Where it could plausibly apply, restated narrowly:** the routing decision "should this
query go to CodeGraph or to grep" (round 8's actual failure), evaluated *before* the agent
commits to a tool choice. This is a small-label-set (`codegraph` / `grep` / `both`),
evidence-already-local (query text, whether the symbol resolves in the index) decision, Laya's stated sweet spot.

**Why I'm not recommending building this yet:**
1. §3's `get_context` batching fix and §7a's dynamic tool exposure are proven-pattern,
   zero-quality-risk, and directly measurable this week. Laya adds a new local-inference
   dependency (even at 30ms, it's new infrastructure, new failure modes, new calibration
   burden per the earlier research: Laya's confidence-to-accuracy curve is documented as
   non-monotonic, meaning thresholds need calibration on CodeGraph's own traffic, not
   borrowed ones) for a problem that a much cheaper mechanism might already fix: **a
   deterministic rule** ("if the query is a known indexed identifier, prefer
   `get_context`; if it's a prose question grep can't answer at all, same") costs a
   dictionary lookup, not a model.
2. The actual round-8 failure wasn't "the agent didn't know which tool was better," it
   was "the tool descriptions/guide didn't make the preference sharp enough and the agent
   hedged by doing both." That's a prompt/guide problem first. Cheapest experiment: sharpen
   the guide/description to explicitly say "if you're about to grep for the second related
   symbol, stop, you have `impact_analysis`" (the log's own already-identified, not-yet-shipped
   fix idea) and re-measure, *before* reaching for a model-based router.
3. **Laya only earns its place if the deterministic version measurably fails**, i.e., if
   after a sharper guide, the agent still hedges on genuinely ambiguous queries (not just
   ones the guide failed to cover). That's a real, testable follow-up, not a default.

**Recommendation: rank order for §16's architecture candidates is A (optimize existing,
no Laya) → B (dynamic tool exposure) → C (smart context) → D-with-deterministic-rules-only
→ D-with-Laya only if the deterministic version is measured and found insufficient.**

## 9. Cost-efficiency frontier / architecture comparison

Cannot honestly plot real quality/cost points for untested architectures (would be
fabrication). What can be stated with the evidence in hand:

| Architecture | Cost lever hit | Quality risk | Confidence | Effort |
|---|---|---|---|---|
| A. `get_context` batched-query fix (§3) | Latency only, not $ tokens directly, but real wall-clock | None, identical output | High | Low |
| B. Dynamic tool exposure (§7a) | Static schema tokens (~750 of 2,554) + selection reasoning | Low, hidden tools reappear on signal | Medium | Low |
| C. Smart/batched context tool (§7b) | Round-trip count, the proven biggest lever (§1, §4) | Medium, wrong task-mode bundle wastes context; needs task classification to get right | Medium | Medium |
| D. Deterministic codegraph-vs-grep routing (§8) | Round-trip count via fixing round 8's additive pattern | Low if rules are conservative | Medium | Low |
| D'. Same + Laya | Same, marginally better on ambiguous cases per general Laya evidence, no CodeGraph-specific evidence yet | Unknown, needs calibration | Low (unmeasured on this domain) | Medium-high |
| E. Context/state engine (§7d) | Cross-session round-trips only | Low | Low-medium | High |

**Pareto-sensible order to actually run: A → B → D → C → (D' only if D measurably falls
short) → E.** This matches confidence × leverage × effort, cheapest and safest first.

## 10. Benchmark plan and smallest experiment per hypothesis

Every item below needs the live A/B protocol already established in this repo
(`docs/MANUAL_TEST_PROMPT.md`'s discipline: one variable at a time, real `/usage` numbers,
report negative results plainly). I can't fabricate these, they need real sessions.

| Hypothesis | Smallest experiment | Metric |
|---|---|---|
| §3's `get_context` batching fix reduces latency without changing output | Before/after timing on the same profiling script used above, on a larger real index | Wall-clock ms per call, byte-identical response diff |
| §7a's dynamic tool exposure reduces static cost without hurting selection | A/B: 11 tools visible vs. 7 (maintenance hidden) on the same editing task | Static token count, tool-call count, task success |
| §8's sharper guide fixes round 8's additive pattern | Re-run round 8's exact prompt on Grafana with only the guide changed | codegraph calls vs. native grep calls, total cost, correctness |
| §7b's smart context tool reduces turns | One task per mode (bugfix/refactor/feature/test_failure), `get_task_context` vs. current multi-call pattern | Total round-trips, total cost, task success |
| Laya routing beats deterministic rules | Only after the above; needs labeled query→correct-tool pairs from real sessions to calibrate confidence thresholds (per the general research: thresholds don't transfer across domains) | Routing accuracy, cost delta, false-additive rate |

**Recommended single next experiment (highest value, lowest risk, already proven pattern):
implement §3's `get_context` batched-query fix, re-run the profiling script above on a
larger real index (e.g. this repo's full `packages/` tree, or re-clone one of the
previously-used external test repos) to confirm the query count drops from ~4N+1 to ~3
regardless of hit count, with a byte-identical response. Zero quality risk, directly
answers deliverable 10, and is the one item in this whole document that needs no live
Claude Code session to validate, a local, scriptable, repeatable measurement.**

## 11. Answer to the final research question

*What is the minimum local work that lets Claude Code/Codex do the same task with
substantially fewer tokens/calls/turns/latency, without reducing correctness?*

Based on evidence gathered so far: **not Laya, not yet.** The two proven, unresolved
problems are (1) a real, measured N+1 query pattern in the most-used tool
(`get_context`, §3, fixable this week with a pattern already proven elsewhere in the same
file), and (2) a tool-selection/routing failure that's been additive not substitutive
(§4, §8) since round 8, for which the cheapest untried fix is sharper tool descriptions
and a deterministic routing rule, not a new model. Laya becomes worth measuring only
after those two are shipped and re-measured, and only against the specific gap they leave
open.
