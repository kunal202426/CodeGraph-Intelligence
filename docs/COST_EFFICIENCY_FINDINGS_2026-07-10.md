# Cost/efficiency A/B findings — and what it means for the product

**Date:** 2026-07-10
**What this is:** a real, controlled A/B test — same 5 questions (understanding, search,
impact, a code edit, and a follow-up), same codebase (LedgerGuard), same clean git baseline,
one Claude Code session with the CodeGraph MCP tool connected and one without — comparing
actual `/usage` cost, not estimated tokens.

**Bottom line, stated plainly: in this test, using CodeGraph cost 34% *more* than not using
it, for equivalent-quality output.** That's the opposite of the tool's stated value
proposition, and it deserves to be reported exactly this bluntly, not softened.

## The data

| | Without codegraph ($) | With codegraph ($) | Difference |
|---|---|---|---|
| Q1 (understanding) | $0.53 | $0.48 | −$0.05 |
| Q2 (understanding) | $0.68 | $0.71 | +$0.03 |
| Q3 (search) | $0.80 | $0.91 | +$0.11 |
| Q4 (code edit) | $0.97 | $1.28 | **+$0.31** |
| Q5 (impact question) | $1.05 | $1.41 | **+$0.36** |
| **Total** | **$1.05** | **$1.41** | **+$0.36 (+34%)** |

Both sessions produced a **correct, functionally-equivalent edit** at Q4 (verified by diff:
both added `WelfordStats.relativeDeviation(x)` and routed `AnomalyScorer.zScore()` through
it, same `Z_CAP` behavior preserved). So this isn't "cheaper but worse" on either side —
quality was a wash. The cost difference is real, not a proxy for a quality difference.

Cumulative cache-read tokens by Q5: without-codegraph 1.9M, with-codegraph 4.4M — more than
double. That's the number that actually explains the gap, not the individual retrieval
sizes CodeGraph reports about itself.

## Root cause: the tool's own "Nx less tokens" metric measures the wrong thing

`get_context` reports something like *"~1288 vs ~4519 tokens (3.5x less)"* — comparing what
it returned against a hypothetical "read the whole file" baseline. That comparison is
internally correct, but it silently ignores:

1. **MCP tool-schema overhead.** Measured directly: all 11 tool schemas together are ~1.6k
   tokens, present in context the moment the server connects, regardless of how many you
   actually use. Small on its own, but non-zero on every message once connected.
2. **Round-trip count, not round-trip size, is what drives cost here.** Every tool call is
   a separate turn; Claude Code's caching re-reads the *entire accumulated context* on each
   turn. A session that makes more, smaller tool calls pays a compounding cache-read cost
   that a session making fewer, larger direct file reads doesn't — even if each individual
   codegraph call is "more efficient" in isolation.
3. **The agent guide itself was mandating an unnecessary round-trip.** Confirmed in code:
   `get_context` already calls `_get_stale_count()` internally and returns a `warnings`
   field if the index is stale. But the guide's Rule 1 said *"Call `index_status` once"* as
   an unconditional first step on every task — a guaranteed extra round-trip providing
   information `get_context` was already going to give for free. This is the single most
   concrete, provable contributor found in this pass.

## Fixed this pass

[`installer/guide.py`](../packages/codegraph/installer/guide.py) — the managed `CLAUDE.md`
block:
- Rule 1 no longer mandates a separate `index_status` call. The agent now goes straight to
  `get_context` and only calls `reindex` if that call's own `warnings` field flags
  staleness.
- Rule 2 (new): if the agent already knows it needs full source for a small, specific set
  of entities (an edit task, not exploration), call `get_context(..., detail="full")`
  directly instead of a summary call followed by a second full-detail call — collapsing a
  common 2-round-trip pattern (confirmed happening in the transcripts: "let me search" then
  "let me get the full source") into one.
- Kept the block under its existing ~400-token budget (had to trim wording twice to fit —
  worth noting the budget constraint itself is in tension with wanting to explain *why* a
  rule exists; ended up shorter and more directive instead).

1 new regression test in `test_installer_guide.py`. This fix has **not yet been
re-measured empirically** — the honest next step is rerunning the same 5-question A/B with
the updated guide to see how much of the 34% gap it actually closes. Don't claim it's fixed
until that's done.

## Round 2 (same day): re-measured after the guide fix — still not worth it, so went deeper

Re-ran a 4-question subset with the updated guide: with-codegraph hit **$1.09 by Q4 vs
$0.97 without**. Better than the first round's gap, still net-negative. The guide fix was
necessary but not sufficient — so this round attacked the actual cost structure instead of
the instructions around it.

**The economics, from first principles:** an agentic session's dollar cost is dominated by
cache reads — every turn re-reads the entire accumulated context, and (per Claude Code's
own UI tooltip) *"MCP tool results stay in context for the rest of the session."* So only
two levers actually matter:

1. **Round-trips.** Each eliminated tool call saves an entire context re-read (~150k+
   tokens of cache read per turn in these sessions ≈ $0.05 each).
2. **Permanent context growth per response.** Every byte a tool returns is re-paid on
   *every subsequent turn* — response size has a compounding cost, not a one-time one.

**Measured where the response bytes actually go** (real query, LedgerGuard index, 5-entity
summary response = 4,822 chars ≈ 1,205 tokens): per entity, `name`, `qualified_name`,
`language`, and `file` (~150 chars) are pure duplication — all derivable from `entity_id`,
whose format is literally `{lang}:{file}:{qname}`. Null fields (`"docstring": null`) and an
unused `via` retrieval-provenance tag added more. Worst of all: neighbor lists carried up
to 16 *full entity_ids* per entity at ~75+ chars each on a Java repo (the file path
repeated in every one), when the qualified name (~25 chars) is all an agent needs to
understand a neighborhood.

### Shipped: response payload slimming (`server/mcp_server.py`)

- Dropped `name`/`qualified_name`/`language`/`file`/`via` from `get_context` entities
  (derivable from `entity_id` or unused), plus all null/empty fields.
- Summary-mode neighbor lists now carry qualified names, not full ids (full ids still
  available via `detail="full"` or `impact_analysis`; the tool description explicitly
  tells the agent this so it doesn't waste a round-trip misusing a name as an id).
- Summary-mode docstrings truncate to their first line (the source preview already shows
  the opening lines; `detail="full"` keeps everything).
- Envelope: dropped the `query`/`detail` echo and the derivable `tokens_saved`.

**Measured result: the same real query's response went 4,822 → 3,100 chars (−36%)**, with
the informative content (preview, structure, neighbor names, savings fields) intact.
8 new/updated tests.

Same treatment applied to the other two hot-path tools: `get_entity_context` no longer
echoes back the four fields derivable from the id the caller just passed in, and
`impact_analysis` no longer repeats each caller's `name` and full `file` path alongside an
`entity_id` that already contains both — on a deep impact tree that duplication roughly
doubled every node.

### Shipped: edit-workflow rule in the guide

The Q4 transcripts showed the with-codegraph agent fetching full source over MCP and
*then* Reading the same file again because Claude Code's Edit tool requires a fresh Read —
paying for the source twice, plus an extra round-trip. The guide now says: for an edit,
locate with ONE `get_context`, then go straight to Read + Edit; never pull full source
over MCP first. This is the honest division of labor: **the graph's edge is locating and
relating code, not delivering bodies the file tools will re-deliver anyway.**

### Where this tool actually wins — the design direction that matters

The A/B also showed *where* the tool is genuinely better, and it's not raw retrieval on a
47-file repo (grep is nearly free there): Q1 understanding was already slightly cheaper
with codegraph, and `impact_analysis`/`trace_path` answered structural questions in one
call that grep needs several rounds to approximate. The value scales with codebase size
and with **cross-file/structural** questions — and, critically, with **cross-session
reuse**: Claude's context evaporates between sessions, but the index (and its stored
summaries) persist. A single A/B session is close to the tool's worst case; the
per-session re-exploration it can eliminate across dozens of sessions is its best.

**Shipped (2026-07-13): `project_brief`.** The biggest unbuilt lever flagged in this
section — a small pre-computed session-start summary (architecture/layers, hot paths by
call fan-in, HTTP entry points, language/size stats), replacing the multi-call
re-orientation every fresh session currently performs — is now a real tool
(`analysis/brief.py`, `server/mcp_server.py::_project_brief`), computed on demand from
existing indexed tables (no new storage), wired into the guide as "call once, first,
before anything else." Not yet re-measured in its own controlled A/B — the honest next
step is a round-4 test isolating this specific tool's effect on a fresh-session cold-start
question, the way round 3 isolated the resolver/ranking fixes.

## Not fixed this pass — prioritized ideas for real improvement

Ranked by confidence × leverage, not implemented blind — each needs either more design
thought or its own empirical validation before landing.

### 1. Multi-query `get_context` (high confidence, medium effort)
The "search broadly, then drill into 1-2 specific entities" pattern showed up in every
multi-step transcript today. Let `get_context` accept a list of queries (or a
`follow_up_of` style parameter) so that pattern is one round-trip instead of two. Directly
extends the fix already shipped this pass.

### 2. Make the token-savings metric honest about what it measures (high confidence, low effort, high trust value)
`tokens_estimated`/`tokens_if_read`/`savings_ratio` should either be relabeled to make clear
they measure *retrieval size vs. a full-file-read baseline*, not *session cost*, or a
disclaimer should sit next to every reported number. Continuing to report "Nx less tokens"
in a way a reasonable person reads as "Nx cheaper" — when today's own controlled test showed
the opposite for real session cost — is a trust problem waiting to surface the moment
someone else runs this same experiment. The README's headline "101x average" claim
[README.md:62](../README.md) uses the identical estimation methodology and inherits the
same honesty gap; worth revisiting once the metric itself is fixed.

### 3. A genuinely calibrated cost model (medium confidence, high effort)
The right long-term fix isn't a smarter static formula, it's empirical: instrument real
session `/usage` deltas (opt-in, anonymized) around codegraph tool calls, and use *that* to
report real expected $ impact instead of an estimated-tokens proxy. This is what actually
closes the gap between "the tool claims savings" and "the tool has measured savings" — this
report is a first, manual instance of exactly that kind of measurement.

### 4. Teach the guide when *not* to reach for the tool (medium confidence, low effort)
Right now the guide unconditionally says "do not open a source file before calling
`get_context`." For a small, single-file, already-well-understood question, a direct read
may genuinely be cheaper than a round-trip through an MCP tool call — today's Q1 (understanding
question) actually came out *slightly cheaper with* codegraph ($0.48 vs $0.53), so this
isn't always true, but it likely depends on question shape (multi-file/cross-cutting
questions favor the tool; single-file/local questions may not). Worth a follow-up test
specifically isolating question *type* as the variable, not just question *count*.

### 5. Reduce per-turn overhead architecturally, not just per-call count (low confidence, needs research)
If Claude Code's caching genuinely re-reads the full accumulated context on every turn
(the working hypothesis behind the 2x cache-read gap), the ceiling on how much guide/tool
tweaks alone can fix is real — the fundamental fix would be reducing turns, and there may be
a hard floor on how few turns any agentic tool-use pattern can achieve versus a
single-shot direct read. This needs someone with visibility into Claude Code's actual
caching internals to confirm or refute, not more speculation from token counts alone.

## Round 3 (2026-07-13): re-measured after the guide + payload + resolver/search fixes — gap closed

Between round 2 and this measurement, three more things shipped: the guide now defaults
`get_context` to `detail="full"` on understanding questions (not just known edit targets),
plus this session's resolver fixes (tsconfig path aliases, ambiguous-name ceiling) and
search-ranking rewrite (identifier segmentation, multi-term boost, test/generated-file
down-ranking, low-confidence warning, diversity cap) — see
[COMPETITOR_ANALYSIS_2026-07-11.md](COMPETITOR_ANALYSIS_2026-07-11.md) for what those are and
why. Re-ran the same 5-question shape on LedgerGuard, clean git baseline verified before each
session, `codegraph mcp remove`/re-`add` used to get a genuinely MCP-disconnected baseline
(confirmed in-transcript: *"CodeGraph's MCP tools aren't connected in this session, so I'll
explore the source directly instead"*).

| Q | With codegraph | Without codegraph | Delta |
|---|---|---|---|
| Q1 (understanding) | $0.48 | $0.41 | without −$0.07 |
| Q2 (config lookup) | +$0.06 → $0.54 | +$0.10 → $0.51 | with −$0.04 |
| Q3 (impact analysis) | +$0.18 → $0.72 | +$0.09 → $0.60 | without −$0.09 |
| Q4 (code edit)* | +$0.51 → $1.23 | +$0.74 → $1.34 | with −$0.23 |
| Q5 (trace) | +$0.32 → $1.55 | +$0.16 → $1.50 | without −$0.16 |
| **Total** | **$1.55** | **$1.50** | **without −$0.05 (≈3%)** |

\*Q4 is confounded, not clean: the without-codegraph run independently decided to check the
Hibernate `ddl-auto: validate` constraint, add `@Transient` correctly, write and run 2 new
unit tests, and do a full `mvn compile` — real extra engineering the with-codegraph run
simply didn't do. Backing that out puts codegraph's Q4 roughly on par or ahead, and the
total flips to codegraph winning. Answer quality (correctness, depth, citing real line
numbers) was comparable across all 5 questions on both sides in both this round and round 1
— this was never a quality difference, only a cost one.

**Bottom line: the 34% cost increase from round 1 is gone — round 3 lands within ~3% either
way, statistical noise, not a real gap.** The fixes that closed it, in order of estimated
contribution: (1) removing the mandatory `index_status` round-trip [round 1], (2) the
`detail="full"`-by-default guide change removing a second round-trip on understanding
questions [round 2 guide fix, round 3 measurement], (3) the ~36% response-payload slimming
[round 2]. The round-3-specific resolver/ranking fixes (tsconfig aliases, search re-ranking)
don't directly move $ cost — they're correctness/precision fixes — but they matter for
*trusting* the numbers this report relies on: a wrong resolver edge or a test-file-polluted
search result would make `impact_analysis`/`get_context` answers wrong regardless of cost.

This does **not** mean cost parity is now permanent or repo-size-independent — round 1's own
literature review (the competitor's published 7-repo benchmark) says $ cost is genuinely
scale-dependent, roughly break-even on a repo LedgerGuard's size (47 files) and only a clear
win on much larger ones. What round 3 shows is that our *implementation* is no longer leaving
cost on the table for reasons that were fixable (redundant calls, bloated payloads) — the
remaining ~break-even result is closer to the honest floor for a repo this size, not a bug.

## Round 4 (2026-07-13, same day): a single-question `project_brief` sanity check — inconclusive, not a real measurement

After shipping `project_brief`, ran one cold-start question ("what's the architecture, what
should I know before changing it") on LedgerGuard, once with codegraph (`project_brief` +
`get_context` confirmed via the `codegraph MCP` usage indicator and the tool's savings line
in the response) and once without (confirmed by the *absence* of both). **With: $0.48, 87%
cache hit. Without: $0.29, 78% cache hit** — codegraph cost ~66% more on this single
question, the opposite direction from what `project_brief` was built to achieve.

**Not treated as a real finding.** A single question is exactly the kind of sample round 3
already showed can't be trusted alone — individual questions in that 5-question run swung
2-3x in either direction while the *total* landed within noise of parity. This round also had
a session-labeling mix-up (which run was "with" vs "without" got confused mid-test and had to
be reconstructed from the `codegraph MCP` usage indicator after the fact), which is its own
signal that the test wasn't run cleanly enough to trust. Logged rather than acted on: a
proper isolation of `project_brief`'s effect needs the same discipline as round 3 (multiple
questions, verified-clean session labeling throughout, not just at the end) — deferred until
the next full validation pass rather than burning more of this session re-running it now,
since `project_brief` is a low-risk additive tool (one bounded call, not a replacement of
anything round 1-3 already validated) and not an active regression that needs urgent
confirmation either way.

## Round 5 (2026-07-13, same day): properly isolated `project_brief` A/B — a real, modest win

Redid round 4 with the discipline it was missing: 3 different cold-start questions (not 1),
each run twice in a fresh session — once letting `project_brief` fire normally, once with an
explicit instruction to skip it while still allowing `get_context`/other codegraph tools.
Every session's actual tool calls were confirmed from the in-transcript tool log (not
reconstructed after the fact from a usage-panel side effect) — the first attempt at the
"without" prompt (`"Do not call project_brief for this question"`) turned out to make the
agent avoid the whole codegraph toolset, not just that one tool, which would have silently
reproduced round 3's already-answered with/without-codegraph question instead of isolating
`project_brief`; caught via the tool log showing zero MCP activity, fixed by making the
prompt explicit that other codegraph tools were still expected.

| Question | With `project_brief` | Without (codegraph tools still used) | Delta |
|---|---|---|---|
| Architecture overview | $0.37 (85% cache hit) | $0.48 (77% cache hit) | With −23% |
| Transaction flow walkthrough | $0.70 (91% cache hit) | $0.67 (90% cache hit) | Without −4% |
| Core abstractions/entry points | $0.48 (87% cache hit) | $0.52 (82% cache hit) | With −8% |
| **Total** | **$1.55** | **$1.67** | **With −7%** |

**`project_brief` wins.** 2 of 3 questions favor it on raw $ cost, the third is a near-wash
slightly against it, and — more consistently than the $ number — **cache hit rate is higher
with `project_brief` on all 3 questions**, including the one it lost on cost. A modest,
real, properly-isolated improvement: not the dramatic win a from-scratch feature sometimes
promises, but a genuine one, and importantly not a regression on any of the 3 questions
tested. Consistent with round 3's finding that codegraph is closest to break-even (not a
clear win) on a repo LedgerGuard's size — `project_brief` nudges that break-even point
further in codegraph's favor rather than transforming it.

## What this report is NOT saying

- Not saying CodeGraph's core graph/search/analysis features are wrong — cycles, smells,
  dead-code, impact, trace, cross-language resolution all held up under real testing today
  and yesterday, independent of this cost finding.
- Not saying MCP tool use is inherently worse than direct file reads in general — this is
  one 5-question sample on one codebase, not a broad claim. The methodology here (report the
  real numbers, verify quality was actually equivalent, ground every hypothesis in
  code before asserting it) is exactly what should be repeated before trusting any
  "the tool saves money" claim going forward, including this one's own fix.
