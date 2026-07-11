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
per-session re-exploration it can eliminate across dozens of sessions is its best. The
biggest unbuilt lever from this analysis: a ~500-token pre-computed `project_brief`
(architecture, layers, key entities, hot paths) served once at session start, replacing
the multi-call re-orientation every fresh session currently performs — that's the
persistent-index advantage made directly billable. Needs its own design pass; logged as
the top candidate for the next improvement round, ahead of everything below.

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

## What this report is NOT saying

- Not saying CodeGraph's core graph/search/analysis features are wrong — cycles, smells,
  dead-code, impact, trace, cross-language resolution all held up under real testing today
  and yesterday, independent of this cost finding.
- Not saying MCP tool use is inherently worse than direct file reads in general — this is
  one 5-question sample on one codebase, not a broad claim. The methodology here (report the
  real numbers, verify quality was actually equivalent, ground every hypothesis in
  code before asserting it) is exactly what should be repeated before trusting any
  "the tool saves money" claim going forward, including this one's own fix.
